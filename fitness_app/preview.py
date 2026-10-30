"""有界的内存预览。租约由有效输入续期，GET 不续期，不创建训练记录。"""
from collections import OrderedDict,deque
import copy
import hashlib
import hmac
import logging
import secrets
import threading
import time

from .auth import APIError
from .catalogue import get_action
from .database import ConflictError,json_text
from .db_schema import new_id,now_ms
from .frame_pipeline import decode_jpeg,analysis_framing as framing,jpeg_dimensions,MODEL_VERSION
from .gym_pose import GymPoseService as ContentPoseService
from .content_bounds import detect_content_bounds
from .records import integer,uuid_id
from .skeleton import skeleton_snapshot
from .automatic_recognition import body_frame
from .basic_recognition import BasicRecognizer
from .semantic_recognition import create_recognizer
from .automatic_analysis import AutomaticAnalysis

logger=logging.getLogger(__name__)


class PreviewManager:
    def __init__(self,slot,pose_factory=ContentPoseService,monotonic=time.monotonic,clock=now_ms,record_store=None,recognizer_factory=None):
        self.record_store=record_store
        self.recognizer_factory=recognizer_factory
        self.slot=slot;self.pose_factory=pose_factory;self.monotonic=monotonic;self.clock=clock
        self.lock=threading.RLock();self.active=None;self.history=OrderedDict();self.receipts=OrderedDict()
        self._stop=threading.Event();self._wake=threading.Event();self._thread=None;self.processing=0;self.processing_id=None;self._deferred_release=set()
        self.result_ready=threading.Event()

    def _receipt(self,user_id,command_id,operation,payload,run):
        key=(user_id,uuid_id(command_id));digest=hashlib.sha256(json_text({'operation':operation,**payload}).encode()).hexdigest()
        previous=self.receipts.get(key)
        if previous:
            if previous[0]!=digest:raise ConflictError('预览命令与原请求不一致','IDEMPOTENCY_CONFLICT')
            return copy.deepcopy(previous[1])
        result=run();self.receipts[key]=(digest,copy.deepcopy(result))
        while len(self.receipts)>256:self.receipts.popitem(last=False)
        return result

    def create(self,user_id,auth_id,expires_at_ms,payload,command_id):
        view=payload.get('coaching_view','auto')
        if not isinstance(view,str) or view not in {'auto','front','side','oblique'}:raise APIError('VALIDATION_FAILED','拍摄机位只能为自动/正面/侧面/斜侧面',422)
        save_training=payload.get('save_training',False)
        save_skeleton=payload.get('save_skeleton',False)
        if type(save_skeleton) is not bool or save_skeleton and not save_training:raise APIError('VALIDATION_FAILED','保存骨架须同时保存训练记录',422)
        if not isinstance(save_training,bool):raise APIError('VALIDATION_FAILED','保存选项无效',422)
        if save_training and self.record_store is None:raise APIError('INVALID_STATE','训练保存尚未可用',409)
        if payload.get('training_id') is not None:
            uuid_id(payload['training_id'])
            if not save_training:raise APIError('VALIDATION_FAILED','纯预览不能关联训练',422)
        mode=payload.get('recognition_mode','manual' if payload.get('action_id') is not None else 'auto')
        if mode not in ('auto','manual'):raise APIError('VALIDATION_FAILED','识别模式无效',422)
        if mode=='auto' and (payload.get('action_id') is not None or payload.get('capability_version') is not None):
            raise APIError('VALIDATION_FAILED','自动识别不接受预先指定动作',422)
        action=get_action(payload.get('action_id')) if mode=='manual' else None;page_id=uuid_id(payload.get('page_id'))
        if mode=='manual' and (not action or action['capability_version']!=payload.get('capability_version')):raise APIError('STALE_CAPABILITY','请重新读取动作能力',422)
        with self.lock:
            self.expire()
            def run():
                if save_training and any(x.get('recorder') and x['recorder'].failed and x['user_id']==user_id for x in [*self.history.values(),*([self.active] if self.active else [])]):
                    raise APIError('STORAGE_UNAVAILABLE','请先处理原自动训练未保存的内容',503)
                retest=None;retest_focus=None
                if payload.get('retest_origin') is not None:
                    if not save_training:raise APIError('VALIDATION_FAILED','复测需保存新的训练记录',422)
                    from .retests import Retests
                    selected=Retests(self.record_store).resolve(user_id,auth_id,payload['retest_origin'])
                    retest=selected['origin'];retest_focus=selected['code']
                recognizer=(self.recognizer_factory() if self.recognizer_factory else create_recognizer(BasicRecognizer)) if mode=='auto' else None
                prior=[]
                if payload.get('training_id'):
                    prior=[x['recorder'] for x in [*self.history.values(),*([self.active] if self.active else [])] if x['user_id']==user_id and x.get('recorder') and x['recorder'].run_id==payload['training_id']]
                    if not prior:raise APIError('NOT_FOUND','原训练上下文不存在，请开始新训练',404)
                    if any(getattr(rec,'retest_origin',None)!=retest for rec in prior):raise ConflictError('同次训练不能切换复测来源，请结束后新建训练')
                    if any(getattr(rec,'save_skeleton',False)!=save_skeleton for rec in prior):raise ConflictError('同次训练不能切换骨架保存选择，请结束后新建训练')
                pid=new_id();self.slot.claim(user_id,pid,'preview');token=secrets.token_urlsafe(32)
                from .automatic_recording import AutomaticRecorder
                recorder=AutomaticRecorder(self.record_store,user_id,auth_id,payload.get('training_id') or pid,retest_origin=retest,save_skeleton=save_skeleton) if save_training else None
                if recorder and prior:recorder.record_ids=list(dict.fromkeys(sid for item in prior for sid in item.record_ids))
                self.active={'preview_id':pid,'user_id':user_id,'auth_id':auth_id,'page_id':page_id,'action_id':action['action_id'] if action else None,
                    'recognition_mode':mode,'coaching_view':view,'recognizer':recognizer,'recorder':recorder,'automatic_analysis':AutomaticAnalysis(recorder=recorder,coaching_view=view,focus_code=retest_focus) if mode=='auto' or save_training else None,
                    'expires_at_ms':expires_at_ms,'token':token,'stream_epoch':new_id(),'lease_until':self.monotonic()+15,
                    'frame_seq':-1,'captured_ms':-1,'digest':None,'pending':None,'result':None,'status':'active','submissions':deque(),
                    'visible_since':None,'last_processed_ms':None,'accepted':0,'replaced':0,'processed':0,'uploading':False}
                self._wake.set()
                return {'preview_id':pid,'preview_token':token,'stream_epoch':self.active['stream_epoch'],'lease_seconds':15,'status':'active','recognition_mode':mode,'training_id':recorder.run_id if recorder else None}
            result=self._receipt(user_id,command_id,'create',payload,run)
            self.start();return result

    @staticmethod
    def _slot_ref(item):return (item['session_id'],'session') if item.get('session_id') else (item['preview_id'],'preview')

    def failed_trainings(self,user_id):
        with self.lock:
            return [{'preview_id':item['preview_id'],**item['recorder'].snapshot()}
                for item in [*self.history.values(),*([self.active] if self.active else [])]
                if item['user_id']==user_id and item.get('recorder') and item['recorder'].failed]

    def record_failure(self,user_id,session_id):
        return next((item for item in self.failed_trainings(user_id) if session_id==item['saved_record_id']),None)

    def recover_training(self,user_id,auth_id,pid,discard=False):
        with self.lock:
            item=self._find(user_id,pid)
            if not item.get('recorder'):raise APIError('INVALID_STATE','此输入没有训练记录',409)
            result=item['recorder'].recover(auth_id,discard)
            if result['save_state']=='clean' and item is self.active:self._close('released','save_recovered')
            return result

    def _find(self,user_id,pid,internal=False):
        item=self.active if self.active and self.active['preview_id']==pid else self.history.get(pid)
        if not item or item['user_id']!=user_id or (item.get('session_id') and not internal):raise APIError('NOT_FOUND','预览不存在',404)
        return item

    def _authorize(self,user_id,pid,token,epoch,active=True,internal=False):
        item=self._find(user_id,pid,internal)
        if not isinstance(token,str) or not hmac.compare_digest(token,item['token']) or epoch!=item['stream_epoch']:
            raise APIError('CONTROL_CONFLICT','原预览流已失效',409)
        if active and (item['status']!='active' or item is not self.active):raise APIError('PREVIEW_EXPIRED','预览已释放，请重新开启摄像头',409)
        return item

    def release(self,user_id,pid,payload,command_id):
        with self.lock:
            self.expire()
            def run():
                item=self._authorize(user_id,pid,payload.get('preview_token'),payload.get('stream_epoch'),False)
                if item is self.active:self._close('released')
                return {'preview_id':pid,'released':True,'training':item['recorder'].snapshot() if item.get('recorder') else None}
            return self._receipt(user_id,command_id,'release',{'preview_id':pid,**payload},run)

    def _close(self,status,reason=None):
        item=self.active
        if item is None:return
        item['status']=status;item['reason']=reason;item['pending']=None;item['result']=None
        if item.get('recognizer') and hasattr(item['recognizer'],'close'):item['recognizer'].close()
        item['recognizer']=None
        if item.get('automatic_analysis'):item['automatic_analysis'].reset(reason if reason in {'auth_revoked','auth_expired','service_stopped','control_lost'} else 'preview_closed')
        item['automatic_analysis']=None
        reference=(item['user_id'],*self._slot_ref(item))
        if self.processing:self._deferred_release.add(reference)
        else:self.slot.release(*reference)
        self.history[item['preview_id']]=item
        while len(self.history)>256:
            removable=next((key for key,value in self.history.items() if not value.get('recorder') or not value['recorder'].failed),None)
            if removable is None:break
            self.history.pop(removable)
        self.active=None;self._wake.set()

    def expire(self):
        # 调用方持有 lock；RLock 也供可控时钟测试直接调用。
        with self.lock:
            item=self.active
            if item and (item['lease_until']<=self.monotonic() or item['expires_at_ms']<=self.clock()):self._close('expired','auth_expired' if item['expires_at_ms']<=self.clock() else 'control_lost')

    def revoke(self,user_id,auth_session_id=None):
        with self.lock:
            item=self.active
            if item and item['user_id']==user_id and (auth_session_id is None or item['auth_id']==auth_session_id):self._close('released','auth_revoked')

    def submit(self,user_id,pid,token,metadata,jpeg,internal=False):
        if not isinstance(metadata,dict) or set(metadata)!={'stream_epoch','frame_seq','captured_ms','width','height'}:
            raise APIError('INVALID_REQUEST','帧元数据字段不完整或含未知字段')
        seq=integer(metadata['frame_seq'],'frame_seq',0,2**53-1);stamp=integer(metadata['captured_ms'],'captured_ms',0,2**53-1)
        width=integer(metadata['width'],'width',1,1920);height=integer(metadata['height'],'height',1,1920)
        if min(width,height)>1080:raise APIError('FRAME_DIMENSIONS','提交帧短边不能超过1080',422)
        # 在解码前后都校验代次，撤销／关闭不能让旧工作继续发布结果。
        with self.lock:
            self.expire();item=self._authorize(user_id,pid,token,metadata['stream_epoch'],internal=internal)
            if item.get('recorder') and item['recorder'].failed:raise APIError('SAVE_FAILED','训练保存未确认，输入已冻结',409)
        dimensions=jpeg_dimensions(jpeg)
        if dimensions!=(width,height):raise APIError('FRAME_DIMENSIONS','元数据与 JPEG 实际尺寸不一致',422)
        digest=hashlib.sha256(jpeg+json_text(metadata).encode()).hexdigest()
        with self.lock:
            self.expire();item=self._authorize(user_id,pid,token,metadata['stream_epoch'],internal=internal);now=self.monotonic()
            if seq==item['frame_seq']:
                if digest!=item['digest']:raise ConflictError('同一帧序号内容不一致','FRAME_CONFLICT')
                return {'accepted':False,'duplicate':True,'frame_seq':seq}
            if seq<item['frame_seq'] or stamp<=item['captured_ms']:raise ConflictError('帧序号或采集时间未递增','STALE_FRAME')
            while item['submissions'] and item['submissions'][0]<=now-1:item['submissions'].popleft()
            if len(item['submissions'])>=30:raise APIError('FRAME_RATE_LIMIT','每流每秒最多提交 30 帧',429)
            replaced=item['pending'] is not None
            item.update(frame_seq=seq,captured_ms=stamp,digest=digest,lease_until=now+15,
                pending={'jpeg':jpeg,'dimensions':dimensions,'frame_seq':seq,'captured_ms':stamp})
            item['accepted']+=1;item['replaced']+=int(replaced);item['submissions'].append(now);self._wake.set()
            return {'accepted':True,'duplicate':False,'frame_seq':seq,'replaced_pending':replaced}

    def begin_upload(self,user_id,pid,token,internal=False):
        with self.lock:
            self.expire();item=self._find(user_id,pid,internal)
            self._authorize(user_id,pid,token,item['stream_epoch'],internal=internal)
            if item['uploading']:raise APIError('FRAME_IN_FLIGHT','原帧提交尚未完成，请等待',429)
            item['uploading']=True
        def release():
            with self.lock:item['uploading']=False
        return release

    def snapshot(self,user_id,pid,internal=False):
        with self.lock:
            self.expire();item=self._find(user_id,pid,internal)
            result=copy.deepcopy(item['result'])
            if result is not None and not internal:
                fresh=item['status']=='active' and item.get('last_result_at') is not None and self.monotonic()-item['last_result_at']<=1
                if item.get('recorder') and item['recorder'].failed:fresh=False
                result['skeleton']=skeleton_snapshot(result,result.get('coaching'),fresh=fresh,
                    time_base='source_ms' if result.get('recognition') else 'captured_ms')
                if not fresh and result.get('recognition'):
                    result['recognition'].update(state='searching',action_id=None,candidate_action_id=None,score=None,reason='stale_frame',message='等待新的画面')
                    result.update(AutomaticAnalysis().waiting(result,result['recognition'],'stale_frame'))
                from .framing_guide import guide
                result['framing_guide']=guide(result,item.get('action_id'),item.get('coaching_view','auto'),fresh=fresh)
            return {'preview_id':pid,'action_id':item['action_id'],'stream_epoch':item['stream_epoch'],'status':item['status'],
                'recognition_mode':item.get('recognition_mode','manual'),'coaching_view':item.get('coaching_view','auto'),'training':item['recorder'].snapshot() if item.get('recorder') else None,
                'reason':item.get('reason'),'lease_remaining_ms':max(0,int((item['lease_until']-self.monotonic())*1000)) if item is self.active else 0,
                'frame_seq':item['frame_seq'],'result':result,
                'queue':{'pending':int(item['pending'] is not None),'processing':int(self.processing_id==pid),'accepted':item['accepted'],'replaced':item['replaced'],'processed':item['processed']}}

    def _next(self):
        with self.lock:
            self.expire();item=self.active
            if not item or not item['pending']:return None
            packet=item['pending'];item['pending']=None;self.processing=1;self.processing_id=item['preview_id']
            return item,packet

    def _publish(self,item,packet,result,elapsed):
        with self.lock:
            self.processing=0;self.processing_id=None;self._flush_releases();self.expire()
            if self.active is not item:
                self.slot.release(item['user_id'],*self._slot_ref(item));return
            if not self.slot.owns(item['user_id'],*self._slot_ref(item)):return
            if item.get('recorder') and item['recorder'].failed:return
            stamp=packet['captured_ms'];recognition=None
            if item.get('recognition_mode')=='auto':
                generic=body_frame(result,stamp,*packet['dimensions'],packet['frame_seq'])
                recognition=item['recognizer'].process(generic,result.get('_visual_feature'))
                action_id=recognition['action_id']
                quality=framing(action_id,result) if action_id else generic['framing']
                if recognition.get('reason')=='target_changed':
                    quality.update(usable=False,code='TARGET_AMBIGUOUS',message='目标连续性不确定，请重新稳定取景')
                quality['recognition_only']=not bool(action_id)
            else:quality=framing(item['action_id'],result)
            if not quality['usable']:item['visible_since']=None
            elif item['visible_since'] is None or item['last_processed_ms'] is None or stamp-item['last_processed_ms']>500:item['visible_since']=stamp
            stable=stamp-item['visible_since'] if item['visible_since'] is not None else 0
            quality.update(stable_ms=stable,ready_for_tracking=quality['usable'] and stable>=500)
            item['last_processed_ms']=stamp;item['processed']+=1;item['last_result_at']=self.monotonic()
            item['result']={'frame_seq':packet['frame_seq'],'captured_ms':stamp,'width':packet['dimensions'][0],'height':packet['dimensions'][1],
                'model_version':result.get('model',MODEL_VERSION),'input_transform':result.get('input_transform'),'processing_ms':round(elapsed,2),'framing':quality}
            if result.get('pretrained') is not None:item['result']['pretrained']=result['pretrained']
            if recognition is None and item.get('recorder'):
                recognition={'available':True,'state':'recognized','action_id':item['action_id'],'model_version':'manual-selection','decision_basis':'user_selected'}
            if recognition is not None:
                item['result']['recognition']=recognition
                item['result'].update(item['automatic_analysis'].process(item['result'],recognition))
            from .framing_guide import guide
            item['result']['framing_guide']=guide(item['result'],item['result'].get('automatic_analysis',{}).get('action_id') or item.get('action_id'),item.get('coaching_view','auto'))
            # 只唤醒会话调度，不在预览锁内调用持有其他锁的消费者。
            self.result_ready.set()

    def start(self):
        if self._thread:return
        self._stop.clear()
        def run():
            engine=None;engine_id=None
            try:
                while not self._stop.is_set():
                    packet=self._next()
                    if packet is None:
                        with self.lock:active_id=self.active['preview_id'] if self.active else None
                        if engine is not None and engine_id!=active_id:engine.close();engine=None;engine_id=None
                        self._wake.wait(0.1);self._wake.clear();continue
                    item,frame=packet
                    try:
                        if engine_id!=item['preview_id']:
                            if engine:engine.close()
                            engine=None;engine=self.pose_factory(video=True);engine_id=item['preview_id']
                        began=time.perf_counter();rgb=decode_jpeg(frame['jpeg'],frame['dimensions'])
                        result=engine.detect(rgb,frame['captured_ms'])
                        result['content_bounds']=detect_content_bounds(rgb)
                        recognizer=item.get('recognizer')
                        if recognizer is not None and len(result.get('poses',[]))==1:
                            result['_visual_feature']=recognizer.extract(rgb,frame['captured_ms'])
                        recognizer=None
                        self._publish(item,frame,result,(time.perf_counter()-began)*1000)
                    except Exception as error:
                        logger.warning('预览处理失败 preview=%s type=%s',item['preview_id'],type(error).__name__)
                        with self.lock:
                            self.processing=0;self.processing_id=None;self._flush_releases()
                            if self.active is item:self._close('failed',error.code if isinstance(error,APIError) else 'POSE_UNAVAILABLE')
                            else:self.slot.release(item['user_id'],*self._slot_ref(item))
                        if engine:engine.close()
                        engine=None;engine_id=None
            finally:
                if engine:engine.close()
        self._thread=threading.Thread(target=run,name='fitness-preview',daemon=True);self._thread.start()

    def stop(self):
        with self.lock:self._close('released','service_stopped');self._stop.set();self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():raise APIError('POSE_STOPPING','姿态引擎正在释放，请稍后核对',503)
            self._thread=None
        if any(item.get('recorder') and item['recorder'].failed for item in self.history.values()):
            raise APIError('SAVE_FAILED','仍有自动训练结果尚未保存，请在实时分析页恢复或明确保留已保存内容后再停止服务',503)

    def _flush_releases(self):
        for reference in self._deferred_release:self.slot.release(*reference)
        self._deferred_release.clear()

    def adopt(self,user_id,auth_id,page_id,pid,token,session_id,action_id,commit):
        """持预览锁提交开始状态并原子移交槽；旧包与新流使用不同对象和 SDK 时间域。"""
        with self.lock:
            self.expire();old=self._find(user_id,pid)
            self._authorize(user_id,pid,token,old['stream_epoch'])
            if old.get('recognition_mode')=='auto':raise APIError('AUTO_OBSERVATION_ONLY','自动观察尚未接入训练保存；可使用手动备用入口',409)
            if old['auth_id']!=auth_id or old['page_id']!=page_id or old['action_id']!=action_id:
                raise APIError('CONTROL_CONFLICT','预览不属于当前控制页或动作',409)
            if not old['result'] or not old['result']['framing']['ready_for_tracking'] or self.monotonic()-old.get('last_result_at',-100)>0.5:
                raise APIError('FRAMING_REQUIRED','请先确认必要部位持续可见',409)
            if not self.slot.owns(user_id,pid,'preview'):raise APIError('CONTROL_CONFLICT','原预览占用已失效',409)
            result=commit()
            if result.get('save_state')=='save_failed':raise APIError('STORAGE_UNAVAILABLE','开始状态尚未保存，请处理原会话',503)
            self.slot.transfer(user_id,pid,'preview',session_id,'session')
            new={**old,'preview_id':new_id(),'session_id':session_id,'token':secrets.token_urlsafe(32),'stream_epoch':new_id(),
                 'frame_seq':-1,'captured_ms':-1,'digest':None,'pending':None,'result':None,'status':'active','reason':None,
                 'visible_since':None,'last_processed_ms':None,'last_result_at':None,'submissions':deque(),
                 'accepted':0,'replaced':0,'processed':0,'uploading':False,'lease_until':self.monotonic()+15}
            self._close('transferred','session_started');self.active=new;self._wake.set();self.start()
            return result,{'preview_id':new['preview_id'],'preview_token':new['token'],'stream_epoch':new['stream_epoch']}

    def close_session(self,user_id,session_id):
        with self.lock:
            if self.active and self.active.get('session_id')==session_id and self.active['user_id']==user_id:self._close('released','session_flow_stopped')
            elif self.slot.owns(user_id,session_id,'session'):
                if self.processing:self._deferred_release.add((user_id,session_id,'session'))
                else:self.slot.release(user_id,session_id,'session')

    def reserve_session(self,user_id,session_id):
        """休息恢复也占用唯一任务槽；上一帧未排空时不能重领同名占用。"""
        with self.lock:
            if self.processing:raise APIError('POSE_STOPPING','上一帧仍在释放，请稍后重试',409)
            self.slot.claim(user_id,session_id,'session')

    def renew_session(self,user_id,session_id):
        with self.lock:
            self.expire()
            if self.active and self.active.get('session_id')==session_id and self.active['user_id']==user_id:
                self.active['lease_until']=self.monotonic()+15
