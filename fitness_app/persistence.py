"""有界未提交批次与重试。仅保留服务端已生成数据，异常不伪装成功。"""
from __future__ import annotations

from collections import OrderedDict
import copy
import hashlib
import logging
import threading
import time
from contextlib import contextmanager

from sqlalchemy.exc import OperationalError

from .auth import APIError
from .database import StorageError,ConflictError,json_text
from .db_schema import new_id
from .records import TERMINAL

STORAGE_FAILURES=(OperationalError,StorageError,OSError)
logger=logging.getLogger(__name__)


class PersistenceCoordinator:
    def __init__(self,store,monotonic=time.monotonic):
        self.store=store;self.monotonic=monotonic
        self._lock=threading.RLock();self._pending={};self._discarded={};self._receipts=OrderedDict();self._last=OrderedDict();self._frozen=set()
        self._stop=threading.Event();self._thread=None;self.freeze_hooks=[]

    @property
    def unhealthy(self):
        with self._lock:return bool(self._pending or self._discarded)

    def _save(self,user_id,session_id,auth_id,operation,payload):
        if operation=='event':return self.store.append_event(user_id,session_id,payload,auth_id=auth_id)
        if operation=='intervals':return self.store.append_intervals(user_id,session_id,payload['set_index'],payload['intervals'],auth_id=auth_id,active_ms=payload.get('active_ms'))
        if operation=='posture':return self.store.append_posture(user_id,session_id,payload['set_index'],payload['findings'],auth_id=auth_id,active_ms=payload.get('active_ms'))
        if operation=='state':return self.store.set_state(user_id,session_id,auth_id=auth_id,**payload)
        raise ValueError("不支持的服务端持久化操作")

    def remember(self,user_id,data):
        with self._lock:
            sid=data['session_id'];self._last[sid]={'user_id':user_id,'data':copy.deepcopy(data)}
            self._last.move_to_end(sid)
            while len(self._last)>256:self._last.popitem(last=False)

    def _base(self,user_id,session_id):
        try:
            data=self.store.snapshot(user_id,session_id);self.remember(user_id,data);return data
        except STORAGE_FAILURES:
            cached=self._pending.get(session_id) or self._discarded.get(session_id) or self._last.get(session_id)
            if not cached or cached['user_id']!=user_id or 'data' not in cached:raise
            data=copy.deepcopy(cached['data']);data['storage_reads_unavailable']=True;return data

    def submit(self,user_id,session_id,auth_id,operation,payload):
        with self._lock:
            last=self._base(user_id,session_id)  # 先核归属，缓存也只属于已确认的原账号。
            if session_id in self._discarded:raise APIError("INVALID_STATE","该会话已停止并放弃未保存部分",409)
            if session_id in self._pending:return self.snapshot(user_id,session_id)
            if session_id in self._frozen and operation in {'event','intervals','posture'}:return self.snapshot(user_id,session_id)
            frozen=copy.deepcopy(payload)
            try:
                result=self._save(user_id,session_id,auth_id,operation,frozen);self.remember(user_id,result);return result
            except STORAGE_FAILURES as error:
                try:
                    if self.store.confirmed(user_id,session_id,operation,frozen):
                        result=self._base(user_id,session_id);result['save_state']='clean';return result
                except STORAGE_FAILURES:pass
                self._pending[session_id]={'user_id':user_id,'auth_id':auth_id,'operation':operation,'payload':frozen,
                    'attempts':0,'next_retry':self.monotonic()+0.5,'error_type':type(error).__name__,'data':last}
                self._frozen.add(session_id)
                logger.warning("持久化冻结 session=%s error=%s",session_id,type(error).__name__)
                for callback in self.freeze_hooks:callback(user_id=user_id,session_id=session_id)
                return self.snapshot(user_id,session_id)

    def snapshot(self,user_id,session_id):
        with self._lock:
            data=self._base(user_id,session_id)
            if session_id in self._frozen:data['analysis_frozen']=True
            pending=self._pending.get(session_id)
            if pending:
                if pending['user_id']!=user_id:raise APIError("NOT_FOUND","记录不存在",404)
                payload=pending['payload'];data.update(save_state='save_failed',analysis_frozen=True,
                    pending={'operation':pending['operation'],'event_seq':payload.get('event_seq'),
                             'set_index':payload.get('set_index'),'attempts':pending['attempts']},
                    save_error='部分内容尚未确认保存，分析已暂停，请核对最后确认版本。')
            if session_id in self._discarded:
                data.update(status='interrupted',end_reason='unsaved_discarded',analysis_frozen=True,
                            save_state='save_failed',terminal_persisted=False,unsaved_discarded=True)
            return data

    def _retry(self,session_id,auth_id=None,automatic=False):
        item=self._pending[session_id]
        if auth_id:item['auth_id']=auth_id
        try:
            result=self._save(item['user_id'],session_id,item['auth_id'],item['operation'],item['payload'])
            del self._pending[session_id]
            self.remember(item['user_id'],result)
            if result['status'] in TERMINAL:self._frozen.discard(session_id)
            return self.snapshot(item['user_id'],session_id)
        except STORAGE_FAILURES:
            try:
                if self.store.confirmed(item['user_id'],session_id,item['operation'],item['payload']):
                    self._base(item['user_id'],session_id);del self._pending[session_id];return self.snapshot(item['user_id'],session_id)
            except STORAGE_FAILURES:pass
            if automatic:
                item['attempts']+=1
                delays=(0.5,1,2)
                item['next_retry']=self.monotonic()+delays[item['attempts']] if item['attempts']<3 else None
            return self.snapshot(item['user_id'],session_id)
        except APIError:
            # 身份／业务条件已失效时不能后台无限重试，保留数据供本人核对。
            item['next_retry']=None
            if not automatic:raise
            return self.snapshot(item['user_id'],session_id)

    def retry(self,user_id,session_id,auth_id):
        with self._lock:
            self._base(user_id,session_id)
            if session_id in self._pending:return self._retry(session_id,auth_id)
            if session_id in self._discarded:self._discarded[session_id]['auth_id']=auth_id
            self._reconcile(session_id)
            return self.snapshot(user_id,session_id)

    def recovery_command(self,user_id,session_id,auth_id,command_id,payload):
        """恢复命令先查本进程回执，再核版本；不要求先写库才能确认放弃。"""
        with self._lock:
            key=(user_id,command_id,'http')
            digest=hashlib.sha256(json_text({'session_id':session_id,**payload}).encode()).hexdigest()
            previous=self._receipts.get(key)
            if previous:
                if previous[0]!=digest:raise ConflictError('恢复命令与原请求不一致','IDEMPOTENCY_CONFLICT')
                return copy.deepcopy(previous[1])
            data=self._base(user_id,session_id)
            if data['state_version']!=payload['expected_version']:raise ConflictError('最后保存版本已变化，请重新读取')
            if payload['command']=='retry_save':result=self.retry(user_id,session_id,auth_id)
            else:result=self.discard(user_id,session_id,auth_id,payload['last_committed_version'],command_id)
            self._receipts[key]=(digest,copy.deepcopy(result))
            while len(self._receipts)>256:self._receipts.popitem(last=False)
            return result

    def release(self,user_id,session_id):
        """仅由合法继续命令在业务状态成功落库后调用，不对客户端开放。"""
        with self._lock:
            self.store.snapshot(user_id,session_id)
            if session_id in self._pending or session_id in self._discarded:raise StorageError("保存尚未恢复，不能继续分析")
            self._frozen.discard(session_id)

    @contextmanager
    def deletion_guard(self,user_id,ids):
        with self._lock:
            for sid in ids if isinstance(ids,list) else []:
                if not isinstance(sid,str):raise APIError('VALIDATION_FAILED','记录标识无效',422)
                pending=self._pending.get(sid) or self._discarded.get(sid)
                if pending and pending['user_id']==user_id:raise APIError('INVALID_STATE','请先处理该记录未保存内容',409)
            yield

    def forget(self,user_id,ids):
        with self._lock:
            for sid in ids:
                if self._last.get(sid,{}).get('user_id')==user_id:self._last.pop(sid,None)
                self._frozen.discard(sid)
            for key,(_,result) in list(self._receipts.items()):
                if key[0]==user_id and result.get('session_id') in ids:self._receipts.pop(key,None)

    def discard(self,user_id,session_id,auth_id,last_version,command_id):
        with self._lock:
            data=self._base(user_id,session_id)
            key=(user_id,command_id)
            digest=hashlib.sha256(json_text({'id':session_id,'last_version':last_version}).encode()).hexdigest()
            previous=self._receipts.get(key)
            if previous:
                if previous[0]!=digest:raise ConflictError("清理命令与原请求不一致","IDEMPOTENCY_CONFLICT")
                return previous[1]
            if data['state_version']!=last_version:raise ConflictError("最后保存版本已变化，请重新核对")
            if session_id not in self._pending and session_id not in self._discarded:
                if data['status'] not in TERMINAL:raise APIError("INVALID_STATE","没有待放弃的未提交批次",409)
                return data
            self._pending.pop(session_id,None)
            self._frozen.add(session_id)
            self._discarded[session_id]={'user_id':user_id,'auth_id':auth_id,'command_id':new_id(),'retry_at':self.monotonic(),'data':data}
            for callback in self.freeze_hooks:callback(user_id=user_id,session_id=session_id)
            self._reconcile(session_id)
            result=self.snapshot(user_id,session_id)
            result.update(unsaved_discarded=True,analysis_frozen=True)
            self._receipts[key]=(digest,result)
            while len(self._receipts)>256:self._receipts.popitem(last=False)
            return result

    def _reconcile(self,session_id):
        item=self._discarded.get(session_id)
        if not item:return
        try:
            current=self.store.snapshot(item['user_id'],session_id)
            if current['status'] not in TERMINAL:
                self.store.set_state(item['user_id'],session_id,'interrupted',item['command_id'],auth_id=item['auth_id'],reason='unsaved_discarded')
            del self._discarded[session_id]
            self._frozen.discard(session_id)
        except STORAGE_FAILURES:
            item['retry_at']=self.monotonic()+10
        except APIError:
            item['retry_at']=None

    def tick(self):
        with self._lock:
            now=self.monotonic()
            for sid,item in list(self._pending.items()):
                if item['next_retry'] is not None and item['next_retry']<=now:self._retry(sid,automatic=True)
            for sid,item in list(self._discarded.items()):
                if item['retry_at'] is not None and item['retry_at']<=now:self._reconcile(sid)

    def start(self):
        if self._thread:return
        self._stop.clear()
        def run():
            while not self._stop.wait(0.1):
                try:self.tick()
                except Exception as error:
                    logger.error("持久化重试调度异常 type=%s",type(error).__name__)
        self._thread=threading.Thread(target=run,name='fitness-persistence',daemon=True);self._thread.start()

    def stop(self):
        with self._lock:
            if self._pending:raise StorageError("仍有未保存内容，请重试或明确放弃后再停止")
            self._stop.set()
        if self._thread:self._thread.join(timeout=2);self._thread=None
