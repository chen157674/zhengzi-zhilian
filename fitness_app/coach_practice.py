"""在原复测身份上扩展练习信息，来源事实与练习状态分别维护。"""
import json
from flask import request,jsonify
from flask_login import current_user
from sqlalchemy import select,update
from itsdangerous import URLSafeSerializer,BadSignature
from . import db_schema as s
from .auth import APIError,body
from .records import uuid_id,integer
from .database import ConflictError,idempotent,json_text

STATES={'saved','practicing','awaiting_retest','completed','dismissed'}
class CoachPractice:
    def __init__(self,app):
        self.app=app;self.store=app.extensions['coach_retests'];self.db=self.store.db
        self.signer=URLSafeSerializer(app.config['SECRET_KEY'],salt='practice-cursor-1')
    def clean(self,uid,auth,data,origin,partial=False):
        if not isinstance(data,dict) or set(data)-{'steps','knowledge_refs','linked_workout_id'}:raise APIError('VALIDATION_FAILED','练习字段无效',422)
        selected=self.app.extensions['retests'].resolve(uid,auth,origin)
        refs=data.get('knowledge_refs',[])
        if not isinstance(refs,list) or len(refs)>10:raise APIError('VALIDATION_FAILED','练习引用过多',422)
        chunks={}
        for ref in refs:
            if not isinstance(ref,dict) or set(ref)!={'id','version'}:raise APIError('VALIDATION_FAILED','知识引用字段无效',422)
            item=self.app.extensions['knowledge_store'].published(ref['id'],ref['version'])
            if not item or item['status']!='published' or item.get('action_id') not in (None,selected['action_id']) or item.get('domain') not in {'movement','hypertrophy'}:raise APIError('KNOWLEDGE_WITHDRAWN','练习来源不适用或已失效',422)
            chunks[ref['id']]=ref
        steps=data.get('steps',[])
        if not isinstance(steps,list) or len(steps)>5:raise APIError('VALIDATION_FAILED','练习步骤最多五条',422)
        for step in steps:
            if not isinstance(step,dict) or set(step)!={'text','citation_ids'} or not isinstance(step['text'],str) or not 1<=len(step['text'].strip())<=200:raise APIError('VALIDATION_FAILED','练习步骤文字无效',422)
            ids=step['citation_ids']
            if not isinstance(ids,list) or len(ids)>10 or any(not isinstance(x,str) or x not in chunks for x in ids):raise APIError('COACH_CITATION_INVALID','步骤引用必须来自适用知识',422)
        wid=data.get('linked_workout_id')
        if wid is not None:
            wid=uuid_id(wid)
            with self.db.transaction(False) as c:
                self.store.sessions._actor(c,uid,auth)
                workout=c.execute(select(s.training_workouts).where(s.training_workouts.c.id==wid,s.training_workouts.c.user_id==uid)).mappings().first()
                if not workout:raise APIError('NOT_FOUND','训练执行不存在',404)
                if workout['status']=='voided' or not any(x['action_id']==selected['action_id'] for x in json.loads(workout['target_snapshot_json']).get('items',[])):raise APIError('PRACTICE_WORKOUT_INCOMPATIBLE','关联执行须包含同一动作且未作废',422)
        return {'steps':[{'text':x['text'].strip(),'citation_ids':list(x['citation_ids'])} for x in steps],'knowledge_refs':list(chunks.values()),'linked_workout_id':wid}
    def snapshot(self,uid,auth,tid):
        with self.db.transaction(False) as c:
            self.store._row(c,uid,auth,tid)
            row=c.execute(select(s.coach_practice_details).where(s.coach_practice_details.c.retest_task_id==tid,s.coach_practice_details.c.user_id==uid)).mappings().first()
        if not row:return None
        refs=json.loads(row['knowledge_refs_json']);valid=[];invalid=[]
        for ref in refs:
            item=self.app.extensions['knowledge_store'].published(ref['id'],ref['version'])
            (valid if item and item['status']=='published' else invalid).append(ref)
        invalid_ids={x['id'] for x in invalid}
        steps=[x for x in json.loads(row['steps_json']) if not invalid_ids.intersection(x['citation_ids'])]
        return {'state_version':row['state_version'],'status':row['status'],'steps':steps,'knowledge_refs':valid,'invalid_knowledge_refs':invalid,'knowledge_status':'source_invalid' if invalid else 'valid','linked_workout_id':row['linked_workout_id']}
    def mutate(self,uid,auth,tid,data,key,command=False):
        tid=uuid_id(tid);key=uuid_id(key);version=integer(data.get('expected_version'),'expected_version')
        task=self.store.get(uid,auth,tid)
        if task['status']!='ready':raise APIError('RETEST_SOURCE_UNAVAILABLE','请重新选择练习来源',409)
        if command:
            if (task.get('practice') or {}).get('knowledge_status')=='source_invalid':raise APIError('KNOWLEDGE_WITHDRAWN','练习方法来源已失效，请重新编辑步骤并核对来源',409)
            kind=data.get('type');changes=None
            if not isinstance(kind,str) or kind not in {'start_practice','finish_practice','dismiss','restore'}:raise APIError('VALIDATION_FAILED','练习命令无效',422)
        else:
            current=self.snapshot(uid,auth,tid) or {'steps':[],'knowledge_refs':[],'linked_workout_id':None}
            merged={k:data.get(k,current[k]) for k in ('steps','knowledge_refs','linked_workout_id')}
            changes=self.clean(uid,auth,merged,task['origin'])
        with self.db.transaction() as c:
            self.store._row(c,uid,auth,tid)
            def write():
                origin=task['origin'];source=self.store.sessions._session(c,uid,origin['session_id'])
                if source['state_version']!=origin['expected_version']:raise ConflictError('练习来源在保存时变化，请重新读取')
                row=c.execute(select(s.coach_practice_details).where(s.coach_practice_details.c.retest_task_id==tid,s.coach_practice_details.c.user_id==uid)).mappings().first()
                if row is None:
                    if version!=0:raise ConflictError('练习版本已变化')
                    c.execute(s.coach_practice_details.insert().values(retest_task_id=tid,user_id=uid,steps_json='[]',knowledge_refs_json='[]'))
                    row={'state_version':0,'status':'saved'}
                if row['state_version']!=version:raise ConflictError('练习已更新，请重新读取')
                values={'state_version':version+1,'updated_at_ms':self.store.clock()}
                if command:
                    current_refs=c.execute(select(s.coach_practice_details.c.knowledge_refs_json).where(s.coach_practice_details.c.retest_task_id==tid)).scalar()
                    for ref in json.loads(current_refs or '[]'):
                        item=self.app.extensions['knowledge_store'].published(ref['id'],ref['version'])
                        if not item or item['status']!='published':raise APIError('KNOWLEDGE_WITHDRAWN','练习依据已变化，请重新编辑步骤',409)
                    allowed={'start_practice':{'saved','awaiting_retest'},'finish_practice':{'practicing'},'dismiss':{'saved','practicing','awaiting_retest'},'restore':{'dismissed'}}
                    if row['status'] not in allowed[kind]:raise APIError('INVALID_STATE','请刷新练习状态后重试',409)
                    values['status']={'start_practice':'practicing','finish_practice':'awaiting_retest','dismiss':'dismissed','restore':'saved'}[kind]
                else:
                    if changes['linked_workout_id']:
                        linked=c.execute(select(s.training_workouts).where(s.training_workouts.c.id==changes['linked_workout_id'],s.training_workouts.c.user_id==uid)).mappings().first()
                        if not linked or linked['status']=='voided' or not any(x['action_id']==task['focus']['action_id'] for x in json.loads(linked['target_snapshot_json']).get('items',[])):raise ConflictError('关联执行在保存时已变化，请重新选择')
                    values.update(steps_json=json_text(changes['steps'],list),knowledge_refs_json=json_text(changes['knowledge_refs'],list),linked_workout_id=changes['linked_workout_id'])
                c.execute(update(s.coach_practice_details).where(s.coach_practice_details.c.retest_task_id==tid,s.coach_practice_details.c.user_id==uid).values(**values))
                return {'id':tid,'version':version+1}
            idempotent(c,uid,key,data,'practice.command' if command else 'practice.edit',write,tid)
        return self.store.get(uid,auth,tid)
    def comparison(self,uid,auth,tid):
        for _ in range(3):
            # 观察连接不执行BEGIN/SELECT，避免读事务把data_version锁在旧快照。
            with self.db.engine.connect() as c:
                before=c.exec_driver_sql('PRAGMA data_version').scalar()
                try:result=self._comparison(uid,auth,tid)
                except APIError:
                    if before==c.exec_driver_sql('PRAGMA data_version').scalar():raise
                    continue
                after=c.exec_driver_sql('PRAGMA data_version').scalar()
            if before==after:return result
        raise APIError('SOURCE_CHANGED','比较来源正在变化，请重新核对',409)
    def _comparison(self,uid,auth,tid):
        task=self.store.get(uid,auth,uuid_id(tid))
        if task['status']!='ready':raise APIError('SOURCE_CHANGED','来源已更新或失效，请重新选择',409)
        origin=task['origin'];source=self.store.sessions.snapshot(uid,origin['session_id'],True)
        if not task['target_record_id']:
            return {'task_id':tid,'task_version':task['state_version'],'comparability':{'status':'awaiting_target','reasons':['请选择新的复测记录']},'metrics':None,'media':None}
        target=self.store.sessions.snapshot(uid,task['target_record_id'],True);comparison=task['comparison']
        from .training_insights import record_statistics,record_evidence
        selected=self.app.extensions['retests'].resolve(uid,auth,origin)
        source_items=[x for x in record_statistics(source) if x['code']==selected['code'] and x['side']==selected['side'] and x['rule_version']==selected['rule_version'] and x['view']==selected['view']]
        before=source_items[0] if len(source_items)==1 else None;after=comparison.get('observation')
        valid=comparison['state'] in {'reobserved','not_reobserved'} and before is not None and after is not None
        def media(record):
            sk=record.get('saved_skeleton') or {};has=sk.get('frame_count',0)>0 and sk.get('status') not in {'deleted','expired','not_saved'}
            return {'record_id':record['session_id'],'availability':'local_required' if record['source']=='video' and record.get('source_sha256') else 'skeleton_only' if has else 'unavailable',
                    'source_sha256':record.get('source_sha256'),'source_bytes':record.get('source_bytes'),'duration_ms':record.get('source_duration_ms'),
                    'evidence_ranges':[{'start_ms':e['start_ms'],'end_ms':e['end_ms']} for e in record_evidence(record) if e['code']==selected['code'] and e['side']==selected['side']], 'skeleton_available':has}
        fields=lambda x:{k:x[k] for k in ('issue','evaluated','unknown')} if x else None
        return {'task_id':tid,'task_version':task['state_version'],'source_version':source['state_version'],'target_version':target['state_version'],
                'comparability':{'status':'comparable' if valid else comparison['state'],'reasons':[] if valid else [comparison['message']]},
                'metrics':{'unit':before['unit'] if before else None,'source':fields(before),'target':fields(after),'change':None},
                'evidence':[selected],'media':{'source':media(source),'target':media(target)},'alignment_policy':'manual_relative_time','comparison':comparison}

    def list(self,uid,auth,args):
        if set(args)-{'view','status','cursor','limit'} or args.get('view')!='practice':raise APIError('INVALID_REQUEST','任务筛选字段无效',400)
        status=args.get('status') or None
        if status is not None and not isinstance(status,str) or status not in STATES|{'source_invalid',None}:raise APIError('VALIDATION_FAILED','任务状态无效',422)
        try:limit=int(args.get('limit','20'))
        except ValueError:raise APIError('VALIDATION_FAILED','分页数量无效',422) from None
        integer(limit,'limit',1,50);offset=0
        if args.get('cursor'):
            try:
                cursor=self.signer.loads(args['cursor'])
                if cursor['uid']!=uid or cursor['status']!=status:raise ValueError()
                offset=integer(cursor['offset'],'offset')
            except (BadSignature,KeyError,ValueError,TypeError):raise APIError('VALIDATION_FAILED','分页游标失效，请重新读取',422) from None
        with self.db.transaction(False) as c:
            self.store.sessions._actor(c,uid,auth)
            ids=c.scalars(select(s.coach_saved_retests.c.id).where(s.coach_saved_retests.c.user_id==uid,s.coach_saved_retests.c.expires_at_ms>self.store.clock()).order_by(s.coach_saved_retests.c.created_at_ms.desc(),s.coach_saved_retests.c.id)).all()
        items=[self.store.get(uid,auth,tid) for tid in ids]
        def state(x):return 'source_invalid' if x['status']!='ready' else 'completed' if (x.get('comparison') or {}).get('state') in {'reobserved','not_reobserved'} else (x.get('practice') or {}).get('status','saved')
        items=[{**x,'practice_status':state(x)} for x in items if status is None or state(x)==status]
        page=items[offset:offset+limit];more=offset+limit<len(items)
        return {'items':page,'has_more':more,'next_cursor':self.signer.dumps({'uid':uid,'status':status,'offset':offset+limit}) if more else None}

def register_practice(app):
    service=CoachPractice(app);app.extensions['coach_practice']=service
    @app.get('/api/v1/retest-tasks/<tid>/comparison')
    def task_comparison(tid):return jsonify(data=service.comparison(current_user.user_id,current_user.id,tid))
    @app.patch('/api/v1/retest-tasks/<tid>/practice')
    def edit_practice(tid):return jsonify(data=service.mutate(current_user.user_id,current_user.id,tid,body({'expected_version','steps','knowledge_refs','linked_workout_id'}),request.headers.get('Idempotency-Key')))
    @app.post('/api/v1/retest-tasks/<tid>/practice-commands')
    def practice_command(tid):return jsonify(data=service.mutate(current_user.user_id,current_user.id,tid,body({'expected_version','type'}),request.headers.get('Idempotency-Key'),True))
