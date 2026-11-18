"""计划执行、冻结目标与逐组实际量；与姿态质量分别统计。"""
import json,re,datetime as dt
from flask import request,jsonify
from flask_login import current_user
from sqlalchemy import select,update,delete
from . import db_schema as s
from .auth import APIError,body
from .database import ConflictError,idempotent,json_text
from .records import integer,uuid_id
from .training_plans import date,load_value,invalid
from .catalogue import get_action

COMMANDS={'ready':['start','void'],'running':['pause','complete','end_early','void'],'paused':['resume','complete','end_early','void'],'completed':['reopen','void'],'ended_early':['reopen','void'],'voided':[]}
class TrainingWorkouts:
    def __init__(self,app):self.app=app;self.plans=app.extensions['training_plans'];self.db=self.plans.db;self.clock=self.plans.clock
    def row(self,c,uid,auth,wid):
        self.plans.actor(c,uid,auth);row=c.execute(select(s.training_workouts).where(s.training_workouts.c.id==uuid_id(wid),s.training_workouts.c.user_id==uid)).mappings().first()
        if not row:raise APIError('NOT_FOUND','训练执行不存在',404)
        return dict(row)
    def completion(self,items,sets,links):
        invalid_sets={(x['item_id'],a['set_index']) for x in links if not x['valid'] for a in json.loads(x['allocations_json'])}
        result=[];total_done=0;met_items=0
        for item in items:
            rows=[];target_sets=item['target_sets']
            for index in range(1,target_sets+1):
                saved=next((x for x in sets if x['item_id']==item['id'] and x['set_index']==index),None)
                quantity=item['target_reps'] if item['target_reps'] is not None else item['target_duration_ms'];actual=(saved['actual_reps'] if item['target_reps'] is not None else saved['actual_duration_ms']) if saved else None
                invalid_source=bool(saved and saved['source']=='record' and (item['id'],index) in invalid_sets)
                met=bool(saved and saved['status']=='done' and not invalid_source and actual is not None and actual>=quantity)
                if met:total_done+=1
                rows.append({'item_id':item['id'],'set_index':index,'actual_reps':saved['actual_reps'] if saved else None,'actual_duration_ms':saved['actual_duration_ms'] if saved else None,
                    'load':json.loads(saved['load_json']) if saved and saved['load_json'] else None,'status':'needs_reconciliation' if invalid_source else saved['status'] if saved else 'pending',
                    'target_met':met,'partial':bool(saved and saved['status']=='done' and not met),'source':saved['source'] if saved else None,'saved_at_ms':saved['saved_at_ms'] if saved else None})
            met=all(x['target_met'] for x in rows)
            if met:met_items+=1
            result.append({'item_id':item['id'],'action_id':item['action_id'],'action_name':(get_action(item['action_id']) or {}).get('name',item['action_id']),'variant':item.get('variant'),'focus_task_id':item.get('focus_task_id'),'target':{'unit':(get_action(item['action_id']) or {}).get('unit'),'sets':target_sets,'reps':item['target_reps'],'duration_ms':item['target_duration_ms'],'rest_seconds':item['rest_seconds'],'load':item.get('load')},'sets':rows,'completion':{'target_met':met}})
        return {'target_items':len(items),'completed_items':met_items,'done_sets':total_done,'target_sets':sum(x['target_sets'] for x in items)},result
    def snapshot(self,c,row):
        target=json.loads(row['target_snapshot_json']);items=target.get('items',[])
        sets=[dict(x) for x in c.execute(select(s.training_workout_sets).where(s.training_workout_sets.c.workout_id==row['id'])).mappings()]
        links=[dict(x) for x in c.execute(select(s.training_record_allocations).where(s.training_record_allocations.c.workout_id==row['id'])).mappings()]
        completion,results=self.completion(items,sets,links)
        source={x['source'] for x in sets};source='mixed' if len(source)>1 else next(iter(source),'manual')
        elapsed=row['elapsed_active_ms']+(max(0,self.clock()-row['active_since_ms']) if row['active_since_ms'] is not None and row['status']=='running' else 0)
        return {k:row[k] for k in ('id','user_id','state_version','status','plan_session_id','plan_revision_id','actual_started_at_ms','actual_ended_at_ms')}|{'target_snapshot':target,'items':results,'completion':completion,'note':json.loads(row['completion_json']).get('user_note',''),'source':source,'record_links':[{**x,'allocations':json.loads(x['allocations_json'])} for x in links],'allowed_commands':COMMANDS[row['status']],'elapsed_active_ms':elapsed}
    def get(self,uid,auth,wid):
        with self.db.transaction(False) as c:result=self.snapshot(c,self.row(c,uid,auth,wid))
        from .learning_guides import training_picture
        for item in result['items']:item['thumbnail']=training_picture(item['action_id'])
        return result
    @staticmethod
    def stored_completion(snapshot):
        value=dict(snapshot['completion'])
        if snapshot.get('note'):value['user_note']=snapshot['note']
        return json_text(value)
    def save_note(self,uid,auth,wid,data,key):
        key=uuid_id(key);version=integer(data.get('expected_version'),'expected_version');note=data.get('note')
        if not isinstance(note,str) or len(note)>500 or re.search(r'[<>\x00-\x08\x0b\x0c\x0e-\x1f]',note):
            raise APIError('VALIDATION_FAILED','备注请填写500字以内的普通文字',422)
        with self.db.transaction() as c:
            row=self.row(c,uid,auth,wid)
            def write():
                if row['state_version']!=version:raise ConflictError('训练已在其他页面更新，本地备注保留')
                if row['status']=='voided':raise APIError('INVALID_STATE','已作废执行仅可查看',409)
                snapshot=self.snapshot(c,row);snapshot['note']=note.strip()
                c.execute(update(s.training_workouts).where(s.training_workouts.c.id==wid,s.training_workouts.c.user_id==uid).values(completion_json=self.stored_completion(snapshot),state_version=version+1))
                return {'id':wid,'version':version+1}
            idempotent(c,uid,key,data,'training.workout.note',write,wid)
        return self.get(uid,auth,wid)
    def create(self,uid,auth,data,key):
        key=uuid_id(key);sid=uuid_id(data.get('plan_session_id'));version=integer(data.get('session_expected_version'),'session_expected_version')
        with self.db.transaction() as c:
            self.plans.actor(c,uid,auth)
            def write():
                session=c.execute(select(s.training_plan_sessions).where(s.training_plan_sessions.c.id==sid,s.training_plan_sessions.c.user_id==uid)).mappings().first()
                if not session:raise APIError('NOT_FOUND','训练安排不存在',404)
                existing=c.execute(select(s.training_workouts).where(s.training_workouts.c.plan_session_id==sid,s.training_workouts.c.user_id==uid)).mappings().first()
                if existing:return {'id':existing['id'],'version':existing['state_version'],'created':False}
                plan=self.plans.row(c,uid,auth,session['plan_id'])
                if session['schedule_version']!=version:raise ConflictError('安排已更新')
                if plan['status']!='active' or session['status']!='planned' or date(session['scheduled_date'])!=self.plans.today():raise APIError('WORKOUT_NOT_AVAILABLE','仅当前启用计划的今日未跳过安排可开始',409)
                # 暂停区间内历史安排由progress排除，今日执行只在恢复后主动开始。
                items=c.execute(select(s.training_plan_items).where(s.training_plan_items.c.session_id==sid).order_by(s.training_plan_items.c.position)).mappings().all()
                target={'plan_id':plan['id'],'plan_title':plan['title'],'session_title':session['title'],'date':session['scheduled_date'],'items':[{'id':x['id'],'action_id':x['action_id'],'variant':x['variant'],'target_sets':x['target_sets'],'target_reps':x['target_reps'],'target_duration_ms':x['target_duration_ms'],'rest_seconds':x['rest_seconds'],'load':json.loads(x['optional_load_json']) if x['optional_load_json'] else None,'focus_task_id':x['focus_task_id']} for x in items]}
                wid=s.new_id();completion,_=self.completion(target['items'],[],[])
                c.execute(s.training_workouts.insert().values(id=wid,user_id=uid,plan_session_id=sid,plan_revision_id=session['revision_id'],target_snapshot_json=json_text(target),completion_json=json_text(completion)))
                return {'id':wid,'version':0,'created':True}
            result=idempotent(c,uid,key,data,'training.workout.create',write,sid)
        return self.get(uid,auth,result['id'])|{'created':result.get('created',False)}
    def command(self,uid,auth,wid,data,key):
        key=uuid_id(key);version=integer(data.get('expected_version'),'expected_version');kind=data.get('type')
        if not isinstance(kind,str) or kind not in {'start','pause','resume','complete','end_early','reopen','void'}:invalid('type')
        if data.get('reason') is not None:
            from .training_plans import text
            text(data['reason'],'reason',200,0)
        with self.db.transaction() as c:
            row=self.row(c,uid,auth,wid)
            def write():
                if row['state_version']!=version:raise ConflictError('执行已更新')
                if kind not in COMMANDS[row['status']]:raise APIError('INVALID_STATE','请刷新训练执行状态后重试',409)
                if kind in {'complete','void'} and data.get('confirmed') is not True:invalid('confirmed','请明确确认完成或作废')
                now=self.clock();values={'state_version':version+1}
                if kind in {'start','resume'}:
                    plan=c.execute(select(s.training_plans).join(s.training_plan_sessions,s.training_plan_sessions.c.plan_id==s.training_plans.c.id).where(s.training_plan_sessions.c.id==row['plan_session_id'])).mappings().one()
                    if plan['status']!='active':raise APIError('PLAN_PAUSED','请先启用训练计划',409)
                    schedule=c.execute(select(s.training_plan_sessions).where(s.training_plan_sessions.c.id==row['plan_session_id'],s.training_plan_sessions.c.user_id==uid)).mappings().one()
                    if kind=='start' and (schedule['status']!='planned' or date(schedule['scheduled_date'])!=self.plans.today()):raise APIError('WORKOUT_NOT_AVAILABLE','安排已改期或跳过，请重新核对今日训练',409)
                    other=c.execute(select(s.training_workouts.c.id).where(s.training_workouts.c.user_id==uid,s.training_workouts.c.status=='running',s.training_workouts.c.id!=wid)).first()
                    if other:raise APIError('WORKOUT_RUNNING','请先暂停另一场训练',409,{'workout_id':other[0]})
                    values.update(status='running',active_since_ms=now,actual_ended_at_ms=None)
                    if kind=='start':values['actual_started_at_ms']=now
                elif kind=='reopen':values.update(status='paused',actual_ended_at_ms=None)
                else:
                    values.update(status={'pause':'paused','complete':'completed','end_early':'ended_early','void':'voided'}[kind],active_since_ms=None)
                    if row['active_since_ms'] is not None:values['elapsed_active_ms']=row['elapsed_active_ms']+max(0,now-row['active_since_ms'])
                    if kind in {'complete','end_early','void'}:values['actual_ended_at_ms']=now
                snapshot=self.snapshot(c,row)
                if kind=='complete' and (not snapshot['items'] or snapshot['completion']['completed_items']!=snapshot['completion']['target_items']):raise APIError('WORKOUT_TARGET_NOT_MET','可继续完成目标，或选择提前结束',422)
                values['completion_json']=self.stored_completion(snapshot)
                c.execute(update(s.training_workouts).where(s.training_workouts.c.id==wid,s.training_workouts.c.user_id==uid).values(**values))
                return {'id':wid,'version':version+1}
            idempotent(c,uid,key,data,'training.workout.'+kind,write,wid)
        return self.get(uid,auth,wid)
    def set(self,uid,auth,wid,iid,index,data,key,remove=False):
        iid=uuid_id(iid);index=integer(index,'set_index',1,10);version=integer(data.get('expected_version'),'expected_version');key=uuid_id(key)
        with self.db.transaction() as c:
            row=self.row(c,uid,auth,wid)
            def write():
                if row['state_version']!=version:raise ConflictError('执行已更新，请重新读取')
                if row['status'] not in {'running','paused'}:raise APIError('INVALID_STATE','请先开始训练，已结束则先重新打开',409)
                item=next((x for x in json.loads(row['target_snapshot_json'])['items'] if x['id']==iid),None)
                if not item or index>item['target_sets']:invalid('set_index')
                old=c.execute(select(s.training_workout_sets).where(s.training_workout_sets.c.workout_id==wid,s.training_workout_sets.c.item_id==iid,s.training_workout_sets.c.set_index==index)).mappings().first()
                if old and old['source']=='record':
                    links=c.execute(select(s.training_record_allocations).where(s.training_record_allocations.c.workout_id==wid,s.training_record_allocations.c.item_id==iid)).mappings().all()
                    active=any(x['valid'] and index in [a['set_index'] for a in json.loads(x['allocations_json'])] for x in links)
                    if active:raise APIError('RECORD_LINK_ACTIVE','请先解除分析记录分配再改为手动',409)
                if remove:
                    if old:c.execute(delete(s.training_workout_sets).where(s.training_workout_sets.c.id==old['id']))
                else:
                    if data.get('source')!='manual' or data.get('status') not in {'done','skipped'}:invalid('source')
                    reps=data.get('actual_reps');duration=data.get('actual_duration_ms');skipped=data['status']=='skipped'
                    if skipped:
                        if reps is not None or duration is not None:invalid('actual','跳过组实际量必须为空')
                    elif item['target_reps'] is not None:
                        integer(reps,'actual_reps',1,100)
                        if duration is not None:invalid('actual_duration_ms')
                    else:
                        integer(duration,'actual_duration_ms',1000,600000)
                        if reps is not None:invalid('actual_reps')
                    load=load_value(data.get('load'))
                    values={'actual_reps':reps,'actual_duration_ms':duration,'load_json':json_text(load) if load else None,'status':data['status'],'source':'manual','saved_at_ms':self.clock()}
                    if old:c.execute(update(s.training_workout_sets).where(s.training_workout_sets.c.id==old['id']).values(**values))
                    else:c.execute(s.training_workout_sets.insert().values(id=s.new_id(),user_id=uid,workout_id=wid,item_id=iid,set_index=index,**values))
                c.execute(update(s.training_workouts).where(s.training_workouts.c.id==wid).values(state_version=version+1))
                snapshot=self.snapshot(c,{**row,'state_version':version+1})
                c.execute(update(s.training_workouts).where(s.training_workouts.c.id==wid).values(completion_json=self.stored_completion(snapshot)))
                return {'id':wid,'version':version+1}
            idempotent(c,uid,key,data,'training.set.delete' if remove else 'training.set.save',write,wid+':'+iid+':'+str(index))
        result=self.get(uid,auth,wid);result['updated_set']=next(x for x in result['items'] if x['item_id']==iid)['sets'][index-1];return result
    def list(self,uid,auth,args):
        if set(args)-{'date_from','date_to','cursor','limit'}:invalid('query')
        start=date(args.get('date_from') or (self.plans.today()-dt.timedelta(days=30)).isoformat());end=date(args.get('date_to') or self.plans.today().isoformat())
        if not 0<=(end-start).days<=90:invalid('date_range')
        try:limit=int(args.get('limit','20'))
        except ValueError:invalid('limit')
        integer(limit,'limit',1,50);offset=0
        if args.get('cursor'):
            try:
                payload=self.plans.signer.loads(args['cursor'])
                if payload['uid']!=uid or payload['kind']!='workouts' or payload['range']!=[start.isoformat(),end.isoformat()]:raise ValueError()
                offset=integer(payload['offset'],'offset')
            except Exception:invalid('cursor')
        with self.db.transaction(False) as c:
            self.plans.actor(c,uid,auth);rows=c.execute(select(s.training_workouts).join(s.training_plan_sessions,s.training_plan_sessions.c.id==s.training_workouts.c.plan_session_id).where(s.training_workouts.c.user_id==uid,s.training_plan_sessions.c.scheduled_date>=start.isoformat(),s.training_plan_sessions.c.scheduled_date<=end.isoformat()).order_by(s.training_workouts.c.created_at_ms.desc(),s.training_workouts.c.id).offset(offset).limit(limit+1)).mappings().all()
            result=[self.snapshot(c,dict(x)) for x in rows[:limit]]
        more=len(rows)>limit;return {'items':result,'has_more':more,'next_cursor':self.plans.signer.dumps({'uid':uid,'kind':'workouts','range':[start.isoformat(),end.isoformat()],'offset':offset+limit}) if more else None}

def register_workouts(app):
    service=TrainingWorkouts(app);app.extensions['training_workouts']=service
    @app.route('/api/v1/training/workouts',methods=['GET','POST'])
    def workouts():
        if request.method=='GET':return jsonify(data=service.list(current_user.user_id,current_user.id,dict(request.args)))
        result=service.create(current_user.user_id,current_user.id,body({'plan_session_id','session_expected_version'}),request.headers.get('Idempotency-Key'))
        return jsonify(data=result),201 if result['created'] else 200
    @app.get('/api/v1/training/workouts/<wid>')
    def workout_detail(wid):return jsonify(data=service.get(current_user.user_id,current_user.id,wid))
    @app.post('/api/v1/training/workouts/<wid>/commands')
    def workout_command(wid):return jsonify(data=service.command(current_user.user_id,current_user.id,wid,body({'expected_version','type','confirmed','reason'}),request.headers.get('Idempotency-Key')))
    @app.put('/api/v1/training/workouts/<wid>/note')
    def workout_note(wid):
        return jsonify(data=service.save_note(current_user.user_id,current_user.id,wid,body({'expected_version','note'}),request.headers.get('Idempotency-Key')))
    @app.route('/api/v1/training/workouts/<wid>/items/<iid>/sets/<int:index>',methods=['PUT','DELETE'])
    def workout_set(wid,iid,index):
        fields={'expected_version'} if request.method=='DELETE' else {'expected_version','actual_reps','actual_duration_ms','load','status','source'}
        return jsonify(data=service.set(current_user.user_id,current_user.id,wid,iid,index,body(fields),request.headers.get('Idempotency-Key'),request.method=='DELETE'))
