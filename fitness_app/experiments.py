"""本人管理员比较任务，复用全局执行槽、身份撤销和幂等任务存储。"""
from dataclasses import dataclass,field
import copy
import json
import logging
import threading
import time

from sqlalchemy import select,update,func
from . import db_schema as s
from .auth import APIError
from .database import idempotent,command_digest,json_text,ConflictError,StorageError
from .records import integer,uuid_id
from .research_metrics import evaluate
from .persistence import STORAGE_FAILURES

FINAL={'completed','cancelled','failed','interrupted'}
logger=logging.getLogger(__name__)


@dataclass
class ExperimentRun:
    experiment_id:str
    job_id:str
    owner_id:str
    auth_id:str
    snapshot:dict
    stop:threading.Event=field(default_factory=threading.Event)
    thread:object=None
    reason:str|None=None
    stopped:bool=False
    terminal_pending:bool=False


class ExperimentManager:
    def __init__(self,store,slot,sources,method_hook=None):
        self.store=store;self.db=store.db;self.slot=slot;self.sources=sources;self.method_hook=method_hook
        self.lock=threading.RLock();self.runs={};self.closed=False

    def _admin(self,c,uid,auth_id=None):
        if auth_id:self.store._actor(c,uid,auth_id)
        role=c.scalar(select(s.users.c.role).where(s.users.c.id==uid,s.users.c.enabled==1))
        if role!='admin':raise APIError('FORBIDDEN','需要管理员权限',403)

    def _owned(self,c,uid,eid):
        self._admin(c,uid)
        row=c.execute(select(s.experiments).where(s.experiments.c.id==eid,s.experiments.c.owner_id==uid)).mappings().first()
        if not row:raise APIError('NOT_FOUND','实验不存在',404)
        return row

    def _job(self,c,uid,eid):
        row=c.execute(select(s.jobs).where(s.jobs.c.experiment_id==eid,s.jobs.c.owner_id==uid,s.jobs.c.kind=='experiment')).mappings().first()
        if not row:raise APIError('NOT_FOUND','实验任务不存在',404)
        return row

    def create(self,uid,auth_id,payload,key):
        key=uuid_id(key)
        with self.lock:
            with self.db.transaction(False) as c:
                self._admin(c,uid,auth_id)
                previous=c.execute(select(s.command_receipts).where(s.command_receipts.c.command_id==key)).mappings().first()
                if previous:
                    if previous['actor_user_id']!=uid or previous['request_digest']!=command_digest('experiment.create',None,payload):raise ConflictError('原请求与当前配置不同','IDEMPOTENCY_CONFLICT')
                    if previous['status']!='succeeded':raise APIError('INVALID_STATE','原请求尚待核对',409)
                    eid=json.loads(previous['result_json'])['id'];return self.snapshot(uid,eid)
            if self.closed:raise APIError('INVALID_STATE','服务正在停止',409)
            prepared=self.sources.snapshot(payload);eid,jid=s.new_id(),s.new_id();self.slot.claim(uid,jid,'experiment')
            try:
                initial={'methods':[{'method_id':method,'status':'queued','metrics':None,'predictions':[]} for method in payload['method_ids']],
                    'evaluation_mode':'verified_prediction_cache','processed_methods':0,'total_methods':len(payload['method_ids'])}
                with self.db.transaction() as c:
                    self._admin(c,uid,auth_id)
                    def write():
                        c.execute(s.experiments.insert().values(id=eid,owner_id=uid,owner_auth_session_id=auth_id,config_json=json_text(prepared['config']),
                            truth_status='source_clip_labels_only',results_json=json_text(initial)))
                        c.execute(s.jobs.insert().values(id=jid,owner_id=uid,kind='experiment',experiment_id=eid))
                        return {'id':eid,'experiment_id':eid,'job_id':jid}
                    idempotent(c,uid,key,payload,'experiment.create',write)
                rt=ExperimentRun(eid,jid,uid,auth_id,prepared);self.runs[eid]=rt
                for old_id,old in list(self.runs.items()):
                    if len(self.runs)<=128:break
                    if old.stopped and not old.terminal_pending:self.runs.pop(old_id)
                rt.thread=threading.Thread(target=self._work,args=(rt,),name='fitness-experiment',daemon=True);rt.thread.start()
            except Exception:self.slot.release(uid,jid,'experiment');raise
            return self.snapshot(uid,eid)

    @staticmethod
    def _result(rows):
        done=sum(row['status']=='completed' for row in rows);processed=sum(row['status'] in {'completed','failed'} for row in rows)
        completeness='full' if done==len(rows) else 'partial' if done else 'none'
        return {'methods':rows,'evaluation_mode':'verified_prediction_cache','processed_methods':processed,'total_methods':len(rows)},completeness

    def _persist(self,rt,status,rows,error=None,actor=True):
        result,completeness=self._result(rows);now=self.store.clock()
        with self.db.transaction() as c:
            if actor:self._admin(c,rt.owner_id,rt.auth_id)
            changes={'status':status,'result_completeness':completeness,'results_json':json_text(result)}
            job={'status':status,'error_code':error,'state_version':s.jobs.c.state_version+1}
            if status=='running':
                current=self._job(c,rt.owner_id,rt.experiment_id)
                if current['started_at_ms'] is None:job['started_at_ms']=now
            if status in FINAL:changes['ended_at_ms']=now;job['ended_at_ms']=now
            c.execute(update(s.experiments).where(s.experiments.c.id==rt.experiment_id,s.experiments.c.owner_id==rt.owner_id).values(**changes))
            c.execute(update(s.jobs).where(s.jobs.c.id==rt.job_id,s.jobs.c.owner_id==rt.owner_id).values(**job))

    def _storage_terminal(self,rt):
        """只保留已提交方法，记录未保存失败；不从未知提交结果造成功。"""
        with self.db.transaction(False) as c:
            row=c.execute(select(s.experiments).where(s.experiments.c.id==rt.experiment_id,s.experiments.c.owner_id==rt.owner_id)).mappings().one()
            rows=json.loads(row['results_json'])['methods']
        rows=[item if item['status']=='completed' else {**item,'status':'failed','error_code':'RESULT_NOT_SAVED','message':'本次结果未确认保存，可在存储恢复后显式重新比较。','metrics':None,'predictions':[]} for item in rows]
        self._persist(rt,'failed',rows,'STORAGE_UNAVAILABLE',actor=False);rt.terminal_pending=False

    def _work(self,rt):
        rows=[{'method_id':method,'status':'queued','metrics':None,'predictions':[]} for method in rt.snapshot['config']['method_ids']]
        try:
            with self.lock:
                if not rt.stop.is_set():self._persist(rt,'running',rows)
            for index,method_id in enumerate(rt.snapshot['config']['method_ids']):
                if rt.stop.is_set():break
                began=time.perf_counter()
                try:
                    if self.method_hook:self.method_hook(method_id,rt.stop)
                    cached=rt.snapshot['methods'][method_id];metrics=evaluate(cached['predictions'])
                    if metrics!=cached['metrics']:raise ValueError('指标协议与冻结预测不匹配')
                    proposal={'method_id':method_id,'method':copy.deepcopy(cached['method']),'status':'completed','metrics':metrics,
                        'predictions':copy.deepcopy(cached['predictions']),'reused_prediction_cache':True,'aggregation_ms':round((time.perf_counter()-began)*1000,3)}
                except Exception as error:
                    proposal={'method_id':method_id,'status':'failed','metrics':None,'predictions':[],
                        'error_code':'METHOD_AGGREGATION_FAILED','message':'该方法的结果汇总失败，其他方法按原选择继续。'}
                    logger.warning('实验方法汇总失败 job=%s method=%s type=%s',rt.job_id,method_id,type(error).__name__)
                with self.lock:
                    if rt.stop.is_set():break
                    updated=copy.deepcopy(rows);updated[index]=proposal;self._persist(rt,'running',updated);rows=updated
            with self.lock:
                status=('cancelled' if rt.reason=='user_cancelled' else 'interrupted') if rt.stop.is_set() else 'failed' if any(row['status']=='failed' for row in rows) else 'completed'
                self._persist(rt,status,rows,rt.reason,actor=not rt.stop.is_set())
        except APIError as error:
            with self.lock:
                rt.reason='auth_revoked';rt.stop.set()
                try:self._persist(rt,'interrupted',rows,error.code,actor=False)
                except STORAGE_FAILURES:rt.terminal_pending=True
        except STORAGE_FAILURES as error:
            with self.lock:
                rt.reason='storage_unavailable';rt.terminal_pending=True
                try:self._storage_terminal(rt)
                except STORAGE_FAILURES:logger.error('实验停止但终态未保存 job=%s type=%s',rt.job_id,type(error).__name__)
        except Exception as error:
            with self.lock:
                rt.reason='experiment_failed'
                try:self._persist(rt,'failed',rows,'EXPERIMENT_FAILED',actor=False)
                except STORAGE_FAILURES:rt.terminal_pending=True
                logger.error('实验执行失败 job=%s type=%s',rt.job_id,type(error).__name__)
        finally:
            with self.lock:rt.stopped=True;self.slot.release(rt.owner_id,rt.job_id,'experiment')

    def snapshot(self,uid,eid):
        with self.lock:
            with self.db.transaction(False) as c:
                row=self._owned(c,uid,eid);job=self._job(c,uid,eid)
            rt=self.runs.get(eid)
            if rt and rt.terminal_pending:
                try:self._storage_terminal(rt)
                except STORAGE_FAILURES:pass
                if not rt.terminal_pending:
                    with self.db.transaction(False) as c:row=self._owned(c,uid,eid);job=self._job(c,uid,eid)
            return {'experiment_id':eid,'job_id':job['id'],'kind':'experiment','status':'failed' if rt and rt.terminal_pending else row['status'],
                'state_version':job['state_version'],'config':json.loads(row['config_json']),'results':json.loads(row['results_json'] or '{}'),
                'result_completeness':row['result_completeness'],'truth_status':row['truth_status'],'created_at_ms':row['created_at_ms'],
                'ended_at_ms':row['ended_at_ms'],'error_code':job['error_code'],'stopped':rt.stopped if rt else job['status'] in FINAL,
                'save_state':'save_failed' if rt and rt.terminal_pending else 'clean','terminal_persisted':job['status'] in FINAL}

    def snapshot_job(self,uid,jid):
        with self.db.transaction(False) as c:
            self._admin(c,uid);eid=c.scalar(select(s.jobs.c.experiment_id).where(s.jobs.c.id==jid,s.jobs.c.owner_id==uid,s.jobs.c.kind=='experiment'))
            if not eid:raise APIError('NOT_FOUND','实验任务不存在',404)
        return self.snapshot(uid,eid)

    def list(self,uid,page=1,page_size=10):
        integer(page,'page',1);integer(page_size,'page_size',1,100)
        with self.db.transaction(False) as c:
            self._admin(c,uid);condition=s.experiments.c.owner_id==uid
            total=c.scalar(select(func.count()).select_from(s.experiments).where(condition))
            ids=c.execute(select(s.experiments.c.id).where(condition).order_by(s.experiments.c.created_at_ms.desc(),s.experiments.c.id.desc()).offset((page-1)*page_size).limit(page_size)).scalars().all()
        summaries=[]
        for eid in ids:
            item=self.snapshot(uid,eid);summaries.append({key:item[key] for key in ('experiment_id','job_id','status','config','created_at_ms','result_completeness','save_state')})
        return {'experiments':summaries,'page':page,'page_size':page_size,'total':total}

    def cancel(self,uid,auth_id,jid,version,key):
        integer(version,'expected_version')
        with self.lock:
            with self.db.transaction() as c:
                self._admin(c,uid,auth_id);job=c.execute(select(s.jobs).where(s.jobs.c.id==jid,s.jobs.c.owner_id==uid,s.jobs.c.kind=='experiment')).mappings().first()
                if not job:raise APIError('NOT_FOUND','实验任务不存在',404)
                eid=job['experiment_id']
                def write():
                    if job['status'] in FINAL:return {'id':jid,'experiment_id':eid}
                    if version!=job['state_version']:raise ConflictError('任务状态已变化，请重新读取')
                    rt=self.runs.get(eid)
                    if rt:rt.reason='user_cancelled';rt.stop.set()
                    c.execute(update(s.jobs).where(s.jobs.c.id==jid).values(status='cancelling',state_version=s.jobs.c.state_version+1))
                    c.execute(update(s.experiments).where(s.experiments.c.id==eid).values(status='cancelling'))
                    return {'id':jid,'experiment_id':eid}
                idempotent(c,uid,uuid_id(key),{'expected_version':version},'job.cancel',write,jid)
            return self.snapshot(uid,eid)

    def revoke(self,user_id,auth_session_id=None):
        with self.lock:
            for rt in self.runs.values():
                if rt.owner_id==user_id and not rt.stopped and (auth_session_id is None or rt.auth_id==auth_session_id):rt.reason='auth_revoked';rt.stop.set()

    def recover(self):
        with self.db.transaction() as c:
            c.execute(update(s.experiments).where(s.experiments.c.status.not_in(FINAL)).values(status='interrupted',ended_at_ms=self.store.clock()))
            c.execute(update(s.jobs).where(s.jobs.c.kind=='experiment',s.jobs.c.status.not_in(FINAL)).values(status='interrupted',error_code='PROCESS_RESTART',ended_at_ms=self.store.clock(),state_version=s.jobs.c.state_version+1))

    def stop(self):
        with self.lock:
            self.closed=True;runs=list(self.runs.values())
            for rt in runs:
                if not rt.stopped:rt.reason='service_stopped';rt.stop.set()
        for rt in runs:
            rt.thread.join(timeout=2)
            if rt.thread.is_alive():raise StorageError('实验仍在停止，请稍后重试')
            if rt.terminal_pending:self._storage_terminal(rt)
