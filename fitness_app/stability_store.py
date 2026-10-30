"""本人运动摘要的有界持久化；无坐标、无独立正确性或疲劳结论。"""
import hashlib,json,logging,threading
from collections import OrderedDict
from flask import jsonify,request
from flask_login import current_user
from sqlalchemy import select,update
from . import db_schema as s
from .auth import APIError
from .database import ConflictError,json_text
from .records import uuid_id,TERMINAL
from .persistence import STORAGE_FAILURES
from .movement_stability import VERSION,MAX_WINDOWS,Collector,report

FAILURES=STORAGE_FAILURES+(ConflictError,APIError,ValueError)
logger=logging.getLogger(__name__)
LIMITATIONS=['仅报告可见关节二维图像的观测变化，不能证明整体动作正确。','不推断肌肉发力、疲劳、伤病风险或调整的因果效果。','完整周期和参考区间/同长度观察窗分开；不足三秒尾段不参与窗口对比。']


class StabilityStore:
    def __init__(self,sessions):self.sessions=sessions;self.db=sessions.db;self.clock=sessions.clock;self.cache=OrderedDict();self.cache_lock=threading.RLock()
    def append(self,uid,sid,index,window):
        payload=json_text(window)
        if len(payload.encode())>32768:raise ValueError('运动摘要过大')
        digest=hashlib.sha256(payload.encode()).hexdigest()
        with self.db.transaction() as c:
            session=self.sessions._session(c,uid,sid)
            if json.loads(session['config_json']).get('stability_policy_version')!=VERSION:return 'not_recorded'
            row=c.execute(select(s.stability_runs).where(s.stability_runs.c.id==sid,s.stability_runs.c.user_id==uid)).mappings().first()
            if row is None:
                c.execute(s.stability_runs.insert().values(id=sid,user_id=uid,policy_version=VERSION,created_at_ms=self.clock()))
                row=c.execute(select(s.stability_runs).where(s.stability_runs.c.id==sid)).mappings().one()
            existing=c.scalar(select(s.stability_windows.c.sha256).where(s.stability_windows.c.session_id==sid,s.stability_windows.c.window_index==index))
            if existing:
                if existing!=digest:raise ConflictError('同一运动摘要内容不一致')
                return row['status']
            if row['status']!='active':return row['status']
            if index!=row['window_count']:raise ConflictError('运动摘要顺序不一致')
            if index>=MAX_WINDOWS:
                c.execute(update(s.stability_runs).where(s.stability_runs.c.id==sid).values(status='limited',reason='window_limit',state_version=row['state_version']+1));return 'limited'
            c.execute(s.stability_windows.insert().values(session_id=sid,window_index=index,data_json=payload,sha256=digest))
            c.execute(update(s.stability_runs).where(s.stability_runs.c.id==sid).values(window_count=index+1,state_version=row['state_version']+1))
        return 'active'
    def finish(self,uid,sid,status='complete',reason=None):
        with self.db.transaction() as c:
            self.sessions._session(c,uid,sid)
            c.execute(update(s.stability_runs).where(s.stability_runs.c.id==sid,s.stability_runs.c.user_id==uid,s.stability_runs.c.status=='active').values(status=status,reason=reason,state_version=s.stability_runs.c.state_version+1))
    def snapshot(self,uid,sid,include_windows=False):
        with self.db.transaction() as c:
            session=self.sessions._session(c,uid,uuid_id(sid));row=c.execute(select(s.stability_runs).where(s.stability_runs.c.id==sid,s.stability_runs.c.user_id==uid)).mappings().first()
            if row is None:
                enrolled=json.loads(session['config_json']).get('stability_policy_version')==VERSION
                return {'status':'insufficient_evidence' if enrolled else 'not_recorded','policy_version':VERSION,'comparisons':[],'window_count':0,'state_version':0,
                    'message':'还没有形成可保存的同组运动观察。' if enrolled else '本记录没有保存运动稳定性摘要，不自动重新分析。','limitations':LIMITATIONS,'evidence_level':'reference','independent_specific_error_accuracy':None}
            if row['policy_version']!=VERSION:return {'status':'unsupported_policy','policy_version':row['policy_version'],'comparisons':[],'window_count':row['window_count'],'state_version':row['state_version'],'message':'本记录使用其他运动摘要策略，未用新策略重新解释旧结果。','limitations':LIMITATIONS,'evidence_level':'reference','independent_specific_error_accuracy':None}
            if row['status']=='active' and session['ended_at_ms'] is not None and self.clock()-session['ended_at_ms']>=5000:
                c.execute(update(s.stability_runs).where(s.stability_runs.c.id==sid).values(status='interrupted',reason='closing_unconfirmed',state_version=row['state_version']+1))
                row={**row,'status':'interrupted','reason':'closing_unconfirmed','state_version':row['state_version']+1}
            key=(uid,sid,row['state_version'])
            with self.cache_lock:
                cached=self.cache.get(key) if not include_windows else None
                if cached is not None:self.cache.move_to_end(key);return json.loads(cached)
            raw=c.execute(select(s.stability_windows.c.data_json,s.stability_windows.c.sha256).where(s.stability_windows.c.session_id==sid).order_by(s.stability_windows.c.window_index)).all()
            if len(raw)!=row['window_count']:raise APIError('STABILITY_DATA_UNAVAILABLE','已保存运动摘要不完整',503)
            windows=[]
            for payload,digest in raw:
                if hashlib.sha256(payload.encode()).hexdigest()!=digest:raise APIError('STABILITY_DATA_UNAVAILABLE','已保存运动摘要完整性校验失败',503)
                windows.append(json.loads(payload))
        compared=report(windows)
        result={'status':row['status'],'policy_version':row['policy_version'],'window_count':row['window_count'],'state_version':row['state_version'],'reason':row['reason'],
            'comparisons':compared,'message':'前后观测变化仅供参考，不是体能或健康评分。','limitations':LIMITATIONS,'evidence_level':'reference','independent_specific_error_accuracy':None,
            'maximum_windows':MAX_WINDOWS,'unknown_windows':sum(all(m['status']!='observed' for m in w['metrics']) for w in windows)}
        result['assessment_state']='compared' if any(v['state']=='compared' for v in compared) else 'not_comparable' if any(v['state']=='not_comparable' for v in compared) else 'insufficient_evidence'
        if include_windows:result['windows']=windows
        else:
            with self.cache_lock:
                self.cache[key]=json_text(result)
                while len(self.cache)>32:self.cache.popitem(last=False)
        return result
    def safe_snapshot(self,uid,sid):
        try:return self.snapshot(uid,sid)
        except APIError as error:
            if error.code!='STABILITY_DATA_UNAVAILABLE':raise
            return {'status':'unavailable','policy_version':VERSION,'comparisons':[],'window_count':0,'state_version':0,'message':'运动摘要完整性未通过，训练摘要仍可查看。','limitations':LIMITATIONS,'evidence_level':'reference','independent_specific_error_accuracy':None}


class StabilityCapture:
    def __init__(self,store,uid,action,sid=None,offset=0):
        self.store,self.uid,self.sid=store,uid,sid;self.collector=Collector(action,offset);self.pending=[];self.index=0;self.status='collecting';self.reason=None
    def bind(self,sid):self.sid=sid;self.flush()
    def push(self,frame,output,group=1,epoch='single'):
        if self.status not in ('active','collecting'):return
        self.pending.extend(self.collector.push(frame,output,group,epoch))
        if self.sid:self.flush()
        elif len(self.pending)>30:self.status='limited';self.reason='pre_record_limit';self.pending=self.pending[:30]
    def flush(self):
        if not self.sid or self.status not in ('active','collecting','limited'):return
        limited=self.status=='limited'
        while self.pending:
            try:state=self.store.append(self.uid,self.sid,self.index,self.pending[0])
            except FAILURES as error:
                try:state=self.store.append(self.uid,self.sid,self.index,self.pending[0])
                except FAILURES:
                    self.status='partial';self.reason='storage_failure';logger.warning('运动摘要保存停止 record=%s type=%s',self.sid,type(error).__name__)
                    try:self.store.finish(self.uid,self.sid,'partial',self.reason)
                    except FAILURES:logger.warning('运动摘要故障状态待恢复 record=%s',self.sid)
                    return
            self.status=state
            if state!='active':self.pending.clear();return
            self.index+=1;self.pending.pop(0)
        if limited:
            self.status='limited'
            try:self.store.finish(self.uid,self.sid,'limited',self.reason)
            except FAILURES:self.status='partial';self.reason='limit_state_unconfirmed';logger.warning('运动摘要上限状态待恢复 record=%s',self.sid)
    def close(self,interrupted=False):
        if self.status in ('active','collecting'):self.pending.extend(self.collector.finish());self.flush()
        if self.sid and self.status in ('active','collecting'):
            try:self.store.finish(self.uid,self.sid,'interrupted' if interrupted else 'complete');self.status='interrupted' if interrupted else 'complete'
            except FAILURES:self.status='partial';self.reason='closing_unconfirmed'
        self.pending.clear();self.collector.rows.clear()
    def snapshot(self):return {'status':self.status,'reason':self.reason,'saved_windows':self.index}


def register_stability(app):
    store=StabilityStore(app.extensions['session_store']);app.extensions['stability_store']=store
    def recover():
        with store.db.transaction() as c:c.execute(update(s.stability_runs).where(s.stability_runs.c.status=='active').values(status='interrupted',reason='service_restarted',state_version=s.stability_runs.c.state_version+1))
    app.extensions['start_hooks'].append(recover)
    @app.get('/api/v1/records/<session_id>/stability')
    def stability_record(session_id):
        if set(request.args)-{'windows'} or request.args.get('windows','0') not in {'0','1'}:raise APIError('INVALID_REQUEST','运动摘要查询字段无效',400)
        return jsonify(data=store.snapshot(current_user.user_id,uuid_id(session_id),request.args.get('windows')=='1'))
