"""实时会话驱动：服务端状态、控制租约、姿态流与唯一保存协调器。"""
from collections import OrderedDict
from dataclasses import dataclass,field
import copy
import hashlib
import logging
import math
from queue import SimpleQueue,Empty
import threading
import time
from sqlalchemy import select

from .auth import APIError
from .control import ControlRegistry
from .database import ConflictError,StorageError,json_text
from .db_schema import new_id,now_ms
from .db_schema import command_receipts
from .persistence import STORAGE_FAILURES
from .records import TERMINAL,integer,uuid_id

logger=logging.getLogger(__name__)


class IdentityContinuity:
    """保守的躯干位置／尺度连续性门槛；不声称姿态点提供生物身份识别。"""
    def __init__(self):self.anchor=None
    def reset(self):self.anchor=None
    def accept(self,frame):
        from .frame_pipeline import usable_point
        q=frame.get('framing',{});points=q.get('landmarks',[])
        if q.get('person_count')!=1 or frame.get('identity_accepted') is False:return False
        width,height=frame['width'],frame['height'];diagonal=math.hypot(width,height)
        if not math.isfinite(diagonal) or min(width,height)<=0:return False
        current={index:(p['x']*width/diagonal,p['y']*height/diagonal) for index in (11,12,23,24)
                 if len(points)>index and (p:=points[index]).get('valid') and usable_point(p)}
        if len(current)<2:return False
        def geometry(values):
            return (sum(p[0] for p in values)/len(values),sum(p[1] for p in values)/len(values)),max(math.dist(a,b) for a in values for b in values)
        _,scale=geometry(list(current.values()))
        if scale<.02:return False
        if self.anchor:
            # 可见部位变化时，只比较同一组关节；不能把肩线尺度与上一帧整段躯干尺度相比。
            shared=sorted(set(current)&set(self.anchor))
            if len(shared)<2:return False
            center,scale=geometry([current[i] for i in shared]);old_center,old_scale=geometry([self.anchor[i] for i in shared])
            if min(scale,old_scale)<.02:return False
            if math.dist(center,old_center)>max(.18,old_scale*2) or not .4<=scale/old_scale<=2.5:return False
        self.anchor=current;return True


@dataclass
class LiveSession:
    user_id:str
    auth_id:str
    expires_at_ms:int
    data:dict
    stream:dict|None=None
    latest:dict|None=None
    adapter:object=None
    identity:IdentityContinuity=field(default_factory=IdentityContinuity)
    seen_seq:int=-1
    previous_capture:int|None=None
    last_good_at:float|None=None
    last_frame_at:float|None=None
    countdown_until:float|None=None
    rest_deadline:float|None=None
    frozen_rest_ms:int|None=None
    active_ms:int=0
    session_origin_mono:float|None=None
    stream_time_offset_ms:int|None=None
    last_analysis_ms:int=0
    skeleton:object=None
    stability:object=None
    frozen:bool=False
    wanted_terminal:str|None=None
    feedback:list=field(default_factory=list)
    feedback_at:float|None=None
    feedback_epoch:object=None


class SessionManager:
    def __init__(self,store,writer,previews,monotonic=time.monotonic,clock=now_ms,engine_provider=None,allow_observer=False):
        self.store=store;self.writer=writer;self.previews=previews;self.monotonic=monotonic;self.clock=clock
        self.lock=threading.RLock();self.controls=ControlRegistry(monotonic,clock,self.lock)
        self.engine_provider=engine_provider or (lambda action:None);self.allow_observer=allow_observer
        self.live=OrderedDict();self.receipts=OrderedDict();self.notifications=SimpleQueue()
        self._stop=threading.Event();self._thread=None;self._wake=self.previews.result_ready
        self.writer.freeze_hooks.append(lambda **event:self.notifications.put(event))

    @staticmethod
    def digest(sid,payload):return hashlib.sha256(json_text({'session_id':sid,**payload}).encode()).hexdigest()

    def _runtime(self,uid,sid,auth_id=None,expires_at_ms=None):
        data=self.writer.snapshot(uid,sid)
        if data['source']!='camera':raise APIError('INVALID_STATE','该入口仅控制本人摄像头会话',409)
        rt=self.live.get(sid)
        if not rt:
            if auth_id is None:raise APIError('CONTROL_CONFLICT','请先明确申请当前页控制',409)
            rt=LiveSession(uid,auth_id,expires_at_ms or self.clock(),data,active_ms=data['active_ms']);self.live[sid]=rt
        if rt.user_id!=uid:raise APIError('NOT_FOUND','会话不存在',404)
        rt.data=data;self.live.move_to_end(sid)
        if rt.session_origin_mono is None and data['started_at_ms'] is not None:
            rt.session_origin_mono=self.monotonic()-max(0,self.clock()-data['started_at_ms'])/1000
        return rt

    def _drop(self,rt,reset_identity=True):
        if rt.skeleton:
            if rt.data['status'] in TERMINAL:rt.skeleton.close(rt.data['status'] in {'interrupted','failed'})
            else:rt.skeleton.flush()
        if rt.stability:
            if rt.data['status'] in TERMINAL:rt.stability.close(rt.data['status'] in {'interrupted','failed'})
            else:rt.stability.flush()
        self.previews.close_session(rt.user_id,rt.data['session_id']);rt.stream=None;rt.latest=None;rt.seen_seq=-1;rt.previous_capture=None;rt.countdown_until=None;rt.rest_deadline=None;rt.stream_time_offset_ms=None
        if reset_identity:rt.identity.reset()
        if rt.adapter:rt.adapter.reset('flow_stopped')
        rt.feedback=[];rt.feedback_at=None;rt.feedback_epoch=None

    def _freeze(self,rt):
        if rt.frozen_rest_ms is None and rt.data['status']=='resting':rt.frozen_rest_ms=self._rest_remaining(rt)
        rt.frozen=True;self._drop(rt)

    def _rest_remaining(self,rt):
        if rt.frozen_rest_ms is not None and rt.frozen:return rt.frozen_rest_ms
        if rt.rest_deadline is None:return rt.data['rest_remaining_ms']
        return max(0,math.ceil((rt.rest_deadline-self.monotonic())*1000-1e-5))

    def _target_committed(self,rt):
        if rt.data['status']=='running':return False
        rt.previous_capture=None
        if rt.adapter:rt.adapter.reset('group_finished')
        if rt.data['status'] in TERMINAL:
            self._drop(rt);self.controls.release(rt.data['session_id'])
        elif rt.data['status']=='resting' and rt.rest_deadline is None:
            rt.rest_deadline=self.monotonic()+rt.data['rest_remaining_ms']/1000
        return True

    def _set(self,rt,state,key=None,expected=None,reason=None,context=None,system=False,extra=None):
        payload={'state':state,'command_id':key or new_id(),'expected_version':expected,'reason':reason,
                 'extra':{'active_ms':rt.active_ms,**(extra or {})}}
        if context:payload['context']={'request_digest':context}
        if system:payload['system']=True
        rt.data=self.writer.submit(rt.user_id,rt.data['session_id'],rt.auth_id,'state',payload)
        if rt.skeleton and rt.data['status'] in TERMINAL:rt.skeleton.close(rt.data['status'] in {'interrupted','failed'})
        if rt.stability and rt.data['status'] in TERMINAL:rt.stability.close(rt.data['status'] in {'interrupted','failed'})
        if rt.data['status']!='running':rt.feedback=[];rt.feedback_at=None;rt.feedback_epoch=None
        if rt.data.get('save_state')=='save_failed':
            if 'rest_remaining_ms' in payload['extra'] and (state=='resting' or payload['extra'].get('resume_from')=='resting'):
                rt.frozen_rest_ms=payload['extra']['rest_remaining_ms']
            self._freeze(rt)
        return rt.data

    def _receipt(self,uid,sid,key,digest):
        previous=self.receipts.get((uid,key))
        if previous:
            if previous[0]!=digest:raise ConflictError('会话命令与原请求不一致','IDEMPOTENCY_CONFLICT')
            return copy.deepcopy(previous[1])
        stored=self.store.command_result(uid,sid,key)
        if stored:
            if stored.get('context_digest')!=digest:raise ConflictError('会话命令与原请求不一致','IDEMPOTENCY_CONFLICT')
            return {**self.writer.snapshot(uid,sid),'replayed':True,'credential_available':False}
        return None

    def _remember(self,uid,key,digest,result):
        self.receipts[(uid,key)]=(digest,copy.deepcopy(result))
        while len(self.receipts)>256:self.receipts.popitem(last=False)

    def claim(self,uid,auth_id,expires_at_ms,sid,key,payload):
        if set(payload)!={'page_id','takeover','expected_version'}:raise APIError('INVALID_REQUEST','控制字段不完整或含未知字段')
        digest=self.digest(sid,{'operation':'control',**payload})
        with self.lock:
            data=self.writer.snapshot(uid,sid)
            if data['source']!='camera':raise APIError('INVALID_STATE','仅摄像头会话可申请控制',409)
            repeated=self._receipt(uid,sid,key,digest)
            if repeated is not None:return repeated
            if data['status'] in TERMINAL:raise APIError('INVALID_STATE','已结束会话不能取得控制',409)
            rt=self._runtime(uid,sid,auth_id,expires_at_ms)
            if data.get('save_state')!='clean':raise APIError('STORAGE_UNAVAILABLE','请先处理原会话保存故障',503)
            def commit():
                extra={'resume_from':'resting','rest_remaining_ms':self._rest_remaining(rt)} if data['status']=='resting' else None
                result=self._set(rt,'paused' if data['started_at_ms'] is not None else 'ready',key,payload['expected_version'],'control_acquired',digest,extra=extra)
                if result.get('save_state')!='clean':raise StorageError('控制状态尚未保存')
                self._drop(rt);rt.auth_id=auth_id;rt.expires_at_ms=expires_at_ms;rt.frozen=False
                return result
            # 新身份的合法接管用当前身份写事务，失败后恢复原运行绑定。
            old_auth=rt.auth_id;rt.auth_id=auth_id
            try:result=self.controls.claim(uid,auth_id,expires_at_ms,sid,payload['page_id'],payload['takeover'],payload['expected_version'],key,commit)
            except Exception:
                rt.auth_id=old_auth;raise
            self._remember(uid,key,digest,{key:value for key,value in result.items() if key!='control_token'}|{'credential_available':False})
            return result

    def _verify(self,uid,auth_id,sid,payload):
        return self.controls.verify(uid,auth_id,sid,payload.get('control_token'),payload.get('controller_epoch'),payload.get('stream_epoch'))

    def command(self,uid,auth_id,sid,key,payload):
        command=payload.get('command');base={'command','expected_version','control_token','controller_epoch','stream_epoch'}
        allowed=base|({'preview_id','preview_token'} if command in {'start','resume'} else {'confirm'} if command=='end' else set())
        if command not in {'start','pause','resume','cancel_countdown','extend_rest','end'} or set(payload)-allowed:
            raise APIError('INVALID_REQUEST','当前会话命令或字段无效')
        expected=integer(payload.get('expected_version'),'expected_version');digest=self.digest(sid,payload)
        with self.lock:
            self.writer.snapshot(uid,sid)  # 在回执前核归属，管理员不绕过。
            repeated=self._receipt(uid,sid,key,digest)
            if repeated is not None:return repeated
            entry=self._verify(uid,auth_id,sid,payload);rt=self._runtime(uid,sid)
            if rt.data['state_version']!=expected:raise ConflictError('会话已更新，请重新读取')
            state=rt.data['status']
            if state in TERMINAL:raise APIError('INVALID_STATE','该会话已经结束',409)
            if command=='end':
                if payload.get('confirm') is not True:raise APIError('VALIDATION_FAILED','请明确确认结束并保存',422)
                remaining=self._rest_remaining(rt)
                self._drop(rt);rt.wanted_terminal='ended_early'
                if rt.data['save_state']=='clean':self._set(rt,'ended_early',key,expected,'user_ended',digest,extra={'rest_remaining_ms':remaining})
                self.controls.release(sid)
            elif rt.data.get('save_state')!='clean' or rt.frozen and command!='resume':
                raise APIError('STORAGE_UNAVAILABLE','分析已冻结，请先处理保存状态',503)
            elif command in {'start','resume'}:
                if state!=('ready' if command=='start' else 'paused'):raise APIError('INVALID_STATE','当前状态不能开始或继续',409)
                from .catalogue import get_action
                definition=get_action(rt.data['action_id'])
                if not definition or definition['capability_version']!=rt.data['capability_version']:
                    raise APIError('STALE_CAPABILITY','动作能力已变化，请重新选择动作建立新会话；原记录保留',409)
                self.store.require_input_policy(rt.data['config'])
                rt.adapter=self.engine_provider(rt.data['action_id'])
                if rt.adapter is None and not self.allow_observer:raise APIError('ACTION_UNAVAILABLE','该动作的计数能力尚未开放',503)
                if rt.adapter and not getattr(rt.adapter,'counting_available',True) and rt.data['mode']=='target':raise APIError('ACTION_UNAVAILABLE','该动作目前支持姿态观察，暂不支持次数目标',409)
                if rt.adapter:rt.adapter.reset('new_flow')
                resting=command=='resume' and rt.data['resume_from']=='resting' and rt.data['rest_remaining_ms']>0
                if resting:
                    if 'preview_id' in payload or 'preview_token' in payload:raise APIError('INVALID_REQUEST','继续休息不需要预览字段')
                    self.previews.reserve_session(uid,sid)
                    try:result=self._set(rt,'resting',key,expected,'rest_resumed',digest,extra={'rest_remaining_ms':rt.data['rest_remaining_ms']})
                    except Exception:self.previews.close_session(uid,sid);raise
                    if result['save_state']!='clean':raise StorageError('继续休息尚未保存')
                    rt.rest_deadline=self.monotonic()+result['rest_remaining_ms']/1000
                else:
                    pid=uuid_id(payload.get('preview_id'))
                    def commit():return self._set(rt,'countdown',key,expected,'user_started' if command=='start' else 'user_resumed',digest,extra={'resume_from':'running','rest_remaining_ms':0})
                    result,handle=self.previews.adopt(uid,auth_id,entry['page_id'],pid,payload.get('preview_token'),sid,rt.data['action_id'],commit)
                    rt.stream=handle;rt.latest=None;rt.seen_seq=-1;rt.previous_capture=None;rt.identity.reset();rt.last_good_at=self.monotonic()
                    rt.countdown_until=self.monotonic()+3
                rt.frozen=False;self.writer.release(uid,sid)
                rt.frozen_rest_ms=None
                self.controls.rotate_stream(uid,auth_id,sid,payload['control_token'],payload['controller_epoch'],payload['stream_epoch'])
            elif command=='pause':
                if state not in {'running','waiting','countdown','resting'}:raise APIError('INVALID_STATE','当前状态不能暂停',409)
                extra={'resume_from':'resting' if state=='resting' else 'running'}
                if state=='resting':extra['rest_remaining_ms']=self._rest_remaining(rt)
                self._set(rt,'paused',key,expected,'user_paused',digest,extra=extra)
                self._drop(rt)
                if rt.data['save_state']=='clean':self.controls.rotate_stream(uid,auth_id,sid,payload['control_token'],payload['controller_epoch'],payload['stream_epoch'])
            elif command=='extend_rest':
                if state!='resting':raise APIError('INVALID_STATE','当前不在休息阶段',409)
                remaining=self._rest_remaining(rt)
                if remaining<=0:raise APIError('INVALID_STATE','休息已经结束，请刷新状态',409)
                if remaining+30000>3600000:raise APIError('REST_LIMIT','延长后不能超过 3600 秒',422)
                self._set(rt,'resting',key,expected,'rest_extended',digest,extra={'rest_remaining_ms':remaining+30000})
                if rt.data['save_state']=='clean':rt.rest_deadline=self.monotonic()+(remaining+30000)/1000
            else:
                if state!='countdown':raise APIError('INVALID_STATE','当前没有倒计时',409)
                self._set(rt,'ready' if rt.data['started_at_ms'] is None else 'paused',key,expected,'countdown_cancelled',digest)
                self._drop(rt)
                if rt.data['save_state']=='clean':self.controls.rotate_stream(uid,auth_id,sid,payload['control_token'],payload['controller_epoch'],payload['stream_epoch'])
            result=self.decorate_snapshot(uid,rt.data)
            self._remember(uid,key,digest,result)
            return result

    def heartbeat(self,uid,auth_id,sid,payload):
        if set(payload)!={'control_token','controller_epoch','stream_epoch'}:raise APIError('INVALID_REQUEST','心跳字段无效')
        with self.lock:
            data=self.writer.snapshot(uid,sid)
            if data['status'] in TERMINAL:raise APIError('INVALID_STATE','已结束会话不再续控制',409)
            result=self.controls.heartbeat(uid,auth_id,sid,payload['control_token'],payload['controller_epoch'],payload['stream_epoch'])
            self.previews.renew_session(uid,sid);return result

    def _input(self,uid,auth_id,sid,token):
        data=self.writer.snapshot(uid,sid);entry=self.controls.entries.get(sid)
        if not entry:raise APIError('CONTROL_CONFLICT','当前页没有有效控制',409)
        self.controls.verify(uid,auth_id,sid,token,entry['controller_epoch'],entry['stream_epoch'])
        rt=self.live.get(sid)
        if data.get('storage_reads_unavailable'):
            if rt:self._freeze(rt)
            raise APIError('STORAGE_UNAVAILABLE','会话存储不可读，输入已冻结',503)
        if not rt or not rt.stream or rt.frozen or data['save_state']!='clean' or data['status'] not in {'countdown','running','waiting','resting'}:
            raise APIError('INVALID_STATE','当前状态不再接收帧',409)
        return rt

    def guard_create(self,uid,key):
        with self.lock:
            with self.store.db.transaction(False) as c:
                if c.scalar(select(command_receipts.c.command_id).where(command_receipts.c.actor_user_id==uid,command_receipts.c.command_id==key)):
                    return  # 由原仓储先核回执／摘要，不把已成功重试误判成第二项分析。
            for sid,rt in self.live.items():
                if rt.user_id!=uid or rt.data['started_at_ms'] is None:continue
                current=self.writer.snapshot(uid,sid)
                if current['status'] not in TERMINAL:raise APIError('ACTIVE_SESSION','已有未结束的本人分析，请返回原会话',409,{'session_id':sid})

    def forget_records(self,uid,ids):
        with self.lock:
            for sid in ids:
                rt=self.live.get(sid)
                if rt and rt.user_id==uid:self._drop(rt);self.live.pop(sid,None);self.controls.forget(sid)

    def begin_upload(self,uid,auth_id,sid,token):
        with self.lock:
            rt=self._input(uid,auth_id,sid,token);handle=rt.stream
            return self.previews.begin_upload(uid,handle['preview_id'],handle['preview_token'],internal=True)

    def submit_frame(self,uid,auth_id,sid,token,metadata,jpeg):
        if not isinstance(metadata,dict) or set(metadata)!={'controller_epoch','stream_epoch','frame_seq','captured_ms','width','height'}:
            raise APIError('INVALID_REQUEST','会话帧元数据字段无效')
        with self.lock:
            rt=self._input(uid,auth_id,sid,token)
            self.controls.verify(uid,auth_id,sid,token,metadata['controller_epoch'],metadata['stream_epoch'])
            handle=rt.stream;translated={key:value for key,value in metadata.items() if key!='controller_epoch'};translated['stream_epoch']=handle['stream_epoch']
            return self.previews.submit(uid,handle['preview_id'],handle['preview_token'],translated,jpeg,internal=True)

    def decorate_snapshot(self,uid,data):
        with self.lock:
            result=copy.deepcopy(data);sid=data['session_id'];rt=self.live.get(sid)
            if rt and rt.user_id==uid:
                result['skeleton_save']=rt.skeleton.snapshot() if rt.skeleton else None
                result['stability_save']=rt.stability.snapshot() if rt.stability else None
                result.update(analysis_frozen=bool(rt.frozen or data.get('analysis_frozen')),live_active_ms=rt.active_ms,
                    live_rest_remaining_ms=self._rest_remaining(rt) if data['status']=='resting' or data['resume_from']=='resting' else 0,
                    countdown_remaining_ms=max(0,int((rt.countdown_until-self.monotonic())*1000)) if rt.countdown_until else None,
                    counting_available=getattr(rt.adapter,'counting_available',rt.adapter is not None),latest_frame=copy.deepcopy(rt.latest))
            age=max(0,round((self.monotonic()-rt.feedback_at)*1000)) if rt and rt.feedback_at is not None else 0
            valid=rt and rt.user_id==uid and not rt.frozen and data['status']=='running' and data.get('save_state')=='clean'
            result.update(feedback=copy.deepcopy(rt.feedback) if valid else [],feedback_age_ms=age if valid else 0,feedback_epoch=rt.feedback_epoch if valid else None)
            from .coaching import coaching_snapshot
            fresh=bool(rt and rt.latest and rt.last_frame_at is not None and self.monotonic()-rt.last_frame_at<=1)
            result['coaching']=coaching_snapshot(result,rt.latest if rt else None,result['feedback'],result['feedback_age_ms'],fresh)
            from .skeleton import skeleton_snapshot
            result['skeleton']=skeleton_snapshot(result.get('latest_frame'),result['coaching'],fresh=fresh,
                unavailable_reason=None if data['status']=='running' else 'session_not_running')
            result['control']=self.controls.snapshot(uid,sid);return result

    def _publish_feedback(self,rt,event=None,intervals=None,findings=None,events=None):
        """仅在原事务成功后发布参考／已验证问题；依据与建议随结果返回。"""
        verified={i['code'] for i in rt.data.get('supported_items',[]) if i['status'] in {'verified','reference'}};items=[]
        if rt.data.get('save_state')=='clean' and rt.data['status']=='running':
            if event or events is not None:
                items=[{'prompt_id':i['code'],'kind':'cycle','sequence':event['event_seq'],'start_ms':i['start_ms'],'end_ms':i['end_ms'],'side':event['side'],
                        **{key:i[key] for key in ('message','suggestion','evidence') if key in i}}
                    for event in ([event] if event else events)
                    for i in event.get('assessments',[]) if i.get('status')=='issue' and i.get('code') in verified]
            elif findings is not None:
                items=[{'prompt_id':i['code'],'kind':'static','sequence':i['end_ms'],'start_ms':i['start_ms'],'end_ms':i['end_ms'],'side':i['side'],
                        'message':i['message'],'suggestion':i['suggestion'],'evidence':i['evidence']} for i in findings if i['status']=='issue' and i['code'] in verified]
            else:
                items=[{'prompt_id':i['item_code'],'kind':'static','sequence':i['end_ms'],'start_ms':i['start_ms'],'end_ms':i['end_ms']}
                    for i in intervals or [] if i.get('kind')=='issue' and i.get('item_code') in verified]
        supported={i['code']:i for i in rt.data.get('supported_items',[])}
        for item in items:
            spec=supported[item['prompt_id']];item.setdefault('message',spec.get('title'));item.setdefault('suggestion',spec.get('suggestion'));item.update(body_parts=spec.get('body_parts',[]),evidence_level=spec.get('evidence_level','verified'),rule_version=spec.get('rule_version'))
        rt.feedback=items[:32];rt.feedback_at=self.monotonic() if items else None
        rt.feedback_epoch=self.controls.snapshot(rt.user_id,rt.data['session_id']).get('stream_epoch') if items else None

    def _capture_stability(self,rt,frame,output,group):
        if not rt.data['config'].get('stability_policy_version'):return
        if rt.stability is None:
            from .stability_store import StabilityCapture,StabilityStore
            rt.stability=StabilityCapture(StabilityStore(self.store),rt.user_id,rt.data['action_id'],rt.data['session_id'])
        rt.stability.push(frame,output,group,rt.stream['stream_epoch'] if rt.stream else 'bound-runtime')

    def _consume(self,rt,frame):
        rt.last_frame_at=self.monotonic()
        rt.latest=copy.deepcopy(frame);quality=rt.latest['framing'];stamp=frame['captured_ms'];state=rt.data['status']
        usable=quality['usable'] and rt.identity.accept(frame)
        if not usable:
            rt.feedback=[];rt.feedback_at=None;rt.feedback_epoch=None
            if quality['usable']:quality.update(usable=False,ready_for_tracking=False,code='TARGET_AMBIGUOUS',message='目标连续性不确定，请原训练者回到取景位置')
            rt.previous_capture=None
            if rt.adapter:rt.adapter.reset('tracking_lost')
            return
        rt.last_good_at=self.monotonic()
        if state=='countdown' and rt.adapter and hasattr(rt.adapter,'prime'):
            prepared=rt.adapter.prime(rt.latest,{'session_id':rt.data['session_id'],'stream_epoch':rt.stream['stream_epoch'] if rt.stream else 'bound-runtime','time_base':'stream_relative_ms'})
            rt.latest['analysis']=prepared.get('observation',{})
        if state=='running':
            if rt.previous_capture is not None and stamp-rt.previous_capture>500:
                rt.feedback=[];rt.feedback_at=None;rt.feedback_epoch=None
            if rt.previous_capture is not None and 0<stamp-rt.previous_capture<=500:rt.active_ms+=stamp-rt.previous_capture
            if rt.adapter:
                group=next((group for group in rt.data['groups'] if group['status'] in {'pending','active'}),None)
                if not group:return
                if rt.stream_time_offset_ms is None:
                    elapsed=round((self.monotonic()-rt.session_origin_mono)*1000) if rt.session_origin_mono is not None else 0
                    rt.stream_time_offset_ms=max(rt.last_analysis_ms+1,elapsed)-stamp
                rt.last_analysis_ms=stamp+rt.stream_time_offset_ms
                analysis_frame={**rt.latest,'captured_ms':rt.last_analysis_ms,'source_captured_ms':stamp}
                if rt.data['config'].get('save_skeleton'):
                    if rt.skeleton is None:
                        from .skeleton_store import SkeletonBuffer,SkeletonStore
                        rt.skeleton=SkeletonBuffer(SkeletonStore(self.store),rt.user_id,rt.data['session_id'])
                    rt.skeleton.push({**analysis_frame,'set_index':group['set_index'],'stream_epoch':rt.stream['stream_epoch'] if rt.stream else None})
                produced=rt.adapter.process(analysis_frame,{'active_ms':rt.active_ms,'session_id':rt.data['session_id'],'set_index':group['set_index'],'time_base':'session_elapsed_ms','coaching_view':rt.data['config'].get('coaching_view','auto'),
                    'stream_epoch':rt.stream['stream_epoch'] if rt.stream else 'bound-runtime'})
                from .coaching import coaching_observation
                if produced.get('observation') is not None:rt.latest['analysis']=coaching_observation(produced)
                if produced.get('posture_findings'):
                    rt.data=self.writer.submit(rt.user_id,rt.data['session_id'],rt.auth_id,'posture',{'set_index':group['set_index'],'findings':produced['posture_findings'],'active_ms':rt.active_ms})
                    if rt.data['save_state']!='clean':self._freeze(rt);return
                    self._publish_feedback(rt,findings=produced['posture_findings'])
                committed_events=[]
                for event in produced.get('events',[]):
                    event={**event,'event_seq':rt.data['last_event_seq']+1,'set_index':group['set_index'],'active_ms':rt.active_ms}
                    rt.data=self.writer.submit(rt.user_id,rt.data['session_id'],rt.auth_id,'event',event)
                    if rt.data['save_state']!='clean':self._freeze(rt);return
                    committed_events.append(event)
                    self._publish_feedback(rt,events=committed_events)
                    self._capture_stability(rt,analysis_frame,{**produced,'events':[event]},group['set_index'])
                    if self._target_committed(rt):return
                if produced.get('intervals'):
                    rt.data=self.writer.submit(rt.user_id,rt.data['session_id'],rt.auth_id,'intervals',{'set_index':group['set_index'],'intervals':produced['intervals'],'active_ms':rt.active_ms})
                    if rt.data['save_state']!='clean':self._freeze(rt);return
                    if not produced.get('posture_findings'):self._publish_feedback(rt,intervals=produced['intervals'])
                    self._capture_stability(rt,analysis_frame,{**produced,'events':[]},group['set_index'])
                    if self._target_committed(rt):return
                self._capture_stability(rt,analysis_frame,{**produced,'events':[]},group['set_index'])
            rt.previous_capture=stamp
        else:rt.previous_capture=None

    def _interrupt(self,rt,reason):
        remaining=self._rest_remaining(rt)
        self._drop(rt);rt.wanted_terminal=reason;self.controls.release(rt.data['session_id'])
        if rt.data['status'] not in TERMINAL and rt.data.get('save_state')=='clean':
            self._set(rt,'interrupted',reason=reason,system=True,extra={'rest_remaining_ms':remaining})

    def recovered(self,uid,auth_id,sid):
        with self.lock:
            rt=self.live.get(sid)
            if not rt:return
            rt.data=self.writer.snapshot(uid,sid)
            if rt.data['save_state']!='clean':return
            if rt.data['status'] in TERMINAL:self._drop(rt);self.controls.release(sid);return
            rt.auth_id=auth_id
            if rt.wanted_terminal:
                reason=rt.wanted_terminal
                self._set(rt,'ended_early' if reason=='ended_early' else 'interrupted',reason='user_ended' if reason=='ended_early' else reason,system=reason!='ended_early')
            elif rt.frozen and rt.data['status'] not in TERMINAL:
                extra={}
                if rt.data['status']=='resting' or rt.data['resume_from']=='resting':extra={'resume_from':'resting','rest_remaining_ms':rt.frozen_rest_ms if rt.frozen_rest_ms is not None else rt.data['rest_remaining_ms']}
                self._set(rt,'paused',reason='save_recovered',extra=extra)

    def discard(self,uid,sid):
        with self.lock:
            rt=self.live.get(sid)
            if rt and rt.user_id==uid:self._drop(rt);rt.frozen=True;rt.wanted_terminal='unsaved_discarded';self.controls.release(sid)

    def recover_command(self,uid,auth_id,sid,key,payload):
        with self.lock:
            result=self.writer.recovery_command(uid,sid,auth_id,key,payload)
            if payload['command']=='discard_unsaved' and result.get('unsaved_discarded'):self.discard(uid,sid)
            elif payload['command']=='retry_save':self.recovered(uid,auth_id,sid)
            current=self.decorate_snapshot(uid,self.writer.snapshot(uid,sid))
            for field in ('unsaved_discarded','terminal_persisted'):
                if field in result:current[field]=result[field]
            return current

    def cleanup_identity(self,auth_id,sid,payload):
        """只核原页关系；认证数据库不可读时也不授予读／继续／新任务权限。"""
        sid=uuid_id(sid)
        if not isinstance(payload,dict) or payload.get('command') not in {'end','discard_unsaved'}:
            raise APIError('CONTROL_CONFLICT','存储不可读时仅允许原页停止或明确放弃',409)
        allowed={'command','expected_version','confirm','control_token','controller_epoch','stream_epoch'}
        if payload['command']=='discard_unsaved':allowed.add('last_committed_version')
        if set(payload)-allowed or payload.get('confirm') is not True:raise APIError('VALIDATION_FAILED','请明确确认原会话收尾',422)
        integer(payload.get('expected_version'),'expected_version')
        if payload['command']=='discard_unsaved':integer(payload.get('last_committed_version'),'last_committed_version')
        return self.controls.cleanup_binding(auth_id,sid,payload.get('control_token'),payload.get('controller_epoch'),payload.get('stream_epoch'))

    def cleanup_command(self,auth_id,sid,key,payload):
        with self.lock:
            binding=self.cleanup_identity(auth_id,sid,payload);uid=binding['user_id'];digest=self.digest(sid,payload)
            previous=self.receipts.get((uid,key))
            if previous:
                if previous[0]!=digest:raise ConflictError('收尾命令与原请求不一致','IDEMPOTENCY_CONFLICT')
                data=previous[1]
                result={field:data[field] for field in ('session_id','stopped','unsaved_discarded','save_state','terminal_persisted','state_version','last_saved_at_ms') if field in data}
                result.setdefault('stopped',True)
                result.setdefault('terminal_persisted',data.get('save_state')=='clean' and data.get('status') in TERMINAL)
                return result
            rt=self.live.get(sid)
            if not rt or rt.user_id!=uid:raise APIError('CONTROL_CONFLICT','原进程已无该任务，不能推断已停止',409)
            data=self.writer.snapshot(uid,sid)
            if data['state_version']!=payload['expected_version']:raise ConflictError('最后保存版本已变化，请重新核对')
            self._drop(rt);self.controls.release(sid)
            if payload['command']=='discard_unsaved':
                normalized={field:payload[field] for field in ('command','expected_version','last_committed_version','confirm')}
                data=self.writer.recovery_command(uid,sid,auth_id,key,normalized)
                if not data.get('unsaved_discarded'):raise APIError('INVALID_STATE','没有已确认放弃的未保存部分',409)
                self.discard(uid,sid)
            else:
                rt.wanted_terminal='ended_early';rt.data=data
                if data['save_state']=='clean' and data['status'] not in TERMINAL:data=self._set(rt,'ended_early',key,reason='user_ended',context=digest)
            result={'session_id':sid,'stopped':True,'save_state':data['save_state'],'state_version':data['state_version'],
                'last_saved_at_ms':data['last_saved_at_ms'],'terminal_persisted':data.get('terminal_persisted',data['save_state']=='clean' and data['status'] in TERMINAL)}
            if data.get('unsaved_discarded'):result['unsaved_discarded']=True
            self._remember(uid,key,digest,result);return result

    def revoke(self,user_id,auth_session_id=None):
        with self.lock:
            self.controls.revoke(user_id,auth_session_id)
            for rt in self.live.values():
                if rt.user_id==user_id and (auth_session_id is None or rt.auth_id==auth_session_id):
                    rt.data=self.writer.snapshot(rt.user_id,rt.data['session_id']);self._interrupt(rt,'auth_revoked')

    def tick(self):
        with self.lock:
            while True:
                try:notification=self.notifications.get_nowait()
                except Empty:break
                rt=self.live.get(notification['session_id'])
                if rt:self._freeze(rt)
            for entry in self.controls.expire():
                rt=self.live.get(entry['session_id'])
                if rt:
                    rt.data=self.writer.snapshot(rt.user_id,entry['session_id'])
                    if entry['reason']=='auth_expired':self._interrupt(rt,'auth_expired')
                    elif rt.data['status'] in {'ready','paused'}:self._drop(rt)
                    else:self._interrupt(rt,'control_lost')
            for sid,rt in list(self.live.items()):
                if rt.expires_at_ms<=self.clock() and rt.data['status'] not in TERMINAL:self._interrupt(rt,'auth_expired')
                if not (rt.stream or rt.frozen or rt.rest_deadline is not None):continue
                rt.data=self.writer.snapshot(rt.user_id,sid)
                if rt.data.get('storage_reads_unavailable'):self._freeze(rt);continue
                if rt.data.get('save_state')!='clean':self._freeze(rt);continue
                if rt.frozen:
                    if rt.data['status'] not in TERMINAL and rt.data['status']!='paused':self.recovered(rt.user_id,rt.auth_id,sid)
                    continue
                if rt.stream:
                    stream=self.previews.snapshot(rt.user_id,rt.stream['preview_id'],internal=True)
                    if stream['status']=='failed':
                        self._drop(rt);self.controls.release(sid);self._set(rt,'failed',reason=stream.get('reason') or 'POSE_UNAVAILABLE');continue
                    if stream['status']!='active':self._interrupt(rt,'control_lost');continue
                    frame=stream['result']
                    if frame and frame['frame_seq']>rt.seen_seq:
                        rt.seen_seq=frame['frame_seq'];self._consume(rt,frame)
                if rt.frozen:continue
                now=self.monotonic();fresh=rt.last_good_at is not None and now-rt.last_good_at<=.5
                state=rt.data['status']
                if state=='resting':
                    if rt.rest_deadline is None:rt.rest_deadline=now+rt.data['rest_remaining_ms']/1000
                    remaining=self._rest_remaining(rt)
                    if remaining<=0:
                        ready=bool(fresh and rt.latest and rt.latest['framing']['ready_for_tracking'])
                        self._set(rt,'countdown' if ready else 'waiting',reason='rest_finished',extra={'rest_remaining_ms':0,'resume_from':'running'})
                        rt.rest_deadline=None;rt.countdown_until=now+3 if ready else None
                    elif rt.data['rest_remaining_ms']-remaining>=5000:self._set(rt,'resting',reason='rest_checkpoint',extra={'rest_remaining_ms':remaining})
                elif state=='countdown' and rt.countdown_until is not None and now>=rt.countdown_until:
                    first=rt.data['started_at_ms'] is None
                    self._set(rt,'running' if fresh and rt.latest and rt.latest['framing']['ready_for_tracking'] else 'waiting',reason='countdown_finished');rt.countdown_until=None;rt.previous_capture=None
                    if first and rt.data['status']=='running' and rt.data['save_state']=='clean':rt.session_origin_mono=now;rt.stream_time_offset_ms=None
                    if rt.data['status']=='running' and rt.data['save_state']=='clean' and rt.adapter and hasattr(rt.adapter,'begin'):rt.adapter.begin()
                elif state=='running' and (rt.last_good_at is None or now-rt.last_good_at>1):
                    self._set(rt,'waiting',reason='tracking_lost');rt.previous_capture=None
                    if rt.adapter:rt.adapter.reset('tracking_lost')
                elif state=='waiting' and fresh and rt.latest and rt.latest['framing']['ready_for_tracking']:
                    self._set(rt,'countdown',reason='tracking_restored');rt.countdown_until=now+3
                elif state=='running' and rt.active_ms-rt.data['active_ms']>=5000:self._set(rt,'running',reason='active_checkpoint')
            if len(self.live)>256:
                for sid,rt in list(self.live.items()):
                    if len(self.live)<=256:break
                    if rt.data['status'] in TERMINAL and rt.data['save_state']=='clean' and not rt.stream:
                        self.live.pop(sid,None);self.controls.forget(sid)

    def start(self):
        if self._thread:return
        self._stop.clear()
        def run():
            while not self._stop.is_set():
                self._wake.wait(.1);self._wake.clear()
                if self._stop.is_set():break
                try:self.tick()
                except (APIError,*STORAGE_FAILURES) as error:logger.warning('实时调度暂不可用 type=%s',type(error).__name__)
                except Exception as error:logger.error('实时调度异常 type=%s',type(error).__name__)
        self._thread=threading.Thread(target=run,name='fitness-sessions',daemon=True);self._thread.start()

    def stop(self):
        with self.lock:
            for rt in self.live.values():
                if rt.data['status'] not in TERMINAL:self._interrupt(rt,'service_stopped')
            if self.writer.unhealthy:raise StorageError('仍有未保存内容，请重试或明确放弃后再停止')
            self._stop.set();self._wake.set()
        if self._thread:self._thread.join(timeout=2);self._thread=None
