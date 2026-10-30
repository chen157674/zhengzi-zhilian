"""实时页控制租约。秘密只存哈希，查询／回执不重新发放控制令牌。"""
from collections import OrderedDict
import hashlib
import hmac
import secrets
import threading
import time

from .auth import APIError
from .database import ConflictError,json_text
from .db_schema import now_ms
from .records import integer,uuid_id


class ControlRegistry:
    def __init__(self,monotonic=time.monotonic,clock=now_ms,lock=None):
        self.monotonic=monotonic;self.clock=clock;self.lock=lock or threading.RLock()
        self.entries={};self.receipts=OrderedDict();self.cleanup=OrderedDict();self.generation=0

    def _live(self,entry):return entry['lease_until']>self.monotonic() and entry['expires_at_ms']>self.clock()

    @staticmethod
    def _matches(entry,token,controller_epoch,stream_epoch):
        return isinstance(token,str) and 16<=len(token)<=256 and type(controller_epoch) is int and type(stream_epoch) is int \
            and entry['controller_epoch']==controller_epoch and entry['stream_epoch']==stream_epoch \
            and hmac.compare_digest(entry['token_hash'],hashlib.sha256(token.encode()).hexdigest())

    @staticmethod
    def _public(entry):
        return {key:entry[key] for key in ('user_id','auth_id','session_id','page_id','controller_epoch','stream_epoch','expires_at_ms','lease_until')}

    def _remember_cleanup(self,entry):
        self.cleanup[entry['session_id']]=dict(entry);self.cleanup.move_to_end(entry['session_id'])
        while len(self.cleanup)>256:self.cleanup.popitem(last=False)

    def claim(self,user_id,auth_id,expires_at_ms,session_id,page_id,takeover,expected_version,command_id,commit):
        """调用方先核会话归属；持同一状态锁的 commit 仅提交业务元数据，不接收秘密。"""
        page_id=uuid_id(page_id);command_id=uuid_id(command_id);integer(expected_version,'expected_version')
        if type(takeover) is not bool:raise APIError('VALIDATION_FAILED','接管确认必须为布尔值',422)
        if expires_at_ms<=self.clock():raise APIError('AUTH_REQUIRED','原登录已失效',401)
        payload={'auth_id':auth_id,'session_id':session_id,'page_id':page_id,'takeover':takeover,'expected_version':expected_version}
        digest=hashlib.sha256(json_text(payload).encode()).hexdigest();key=(user_id,command_id)
        with self.lock:
            previous=self.receipts.get(key)
            if previous:
                if previous[0]!=digest:raise ConflictError('控制请求与原操作不一致','IDEMPOTENCY_CONFLICT')
                return dict(previous[1])
            old=self.entries.get(session_id)
            if old and old['user_id']!=user_id:raise APIError('NOT_FOUND','会话不存在',404)
            if old and self._live(old) and not takeover:
                raise APIError('CONTROL_HELD','该训练已有页面持有控制，请明确确认接管',409)
            token=secrets.token_urlsafe(32);epoch=self.generation+1
            # 先完成业务事务；存储失败时原控制保持原样，不出现新旧两页都可提交。
            committed=commit()
            version=integer(committed['state_version'],'state_version');status=committed['status']
            self.generation=epoch
            entry={'user_id':user_id,'auth_id':auth_id,'session_id':session_id,'page_id':page_id,
                'token_hash':hashlib.sha256(token.encode()).hexdigest(),'controller_epoch':epoch,'stream_epoch':epoch,
                'lease_until':self.monotonic()+15,'expires_at_ms':expires_at_ms}
            self.entries[session_id]=entry;self._remember_cleanup(entry)
            receipt={'session_id':session_id,'controller_epoch':epoch,'stream_epoch':epoch,'lease_seconds':15,
                     'state_version':version,'status':status,'credential_available':False}
            self.receipts[key]=(digest,receipt)
            while len(self.receipts)>256:self.receipts.popitem(last=False)
            return {**receipt,'credential_available':True,'control_token':token}

    def _verify(self,user_id,auth_id,session_id,token,controller_epoch,stream_epoch):
        entry=self.entries.get(session_id)
        if not entry or entry['user_id']!=user_id:raise APIError('CONTROL_CONFLICT','原控制已失效，请重新申请',409)
        if entry['auth_id']!=auth_id or not self._live(entry) or not self._matches(entry,token,controller_epoch,stream_epoch):
            raise APIError('CONTROL_CONFLICT','原控制令牌或流代次已失效',409)
        return entry

    def verify(self,user_id,auth_id,session_id,token,controller_epoch,stream_epoch):
        with self.lock:return self._public(self._verify(user_id,auth_id,session_id,token,controller_epoch,stream_epoch))

    def heartbeat(self,user_id,auth_id,session_id,token,controller_epoch,stream_epoch):
        with self.lock:
            entry=self._verify(user_id,auth_id,session_id,token,controller_epoch,stream_epoch)
            entry['lease_until']=self.monotonic()+15;self._remember_cleanup(entry)
            return {'controller_epoch':entry['controller_epoch'],'stream_epoch':entry['stream_epoch'],
                'lease_remaining_ms':min(15000,max(0,entry['expires_at_ms']-self.clock()))}

    def rotate_stream(self,user_id,auth_id,session_id,token,controller_epoch,stream_epoch):
        """合法继续／重新倒计时后换流，旧半周期和迟到帧均不得沿用。"""
        with self.lock:
            entry=self._verify(user_id,auth_id,session_id,token,controller_epoch,stream_epoch)
            self.generation+=1;entry['stream_epoch']=self.generation;self._remember_cleanup(entry)
            return entry['stream_epoch']

    def snapshot(self,user_id,session_id,page_id=None):
        with self.lock:
            entry=self.entries.get(session_id)
            if entry and entry['user_id']!=user_id:raise APIError('NOT_FOUND','会话不存在',404)
            live=bool(entry and self._live(entry))
            return {'has_controller':live,'controlled_elsewhere':bool(live and page_id!=entry['page_id']),
                    'controller_epoch':entry['controller_epoch'] if live else None,'stream_epoch':entry['stream_epoch'] if live else None,
                    'lease_remaining_ms':min(max(0,int((entry['lease_until']-self.monotonic())*1000)),max(0,entry['expires_at_ms']-self.clock())) if live else 0}

    def expire(self):
        with self.lock:
            expired=[]
            for sid,entry in list(self.entries.items()):
                if not self._live(entry):
                    self._remember_cleanup(entry);del self.entries[sid]
                    expired.append({**self._public(entry),'reason':'auth_expired' if entry['expires_at_ms']<=self.clock() else 'lease_expired'})
            return expired

    def revoke(self,user_id,auth_id=None):
        with self.lock:
            removed=[]
            for sid,entry in list(self.entries.items()):
                if entry['user_id']==user_id and (auth_id is None or entry['auth_id']==auth_id):
                    self._remember_cleanup(entry);del self.entries[sid];removed.append(self._public(entry))
            return removed

    def release(self,session_id):
        with self.lock:
            entry=self.entries.pop(session_id,None)
            if entry:self._remember_cleanup(entry)

    def cleanup_binding(self,auth_id,session_id,token,controller_epoch,stream_epoch):
        """只供数据库不可读时验证原页的停止／放弃；不得作为一般鉴权或读取接口。"""
        with self.lock:
            entry=self.entries.get(session_id) or self.cleanup.get(session_id)
            if not entry or entry['auth_id']!=auth_id or not self._matches(entry,token,controller_epoch,stream_epoch):
                raise APIError('CONTROL_CONFLICT','无法核实原控制关系',409)
            return {key:entry[key] for key in ('user_id','auth_id','session_id')}

    def forget(self,session_id):
        with self.lock:self.entries.pop(session_id,None);self.cleanup.pop(session_id,None)
