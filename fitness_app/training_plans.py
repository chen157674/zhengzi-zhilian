"""本人结构化计划、修订和安排；所有写命令事务化、版本化且幂等。"""
import json,datetime as dt
from zoneinfo import ZoneInfo
from decimal import Decimal,InvalidOperation
from flask import jsonify,request
from flask_login import current_user
from sqlalchemy import select,update,delete
from itsdangerous import URLSafeSerializer,BadSignature
from . import db_schema as s
from .auth import APIError,body
from .records import uuid_id,integer
from .database import ConflictError,idempotent,json_text
from .catalogue import get_action
from .training_info import EXPERIENCE,GOALS,EQUIPMENT,LOAD_BASES
ZONE=ZoneInfo('Asia/Shanghai')

def invalid(field,message='请检查训练计划字段'):
    raise APIError('VALIDATION_FAILED',message,422,{field:message})
def text(value,field,maximum,minimum=1):
    if not isinstance(value,str) or not minimum<=len(value.strip())<=maximum:invalid(field)
    return value.strip()
def date(value,field='date'):
    if not isinstance(value,str):invalid(field)
    try:result=dt.date.fromisoformat(value)
    except ValueError:invalid(field)
    if result.isoformat()!=value:invalid(field)
    return result

def load_value(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)!={'value','unit','basis'} or not isinstance(value['unit'],str) or value['unit'] not in {'kg','lb'} or not isinstance(value['basis'],str) or value['basis'] not in LOAD_BASES:invalid('load')
    if isinstance(value['value'],bool) or not isinstance(value['value'],(int,float,str)):invalid('load')
    try:n=Decimal(str(value['value']))
    except InvalidOperation:invalid('load')
    if not n.is_finite() or not 0<=n<=500 or n.as_tuple().exponent < -2:invalid('load')
    return {'value':float(n),'unit':value['unit'],'basis':value['basis']}

def clean_constraints(data):
    if not isinstance(data,dict) or set(data)-{'experience','training_weekdays','minutes_per_session','equipment','preferences'}:invalid('constraints')
    result={}
    if data.get('experience') is not None:
        if not isinstance(data['experience'],str) or data['experience'] not in EXPERIENCE:invalid('constraints.experience')
        result['experience']=data['experience']
    days=data.get('training_weekdays',[])
    if not isinstance(days,list) or not 1<=len(days)<=7 or any(type(x) is not int or not 1<=x<=7 for x in days) or len(days)!=len(set(days)):invalid('constraints.training_weekdays')
    result['training_weekdays']=sorted(days)
    result['minutes_per_session']=integer(data.get('minutes_per_session'),'constraints.minutes_per_session',10,180)
    equipment=data.get('equipment',[])
    if not isinstance(equipment,list) or any(not isinstance(x,str) or x not in EQUIPMENT for x in equipment) or len(set(equipment))!=len(equipment):invalid('constraints.equipment')
    result['equipment']=equipment
    if 'preferences' in data:result['preferences']=text(data['preferences'],'constraints.preferences',300,0)
    return result

def clean_draft(data):
    allowed={'title','goal','start_date','weeks','timezone','constraints','sessions'}
    if not isinstance(data,dict) or set(data)!=allowed:invalid('plan','计划字段缺失或含未知字段')
    title=text(data['title'],'title',60);goal=data['goal']
    if not isinstance(goal,str) or goal not in GOALS|{'general'}:invalid('goal')
    start=date(data['start_date'],'start_date');weeks=integer(data['weeks'],'weeks',1,8)
    if start.weekday()!=0:invalid('start_date','开始周须为周一')
    if data['timezone']!='Asia/Shanghai':invalid('timezone')
    constraints=clean_constraints(data['constraints']);sessions=data['sessions']
    if not isinstance(sessions,list) or len(sessions)>56:invalid('sessions')
    seen=set();clients=set();clean=[]
    def client(v,field):
        v=text(v,field,80)
        if v in clients:invalid(field,'本次草稿的client_id不可重复')
        clients.add(v);return v
    for row in sessions:
        if not isinstance(row,dict) or set(row)!={'client_id','date','day_kind','title','estimated_minutes','items'}:invalid('sessions')
        day=date(row['date'])
        if day in seen or not start<=day<start+dt.timedelta(weeks=weeks):invalid('sessions.date')
        seen.add(day);kind=row['day_kind']
        if not isinstance(kind,str) or kind not in {'training','rest'}:invalid('sessions.day_kind')
        items=row['items']
        if not isinstance(items,list) or len(items)>12 or (kind=='training' and not items) or (kind=='rest' and items):invalid('sessions.items')
        entry={'client_id':client(row['client_id'],'session.client_id'),'date':day.isoformat(),'day_kind':kind,'title':text(row['title'],'session.title',60),'estimated_minutes':integer(row['estimated_minutes'],'estimated_minutes',0,180),'items':[]}
        for item in items:
            if not isinstance(item,dict) or set(item)!={'client_id','action_id','variant','target_sets','target_reps','target_duration_ms','rest_seconds','load','focus_task_id'}:invalid('items')
            action=get_action(item['action_id']) if isinstance(item['action_id'],str) else None
            if not action:invalid('action_id','动作不在当前目录')
            reps=item['target_reps'];duration=item['target_duration_ms']
            if (reps is None)==(duration is None):invalid('target','次数与保持时间只能选一种')
            if reps is not None:integer(reps,'target_reps',1,100)
            if duration is not None:integer(duration,'target_duration_ms',1000,600000)
            if (action['unit']=='seconds')!=(duration is not None):invalid('target','目标单位须匹配动作')
            variant=item['variant']
            if variant is not None:variant=text(variant,'variant',300,0)
            focus=item['focus_task_id']
            if focus is not None:focus=uuid_id(focus)
            entry['items'].append({'client_id':client(item['client_id'],'item.client_id'),'action_id':action['action_id'],'variant':variant,'target_sets':integer(item['target_sets'],'target_sets',1,10),'target_reps':reps,'target_duration_ms':duration,'rest_seconds':integer(item['rest_seconds'],'rest_seconds',0,600),'load':load_value(item['load']),'focus_task_id':focus})
        clean.append(entry)
    return {'title':title,'goal':goal,'start_date':start.isoformat(),'weeks':weeks,'timezone':'Asia/Shanghai','constraints':constraints,'sessions':sorted(clean,key=lambda x:x['date'])}

class TrainingPlans:
    def __init__(self,app):
        self.app=app;self.db=app.extensions['database'];self.sessions=app.extensions['session_store'];self.clock=app.config.get('CLOCK') or s.now_ms
        self.signer=URLSafeSerializer(app.config['SECRET_KEY'],salt='training-list-1')
    def today(self):return dt.datetime.fromtimestamp(self.clock()/1000,ZONE).date()
    def actor(self,c,uid,auth):self.sessions._actor(c,uid,auth)
    def row(self,c,uid,auth,pid):
        self.actor(c,uid,auth);row=c.execute(select(s.training_plans).where(s.training_plans.c.id==uuid_id(pid),s.training_plans.c.user_id==uid)).mappings().first()
        if not row:raise APIError('NOT_FOUND','训练计划不存在',404)
        return dict(row)
    def session_row(self,c,uid,auth,pid,sid):
        plan=self.row(c,uid,auth,pid);row=c.execute(select(s.training_plan_sessions).where(s.training_plan_sessions.c.id==uuid_id(sid),s.training_plan_sessions.c.plan_id==pid,s.training_plan_sessions.c.user_id==uid)).mappings().first()
        if not row:raise APIError('NOT_FOUND','训练安排不存在',404)
        return plan,dict(row)
    def summary(self,row):
        return {k:row[k] for k in ('id','title','goal','status','state_version','start_date','weeks','timezone','current_revision_id','updated_at_ms','activated_at_ms')}|{'end_date':(date(row['start_date'])+dt.timedelta(weeks=row['weeks'],days=-1)).isoformat(),'links':{'view':'/training/plan?plan_id='+row['id'],'edit':'/training/plans/'+row['id']+'/edit','activate':'/training/plans/'+row['id']+'/activate'}}
    def snapshot(self,c,uid,auth,pid):
        row=self.row(c,uid,auth,pid);revision=c.execute(select(s.training_plan_revisions).where(s.training_plan_revisions.c.id==row['current_revision_id'],s.training_plan_revisions.c.user_id==uid)).mappings().first()
        sessions=c.execute(select(s.training_plan_sessions).where(s.training_plan_sessions.c.plan_id==pid,s.training_plan_sessions.c.user_id==uid).order_by(s.training_plan_sessions.c.scheduled_date)).mappings().all()
        items=c.execute(select(s.training_plan_items).where(s.training_plan_items.c.user_id==uid,s.training_plan_items.c.session_id.in_([x['id'] for x in sessions]))).mappings().all() if sessions else []
        workout_states={x['plan_session_id']:x['status'] for x in c.execute(select(s.training_workouts.c.plan_session_id,s.training_workouts.c.status).where(s.training_workouts.c.user_id==uid,s.training_workouts.c.plan_session_id.in_([x['id'] for x in sessions]))).mappings()} if sessions else {}
        def item(x):return {'id':x['id'],'item_id':x['id'],'client_id':None,'action_id':x['action_id'],'variant':x['variant'],'target_sets':x['target_sets'],'target_reps':x['target_reps'],'target_duration_ms':x['target_duration_ms'],'rest_seconds':x['rest_seconds'],'load':json.loads(x['optional_load_json']) if x['optional_load_json'] else None,'focus_task_id':x['focus_task_id'],'position':x['position']}
        return {**self.summary(row),'revision':{'id':revision['id'],'revision_number':revision['revision_number'],'constraints':json.loads(revision['constraints_json']),'sessions':json.loads(revision['sessions_json']),'source':revision['source'],'knowledge_refs':json.loads(revision['knowledge_refs_json']),'template_version':revision['template_version']} if revision else None,
                'sessions':[{**dict(x),'workout_status':workout_states.get(x['id']),'items':[item(i) for i in sorted(items,key=lambda i:i['position']) if i['session_id']==x['id']]} for x in sessions],
                'client_id_map':{entry['client_id']:session['id'] for entry in (json.loads(revision['sessions_json']) if revision else []) for session in sessions if session['revision_id']==revision['id'] and session['scheduled_date']==entry['date']}|{entry_item['client_id']:saved['id'] for entry in (json.loads(revision['sessions_json']) if revision else []) for session in sessions if session['revision_id']==revision['id'] and session['scheduled_date']==entry['date'] for position,entry_item in enumerate(entry['items']) for saved in items if saved['session_id']==session['id'] and saved['position']==position}}
    def get(self,uid,auth,pid):
        with self.db.transaction(False) as c:return self.snapshot(c,uid,auth,pid)
    def current(self,uid,auth):
        with self.db.transaction(False) as c:
            self.actor(c,uid,auth);row=c.execute(select(s.training_plans).where(s.training_plans.c.user_id==uid,s.training_plans.c.status.in_(['active','paused']))).mappings().first()
        return {'plan':self.summary(row) if row else None}
    def list(self,uid,auth,args):
        if set(args)-{'status','cursor','limit'}:invalid('query')
        status=args.get('status') or None
        if status not in {None,'draft','active','paused','archived'}:invalid('status')
        try:limit=int(args.get('limit','20'))
        except ValueError:invalid('limit')
        integer(limit,'limit',1,50);offset=0
        if args.get('cursor'):
            try:
                cursor=self.signer.loads(args['cursor'])
                if cursor['uid']!=uid or cursor['status']!=status:raise ValueError()
                offset=integer(cursor['offset'],'offset')
            except (BadSignature,ValueError,KeyError,TypeError):invalid('cursor')
        with self.db.transaction(False) as c:
            self.actor(c,uid,auth);query=select(s.training_plans).where(s.training_plans.c.user_id==uid)
            if status:query=query.where(s.training_plans.c.status==status)
            rows=c.execute(query.order_by(s.training_plans.c.updated_at_ms.desc(),s.training_plans.c.id).offset(offset).limit(limit+1)).mappings().all()
        more=len(rows)>limit
        return {'items':[self.summary(x) for x in rows[:limit]],'has_more':more,'next_cursor':self.signer.dumps({'uid':uid,'status':status,'offset':offset+limit}) if more else None}
    def _revision(self,c,uid,pid,draft,number,source='manual',refs=None):
        for session in draft['sessions']:
            for item in session['items']:
                if item['focus_task_id']:
                    task=c.execute(select(s.coach_saved_retests).where(s.coach_saved_retests.c.id==item['focus_task_id'],s.coach_saved_retests.c.user_id==uid,s.coach_saved_retests.c.expires_at_ms>self.clock())).mappings().first()
                    if not task:raise APIError('NOT_FOUND','计划关注任务不存在或已过期',404)
                    origin=json.loads(task['origin_json']);record=self.sessions._session(c,uid,origin['session_id'])
                    if record['action_id']!=item['action_id'] or record['state_version']!=origin['expected_version']:raise APIError('PRACTICE_ACTION_MISMATCH','关注任务不匹配当前动作或来源版本',422)
        rid=s.new_id();c.execute(s.training_plan_revisions.insert().values(id=rid,user_id=uid,plan_id=pid,revision_number=number,constraints_json=json_text(draft['constraints']),sessions_json=json_text(draft['sessions'],list),knowledge_refs_json=json_text(refs or [],list),source=source,template_version='manual-1'))
        return rid
    def _materialize(self,c,uid,pid,rid,rows,effective=None):
        mapping={}
        for entry in rows:
            if entry['day_kind']=='rest' or effective and entry['date']<effective:continue
            existing=c.execute(select(s.training_plan_sessions).where(s.training_plan_sessions.c.plan_id==pid,s.training_plan_sessions.c.scheduled_date==entry['date'],s.training_plan_sessions.c.status!='cancelled')).mappings().first()
            if existing:continue
            sid=s.new_id();mapping[entry['client_id']]=sid
            c.execute(s.training_plan_sessions.insert().values(id=sid,user_id=uid,plan_id=pid,revision_id=rid,scheduled_date=entry['date'],title=entry['title'],estimated_minutes=entry['estimated_minutes']))
            for position,item in enumerate(entry['items']):
                if item['focus_task_id']:
                    task=c.execute(select(s.coach_saved_retests.c.id).where(s.coach_saved_retests.c.id==item['focus_task_id'],s.coach_saved_retests.c.user_id==uid,s.coach_saved_retests.c.expires_at_ms>self.clock())).first()
                    if not task:raise APIError('NOT_FOUND','动作关联任务不存在',404)
                iid=s.new_id();mapping[item['client_id']]=iid
                c.execute(s.training_plan_items.insert().values(id=iid,user_id=uid,session_id=sid,position=position,action_id=item['action_id'],variant=item['variant'],target_sets=item['target_sets'],target_reps=item['target_reps'],target_duration_ms=item['target_duration_ms'],rest_seconds=item['rest_seconds'],optional_load_json=json_text(item['load']) if item['load'] else None,focus_task_id=item['focus_task_id']))
        return mapping
    def _event(self,c,uid,pid,key,kind,version,sid=None,from_date=None,to_date=None,reason=None):
        c.execute(s.training_schedule_events.insert().values(id=s.new_id(),user_id=uid,plan_id=pid,session_id=sid,command_id=key,event_type=kind,plan_version=version,from_date=from_date,to_date=to_date,reason=reason,created_at_ms=self.clock()))
    def create(self,uid,auth,data,key,source='manual',refs=None):
        key=uuid_id(key);draft=clean_draft(data)
        with self.db.transaction() as c:
            self.actor(c,uid,auth)
            def write():
                pid=s.new_id();c.execute(s.training_plans.insert().values(id=pid,user_id=uid,title=draft['title'],goal=draft['goal'],start_date=draft['start_date'],weeks=draft['weeks'],timezone=draft['timezone'],updated_at_ms=self.clock()))
                rid=self._revision(c,uid,pid,draft,1,source,refs);c.execute(update(s.training_plans).where(s.training_plans.c.id==pid).values(current_revision_id=rid))
                return {'id':pid,'version':0}
            result=idempotent(c,uid,key,{'draft':draft,'source':source,'knowledge_refs':refs or []},'training.plan.create',write)
        return self.get(uid,auth,result['id'])
    def edit(self,uid,auth,pid,data,key):
        key=uuid_id(key);version=integer(data.get('expected_version'),'expected_version')
        if not {'title','edited_revision'}&data.keys():invalid('plan','请修改标题或安排')
        with self.db.transaction() as c:
            row=self.row(c,uid,auth,pid)
            def write():
                if row['state_version']!=version:raise ConflictError('计划已更新')
                if row['status']=='archived':raise APIError('INVALID_STATE','归档计划不可修改',409)
                changes={'state_version':version+1,'updated_at_ms':self.clock()}
                if 'title' in data:changes['title']=text(data['title'],'title',60)
                if 'edited_revision' in data:
                    value=data['edited_revision']
                    if not isinstance(value,dict) or set(value)!={'constraints','sessions'}:invalid('edited_revision')
                    effective=date(data.get('effective_date'),'effective_date')
                    if effective<self.today():invalid('effective_date','只能从今日或之后修改未开始安排')
                    draft=clean_draft({**{k:row[k] for k in ('title','goal','start_date','weeks','timezone')},**value})
                    previous=c.execute(select(s.training_plan_revisions).where(s.training_plan_revisions.c.id==row['current_revision_id'])).mappings().one();rid=self._revision(c,uid,pid,draft,previous['revision_number']+1)
                    candidates=c.execute(select(s.training_plan_sessions).where(s.training_plan_sessions.c.plan_id==pid,s.training_plan_sessions.c.scheduled_date>=effective.isoformat(),s.training_plan_sessions.c.status!='cancelled')).mappings().all()
                    for session in candidates:
                        workout=c.execute(select(s.training_workouts).where(s.training_workouts.c.plan_session_id==session['id'])).mappings().first()
                        if workout and workout['status']!='ready':continue
                        if workout:
                            c.execute(update(s.coach_practice_details).where(s.coach_practice_details.c.linked_workout_id==workout['id']).values(linked_workout_id=None))
                            c.execute(delete(s.training_workouts).where(s.training_workouts.c.id==workout['id']))
                        c.execute(update(s.training_plan_sessions).where(s.training_plan_sessions.c.id==session['id']).values(status='cancelled',schedule_version=session['schedule_version']+1,superseded_by=rid))
                        self._event(c,uid,pid,key,'supersede',version+1,session['id'],from_date=session['scheduled_date'])
                    changes['current_revision_id']=rid
                    if row['status']!='draft':self._materialize(c,uid,pid,rid,draft['sessions'],effective.isoformat())
                c.execute(update(s.training_plans).where(s.training_plans.c.id==pid).values(**changes));return {'id':pid,'version':version+1}
            result=idempotent(c,uid,key,data,'training.plan.edit',write,pid)
        return self.get(uid,auth,result['id'])
    def command(self,uid,auth,pid,kind,data,key):
        key=uuid_id(key);version=integer(data.get('expected_version'),'expected_version')
        with self.db.transaction() as c:
            row=self.row(c,uid,auth,pid)
            def write():
                if row['state_version']!=version:raise ConflictError('计划已更新，请重新确认')
                state=row['status'];target={'activate':'active','pause':'paused','resume':'active','archive':'archived'}[kind]
                if kind=='activate':
                    if data.get('confirmed') is not True:invalid('confirmed','请明确确认启用与替换范围')
                    if state=='active':return {'id':pid,'version':version}
                    if state!='draft':raise APIError('INVALID_STATE','只有草稿可启用',409)
                    if not date(row['start_date'])<=self.today()<date(row['start_date'])+dt.timedelta(weeks=row['weeks']):raise APIError('PLAN_OUT_OF_RANGE','计划不在当前日期范围内',422)
                    current=c.execute(select(s.training_plans).where(s.training_plans.c.user_id==uid,s.training_plans.c.status.in_(['active','paused']),s.training_plans.c.id!=pid)).mappings().first()
                    if current:
                        if data.get('replace_plan_id')!=current['id'] or data.get('replace_expected_version')!=current['state_version']:raise APIError('ACTIVE_PLAN_EXISTS','已有当前计划，请明确确认替换及版本',409)
                        running=c.execute(select(s.training_workouts.c.id).join(s.training_plan_sessions,s.training_workouts.c.plan_session_id==s.training_plan_sessions.c.id).where(s.training_plan_sessions.c.plan_id==current['id'],s.training_workouts.c.status.in_(['running','paused']))).first()
                        if running:raise APIError('WORKOUT_RUNNING','请先结束原计划的执行',409)
                        c.execute(update(s.training_plans).where(s.training_plans.c.id==current['id']).values(status='archived',state_version=current['state_version']+1,updated_at_ms=self.clock()))
                        self._event(c,uid,current['id'],key,'archive',current['state_version']+1)
                    elif data.get('replace_plan_id'):raise ConflictError('被替换计划已变化')
                    revision=c.execute(select(s.training_plan_revisions).where(s.training_plan_revisions.c.id==row['current_revision_id'])).mappings().one()
                    self._materialize(c,uid,pid,revision['id'],json.loads(revision['sessions_json']),self.today().isoformat())
                elif kind=='pause' and state!='active' or kind=='resume' and state!='paused' or kind=='archive' and state not in {'draft','active','paused'}:raise APIError('INVALID_STATE','计划状态不允许此操作',409)
                if kind=='resume' and data.get('past_due_policy')!='keep_missed':invalid('past_due_policy')
                if kind=='archive':
                    active=c.execute(select(s.training_workouts.c.id).join(s.training_plan_sessions,s.training_workouts.c.plan_session_id==s.training_plan_sessions.c.id).where(s.training_plan_sessions.c.plan_id==pid,s.training_workouts.c.status.in_(['running','paused']))).first()
                    if active:raise APIError('WORKOUT_RUNNING','请先结束本计划当前执行',409)
                self._event(c,uid,pid,key,kind,version+1)
                c.execute(update(s.training_plans).where(s.training_plans.c.id==pid).values(status=target,state_version=version+1,updated_at_ms=self.clock(),**({'activated_at_ms':self.clock()} if kind=='activate' else {})))
                return {'id':pid,'version':version+1}
            result=idempotent(c,uid,key,data,'training.plan.'+kind,write,pid)
        return self.get(uid,auth,result['id'])
    def remove(self,uid,auth,pid,data,key):
        version=integer(data.get('expected_version'),'expected_version');key=uuid_id(key)
        with self.db.transaction() as c:
            self.actor(c,uid,auth)
            def write():
                row=self.row(c,uid,auth,pid)
                if row['state_version']!=version:raise ConflictError('计划已变化')
                if not isinstance(data.get('scope'),str) or data.get('scope') not in {'draft_only','plan_and_executions'} or data.get('confirm_title')!=row['title']:invalid('confirm_title','请确认实际标题及删除范围')
                ids=list(c.scalars(select(s.training_workouts.c.id).join(s.training_plan_sessions,s.training_workouts.c.plan_session_id==s.training_plan_sessions.c.id).where(s.training_plan_sessions.c.plan_id==pid)))
                if data['scope']=='draft_only' and (row['status']!='draft' or ids):raise APIError('INVALID_STATE','只允许删除无执行的草稿',409)
                c.execute(update(s.coach_practice_details).where(s.coach_practice_details.c.linked_workout_id.in_(ids)).values(linked_workout_id=None))
                c.execute(delete(s.training_plans).where(s.training_plans.c.id==pid,s.training_plans.c.user_id==uid))
                return {'id':pid,'deleted_plan_id':pid,'deleted_workout_count':len(ids),'records_preserved':True}
            result=idempotent(c,uid,key,data,'training.plan.delete',write,pid)
        return result

def register_training_plans(app):
    service=TrainingPlans(app);app.extensions['training_plans']=service
    @app.get('/api/v1/training/plans')
    def plans_list():return jsonify(data=service.list(current_user.user_id,current_user.id,dict(request.args)))
    @app.get('/api/v1/training/plans/current')
    def current_plan():return jsonify(data=service.current(current_user.user_id,current_user.id))
    @app.post('/api/v1/training/plan-drafts')
    def create_draft():return jsonify(data=service.create(current_user.user_id,current_user.id,body({'title','goal','start_date','weeks','timezone','constraints','sessions'},max_bytes=131072),request.headers.get('Idempotency-Key'))),201
    @app.route('/api/v1/training/plans/<pid>',methods=['GET','PATCH','DELETE'])
    def plan_detail(pid):
        uid,auth=current_user.user_id,current_user.id
        if request.method=='GET':return jsonify(data=service.get(uid,auth,pid))
        if request.method=='PATCH':return jsonify(data=service.edit(uid,auth,pid,body({'expected_version','title','edited_revision','effective_date'},max_bytes=131072),request.headers.get('Idempotency-Key')))
        return jsonify(data=service.remove(uid,auth,pid,body({'expected_version','scope','confirm_title'}),request.headers.get('Idempotency-Key')))
    for command in ('activate','pause','resume','archive'):
        def handler(pid,kind=command):
            fields={'expected_version'}|({'confirmed','replace_plan_id','replace_expected_version'} if kind=='activate' else {'past_due_policy'} if kind=='resume' else set())
            return jsonify(data=service.command(current_user.user_id,current_user.id,pid,kind,body(fields),request.headers.get('Idempotency-Key')))
        app.add_url_rule('/api/v1/training/plans/<pid>/'+command,'training_plan_'+command,handler,methods=['POST'])
    @app.post('/api/v1/training/commands/<key>/abandon')
    def abandon_training_command(key):
        # BEGIN IMMEDIATE与原写命令串行：已提交则返回回执，未提交封存后拒绝迟到写入。
        key=uuid_id(key);data=body({'confirmed'})
        if data.get('confirmed') is not True:invalid('confirmed','请明确确认放弃未登记命令')
        with service.db.transaction() as c:
            uid=current_user.user_id;service.actor(c,uid,current_user.id)
            row=c.execute(select(s.command_receipts).where(s.command_receipts.c.command_id==key)).mappings().first()
            if row:
                if row['actor_user_id']!=uid or not row['resource_type'].startswith('training.'):raise APIError('NOT_FOUND','命令不可核对',404)
                if row['status']=='succeeded':
                    result=json.loads(row['result_json']) if row['result_json'] else {}
                    return jsonify(data={'resource_type':row['resource_type'],'resource_id':row['resource_id'],'saved_version':result.get('version'),'status':'succeeded'})
                if row['resource_type']!='training.abandoned':raise APIError('COMMAND_UNRESOLVED','原命令尚未确认，不可放弃',409)
            else:
                import hashlib
                c.execute(s.command_receipts.insert().values(command_id=key,actor_user_id=uid,resource_type='training.abandoned',request_digest=hashlib.sha256(('abandon:'+key).encode()).hexdigest(),status='failed',ended_at_ms=service.clock()))
            return jsonify(data={'status':'abandoned','resource_type':'training.abandoned','resource_id':None,'saved_version':None})

    @app.get('/api/v1/training/commands/<key>')
    def training_command(key):
        key=uuid_id(key)
        with service.db.transaction(False) as c:
            service.actor(c,current_user.user_id,current_user.id)
            row=c.execute(select(s.command_receipts).where(s.command_receipts.c.command_id==key,s.command_receipts.c.actor_user_id==current_user.user_id,s.command_receipts.c.resource_type.like('training.%'))).mappings().first()
            if not row:raise APIError('NOT_FOUND','命令回执未登记',404)
            result=json.loads(row['result_json']) if row['result_json'] else {}
            return jsonify(data={'resource_type':row['resource_type'],'resource_id':row['resource_id'],'saved_version':result.get('version'),'status':row['status']})
