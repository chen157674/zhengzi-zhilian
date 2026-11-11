"""可恢复单项任务卡；本人确认后保存，视频/摄像头真实记录进入同项比较。"""
import json
from flask import jsonify,request
from flask_login import current_user
from sqlalchemy import select,update,delete
from . import db_schema as s
from .auth import body,APIError
from .database import ConflictError,json_text
from .records import uuid_id,integer
from .training_insights import record_evidence
from .retests import evaluate

class CoachRetests:
    def __init__(self,app):self.app=app;self.db=app.extensions['database'];self.sessions=app.extensions['session_store'];self.clock=app.config.get('CLOCK') or s.now_ms
    def _row(self,c,uid,auth,tid):
        self.sessions._actor(c,uid,auth)
        row=c.execute(select(s.coach_saved_retests).where(s.coach_saved_retests.c.id==uuid_id(tid),s.coach_saved_retests.c.user_id==uid)).mappings().first()
        if not row or row['expires_at_ms']<=self.clock():raise APIError('NOT_FOUND','复测任务不存在或已到期',404)
        return dict(row)
    def save(self,uid,auth,tid,data):
        tid=uuid_id(tid)
        if data.get('confirmed') is not True:raise APIError('RETEST_CONFIRM_REQUIRED','请确认本次复测重点后保存',422)
        origin=data.get('origin');selected=self.app.extensions['retests'].resolve(uid,auth,origin)
        record=self.sessions.snapshot(uid,origin['session_id'],True)
        if record['action_id'] not in {'machine_chest_press','standing_dumbbell_curl','bodyweight_squat'}:raise APIError('RETEST_ACTION_UNSUPPORTED','新任务卡首版深入支持三个动作',422)
        practice=self.app.extensions['coach_practice'].clean(uid,auth,data['practice'],origin) if 'practice' in data else None
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth)
            source=self.sessions._session(c,uid,origin['session_id'])
            if source['state_version']!=origin['expected_version'] or source['status'] not in {'completed','ended_early'} or source['source']=='demo' or json.loads(source['config_json']).get('included_in_statistics',True)is not True:raise ConflictError('保存时原提示已变化，请重新读取记录')
            previous=c.execute(select(s.coach_saved_retests).where(s.coach_saved_retests.c.id==tid)).mappings().first()
            if previous:
                if previous['user_id']!=uid or json.loads(previous['origin_json'])!=origin:raise ConflictError('保存标识与来源不同')
            else:c.execute(s.coach_saved_retests.insert().values(id=tid,user_id=uid,origin_json=json_text(origin),created_at_ms=self.clock(),expires_at_ms=self.clock()+90*86400000))
            if practice is not None:
                previous_practice=c.execute(select(s.coach_practice_details).where(s.coach_practice_details.c.retest_task_id==tid)).mappings().first()
                if previous_practice:
                    if json.loads(previous_practice['steps_json'])!=practice['steps'] or json.loads(previous_practice['knowledge_refs_json'])!=practice['knowledge_refs'] or previous_practice['linked_workout_id']!=practice['linked_workout_id']:raise ConflictError('保存标识与练习内容不同','IDEMPOTENCY_CONFLICT')
                else:c.execute(s.coach_practice_details.insert().values(retest_task_id=tid,user_id=uid,steps_json=json_text(practice['steps'],list),knowledge_refs_json=json_text(practice['knowledge_refs'],list),linked_workout_id=practice['linked_workout_id']))
        return self.get(uid,auth,tid)
    def get(self,uid,auth,tid):
        with self.db.transaction(False) as c:row=self._row(c,uid,auth,tid)
        origin=json.loads(row['origin_json']);practice=self.app.extensions['coach_practice'].snapshot(uid,auth,tid) if 'coach_practice' in self.app.extensions else None
        result={'task_id':row['id'],'state_version':row['state_version'],'expires_at_ms':row['expires_at_ms'],'target_record_id':row['target_record_id'],'practice':practice}
        try:result.update(origin=origin,focus=self.app.extensions['retests'].resolve(uid,auth,origin),status='ready')
        except (APIError,ConflictError):return {**result,'status':'source_invalid','origin':None,'focus':None,'evidence_status':'invalid','allowed_actions':[]}
        result['evidence_status']='valid'
        state=(practice or {}).get('status','saved')
        result['allowed_actions']={'saved':['start_practice','dismiss'],'practicing':['finish_practice','dismiss'],'awaiting_retest':['start_practice','dismiss'],'completed':[],'dismissed':['restore']}.get(state,[])
        if (practice or {}).get('knowledge_status')=='source_invalid':result['allowed_actions']=[]
        if row['target_record_id']:
            try:
                result['comparison']=self.compare(uid,auth,tid,row['target_record_id'])
                if result['comparison']['state'] in {'reobserved','not_reobserved'}:result['allowed_actions']=[]
            except (APIError,ConflictError):result.update(status='source_invalid',comparison=None,evidence_status='invalid',allowed_actions=[])
        return result
    def list(self,uid,auth):
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth)
            c.execute(delete(s.coach_saved_retests).where(s.coach_saved_retests.c.user_id==uid,s.coach_saved_retests.c.expires_at_ms<=self.clock()))
            ids=c.scalars(select(s.coach_saved_retests.c.id).where(s.coach_saved_retests.c.user_id==uid,s.coach_saved_retests.c.expires_at_ms>self.clock()).order_by(s.coach_saved_retests.c.created_at_ms.desc()).limit(20)).all()
        return {'tasks':[self.get(uid,auth,tid) for tid in ids],'limit':20}
    def compare(self,uid,auth,tid,target_id,confirmed=False):
        with self.db.transaction(False) as c:row=self._row(c,uid,auth,tid)
        if row['target_record_id']!=target_id and not confirmed:raise APIError('RETEST_SUBJECT_REQUIRED','请先确认同一训练者并绑定目标记录',422)
        origin=json.loads(row['origin_json']);self.app.extensions['retests'].resolve(uid,auth,origin)
        source=self.sessions.snapshot(uid,origin['session_id'],True);target=self.sessions.snapshot(uid,uuid_id(target_id),True)
        if source['session_id']==target['session_id'] or source.get('source_sha256') and source.get('source_sha256')==target.get('source_sha256'):raise APIError('RETEST_SAME_SOURCE','请使用调整后新采集或新拍摄的记录',422)
        if target['source'] not in {'camera','video'} or target['status'] not in {'completed','ended_early'} or target.get('included_in_statistics') is False:raise APIError('RETEST_TARGET_UNAVAILABLE','请使用完整保存且有效的新训练记录',422)
        if target['source']=='video':
            with self.db.transaction(False) as c:
                job=c.execute(select(s.jobs.c.status).where(s.jobs.c.session_id==target['session_id'])).first()
                revision=c.execute(select(s.video_revisions.c.status).where(s.video_revisions.c.session_id==target['session_id'])).first()
            if (job and job[0]!='completed') or (revision and revision[0]!='completed') or target['status']!='completed':
                raise APIError('RETEST_TARGET_INCOMPLETE','上传目标必须完整正常完成分析，取消的部分结果不用于正式比较',422)
        if source.get('config',{}).get('exercise_basis')!=target.get('config',{}).get('exercise_basis'):raise APIError('RETEST_NOT_COMPARABLE','两次动作变式依据不同，暂不比较',422)
        if row['target_record_id']==target_id and row['target_version']!=target['state_version']:raise ConflictError('目标记录已修订，原比较已失效')
        selected=next(x for x in record_evidence(source) if x['id']==origin['evidence_id'])
        # 只开放新任务链的视频来源，旧相机复测API语义保持。
        return evaluate(source,target,selected,allow_video=True)
    def bind(self,uid,auth,tid,data):
        if data.get('same_trainee') is not True or data.get('comparable_conditions') is not True:raise APIError('RETEST_SUBJECT_REQUIRED','请确认同一训练者及同器械、变式和可比机位',422)
        target=uuid_id(data.get('target_record_id'));version=integer(data.get('expected_version'),'expected_version')
        checked_target=self.sessions.snapshot(uid,target,True)['state_version']
        if 'target_expected_version' in data and integer(data['target_expected_version'],'target_expected_version')!=checked_target:raise ConflictError('候选记录已修订，请重新核对详情')
        comparison=self.compare(uid,auth,tid,target,True)
        with self.db.transaction() as c:
            row=self._row(c,uid,auth,tid)
            source=self.sessions._session(c,uid,json.loads(row['origin_json'])['session_id'])
            target_row=self.sessions._session(c,uid,target)
            if source['state_version']!=json.loads(row['origin_json'])['expected_version'] or target_row['state_version']!=checked_target:raise ConflictError('比较来源在确认时发生变化，请重新核对')
            if row['target_record_id']==target:
                if row['target_version']!=checked_target:raise ConflictError('目标记录已变化，请重新核对')
                return {'task_id':tid,'comparison':comparison}
            if row['state_version']!=version:raise ConflictError('任务已更新')
            c.execute(update(s.coach_saved_retests).where(s.coach_saved_retests.c.id==tid).values(target_record_id=target,target_version=checked_target,state_version=version+1))
        return self.get(uid,auth,tid)

def register_coach_retests(app):
    store=CoachRetests(app);app.extensions['coach_retests']=store
    @app.route('/api/v1/retest-tasks',methods=['GET','POST'])
    def retest_tasks():
        uid,auth=current_user.user_id,current_user.id
        if request.method=='GET':return jsonify(data=app.extensions['coach_practice'].list(uid,auth,dict(request.args)) if request.args.get('view')=='practice' else store.list(uid,auth))
        return jsonify(data=store.save(uid,auth,request.headers.get('Idempotency-Key'),body({'origin','confirmed','practice'}))),201
    @app.get('/api/v1/retest-tasks/<tid>')
    def retest_task(tid):return jsonify(data=store.get(current_user.user_id,current_user.id,tid))
    @app.post('/api/v1/retest-tasks/<tid>/target')
    def retest_target(tid):return jsonify(data=store.bind(current_user.user_id,current_user.id,tid,body({'target_record_id','expected_version','same_trainee','comparable_conditions','target_expected_version'})))
