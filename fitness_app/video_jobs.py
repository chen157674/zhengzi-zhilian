"""单活动离线执行器：源时间推进、取消栅栏与原批次保存。"""
from dataclasses import dataclass,field
import json
import logging
import threading
import time

from sqlalchemy import select,update

from . import db_schema as s
from .auth import APIError
from .database import ConflictError,StorageError,idempotent,command_digest
from .frame_pipeline import analysis_framing as framing
from .content_bounds import detect_content_bounds
from .media import video_frames
from .persistence import STORAGE_FAILURES
from .gym_pose import GymPoseService as ContentPoseService
from .records import TERMINAL,integer,uuid_id
from .sessions import IdentityContinuity
from .coaching import compact_frame,committed_issues,coaching_snapshot,coaching_observation
from .skeleton import skeleton_snapshot

FINAL=TERMINAL|{'cancelled'}
MAX_RETAINED_RUNS=64
logger=logging.getLogger(__name__)


@dataclass
class VideoRun:
    job_id:str
    user_id:str
    auth_id:str
    session_id:str
    input_id:str
    info:dict
    adapter:object
    stop:threading.Event=field(default_factory=threading.Event)
    thread:object=None
    reason:str|None=None
    processed_ms:int=0
    frames:int=0
    active_ms:int=0
    began:float=field(default_factory=time.monotonic)
    stopped:bool=False
    cleanup_error:bool=False
    final_status:str|None=None
    error_code:str|None=None
    latest_status_frame:dict|None=None
    latest_issues:list=field(default_factory=list)
    latest_issue_ms:int|None=None
    latest_skeleton_frame:dict|None=None
    counting_available:bool=False

    def __post_init__(self):
        self.counting_available=getattr(self.adapter,'counting_available',self.adapter is not None)


class VideoJobs:
    def __init__(self,store,writer,inputs,slot,engine_provider=None,allow_observer=False,pose_factory=ContentPoseService,frames=video_frames):
        self.store=store;self.db=store.db;self.writer=writer;self.inputs=inputs;self.slot=slot
        self.engine_provider=engine_provider;self.allow_observer=allow_observer;self.pose_factory=pose_factory;self.frames=frames
        self.lock=threading.RLock();self.runs={};self.closed=False

    def _row(self,c,uid,jid):
        row=c.execute(select(s.jobs).where(s.jobs.c.id==jid,s.jobs.c.owner_id==uid,s.jobs.c.kind=='video')).mappings().first()
        if not row:raise APIError('NOT_FOUND','视频任务不存在',404)
        return row

    def _prune_finished(self):
        """新任务前淘汰最旧安全终态；待保存／待清理／活线程不能被内存限额抹掉。"""
        if len(self.runs)<MAX_RETAINED_RUNS:return
        for jid,rt in list(self.runs.items()):
            if len(self.runs)<MAX_RETAINED_RUNS:break
            if not rt.stopped or rt.cleanup_error or rt.thread is None or rt.thread.is_alive():continue
            with self.db.transaction(False) as c:
                row=c.execute(select(s.jobs.c.status,s.analysis_sessions.c.status.label('session_status'))
                    .join(s.analysis_sessions,s.jobs.c.session_id==s.analysis_sessions.c.id)
                    .where(s.jobs.c.id==jid,s.jobs.c.owner_id==rt.user_id)).mappings().first()
            if row is None:
                self.runs.pop(jid,None);continue  # 仅移除已删除记录的无活动内存壳。
            if row['status'] not in FINAL or row['session_status'] not in TERMINAL:continue
            saved=self.writer.snapshot(rt.user_id,rt.session_id)
            if saved['save_state']!='clean' or saved.get('storage_reads_unavailable'):continue
            self.runs.pop(jid,None)

    def snapshot(self,uid,jid):
        with self.lock:
            return self._snapshot(uid,jid)

    def _snapshot(self,uid,jid):
        with self.db.transaction(False) as c:row=dict(self._row(c,uid,jid))
        data=self.writer.snapshot(uid,row['session_id']);rt=self.runs.get(jid)
        coaching=coaching_snapshot(data,rt.latest_status_frame if rt else None,rt.latest_issues if rt else [],
                rt.processed_ms-rt.latest_issue_ms if rt and rt.latest_issue_ms is not None else 0)
        skeleton=skeleton_snapshot(rt.latest_skeleton_frame if rt else None,coaching,time_base='source_ms',
                unavailable_reason=None if rt and not rt.stopped and row['status']=='running' else 'video_not_running')
        return {'job_id':jid,'session_id':row['session_id'],'status':rt.final_status if rt and rt.stopped and rt.final_status else row['status'],'state_version':row['state_version'],
            'processed_ms':rt.processed_ms if rt else row['processed_ms'],'processed_frames':rt.frames if rt else None,'total_ms':row['total_ms'],
            'processing_elapsed_ms':round((time.monotonic()-rt.began)*1000) if rt and not rt.stopped else (row['ended_at_ms']-row['started_at_ms'] if row['ended_at_ms'] and row['started_at_ms'] else None),
            'error_code':rt.error_code if rt and rt.error_code else row['error_code'],'stopped':rt.stopped if rt else row['status'] in FINAL,
            'terminal_persisted':row['status'] in FINAL,
            'cleanup_pending':bool(rt and rt.cleanup_error),'save_state':data['save_state'],'session':data,
            'result_completeness':'full' if row['status']=='completed' else 'partial' if data['started_at_ms'] is not None else 'none',
            'counting_available':rt.counting_available if rt else bool(data.get('counting_available')),'skeleton_available':False,
            'coaching':coaching,'skeleton':skeleton,'live_skeleton_available':skeleton['available'],
            'skeleton_reason':'本次仅保留摘要和事件，未保留逐帧骨架；回看不会重新推理。'}

    def start(self,uid,auth_id,sid,version,key):
        integer(version,'expected_version');uuid_id(key)
        with self.lock:
            if self.closed:raise APIError('INVALID_STATE','服务正在停止',409)
            payload={'session_id':sid,'expected_version':version}
            with self.db.transaction(False) as c:
                self.store._actor(c,uid,auth_id);row=self.store._session(c,uid,sid)
                previous=c.execute(select(s.command_receipts).where(s.command_receipts.c.command_id==key)).mappings().first()
                if previous:
                    if previous['actor_user_id']!=uid or previous['request_digest']!=command_digest('job.video',sid,payload):raise ConflictError('幂等标识与请求不匹配','IDEMPOTENCY_CONFLICT')
                    return self.snapshot(uid,json.loads(previous['result_json'])['id'])
                if row['state_version']!=version:raise ConflictError('会话已更新，请重新读取')
                if row['source'] not in {'video','demo'} or row['status']!='ready':raise APIError('INVALID_STATE','只有已就绪视频会话可以开始',409)
                from .catalogue import get_action
                definition=get_action(row['action_id'])
                if not definition or definition['capability_version']!=row['capability_version']:raise APIError('STALE_CAPABILITY','动作能力已变化，请重新建立视频会话；原记录保留',409)
                config=json.loads(row['config_json']);self.store.require_input_policy(config)
                input_id=config['input_id'];media=self.inputs._row(c,uid,input_id)
                if media['status']!='ready' or media['expires_at_ms']<=self.store.clock():raise APIError('INVALID_STATE','视频已过期、已移除或已被使用，请重新选择',409)
                info=json.loads(media['encoding_json']);action=row['action_id'];info['action_id']=action;info['coaching_view']=config.get('coaching_view','auto')
            if self.writer.unhealthy:raise APIError('STORAGE_UNAVAILABLE','请先处理原会话保存故障',503)
            self._prune_finished()
            adapter=self.engine_provider(action) if self.engine_provider else None
            if adapter is None and not self.allow_observer:raise APIError('ACTION_UNAVAILABLE','请选择已配置分析的动作',409)
            jid=s.new_id();self.slot.claim(uid,jid,'video')
            try:
                with self.db.transaction() as c:
                    self.store._actor(c,uid,auth_id)
                    fresh=self.store._session(c,uid,sid);media=self.inputs._row(c,uid,input_id)
                    if fresh['state_version']!=version or fresh['status']!='ready' or media['status']!='ready':raise ConflictError('视频状态已改变，请重新读取')
                    def write():
                        c.execute(s.jobs.insert().values(id=jid,owner_id=uid,kind='video',session_id=sid,input_id=input_id,status='queued',processed_ms=0,total_ms=media['duration_ms']))
                        c.execute(update(s.media_inputs).where(s.media_inputs.c.id==input_id).values(status='consumed'))
                        return {'id':jid}
                    idempotent(c,uid,key,payload,'job.video',write,sid)
                rt=VideoRun(jid,uid,auth_id,sid,input_id,info,adapter);self.runs[jid]=rt
                rt.thread=threading.Thread(target=self._work,args=(rt,),name='fitness-video',daemon=True);rt.thread.start()
            except Exception:self.slot.release(uid,jid,'video');raise
            return self.snapshot(uid,jid)

    def _update(self,rt,**changes):
        with self.db.transaction() as c:
            c.execute(update(s.jobs).where(s.jobs.c.id==rt.job_id).values(**changes,state_version=s.jobs.c.state_version+1))

    def _state(self,rt,state,reason,system=False):
        return self.writer.submit(rt.user_id,rt.session_id,rt.auth_id,'state',{'state':state,'command_id':s.new_id(),
            'reason':reason,'extra':{'active_ms':rt.active_ms},'system':system})

    def _work(self,rt):
        model=None;iterator=None;outcome='completed';failure=None;previous=None;last_save=time.monotonic();identity=IdentityContinuity()
        try:
            with self.lock:
                if rt.stop.is_set():outcome='cancelled'
                else:
                    data=self._state(rt,'running','video_started')
                    if data['save_state']!='clean':raise StorageError('初始状态待保存')
                    self._update(rt,status='running',started_at_ms=self.store.clock())
            if not rt.stop.is_set():
                model=self.pose_factory(video=True);iterator=self.frames(self.inputs._path(rt.input_id),rt.info)
                for stamp,rgb in iterator:
                    if rt.stop.is_set():break
                    output=model.detect(rgb,stamp)
                    output['content_bounds']=detect_content_bounds(rgb)
                    frame={'captured_ms':stamp,'frame_seq':rt.frames,'width':int(rgb.shape[1]),'height':int(rgb.shape[0]),'framing':framing(rt.info['action_id'],output),'model_version':output.get('model')}
                    if output.get('pretrained') is not None:frame['pretrained']=output['pretrained']
                    # 取消请求与提交共用短锁；推理不占锁，迟到结果不能越过取消边界。
                    with self.lock:
                        if rt.stop.is_set():break
                        with self.db.transaction(False) as c:self.store._actor(c,rt.user_id,rt.auth_id)
                        usable=frame['framing']['usable'] and identity.accept(frame)
                        if usable:
                            if previous is not None and stamp-previous>500:rt.latest_issues=[];rt.latest_issue_ms=None
                            if previous is not None and 0<stamp-previous<=500:rt.active_ms+=stamp-previous
                            previous=stamp
                            produced=rt.adapter.process(frame,{'active_ms':rt.active_ms,'session_id':rt.session_id,'set_index':1,'time_base':'source_ms','coaching_view':rt.info.get('coaching_view','auto')}) if rt.adapter else {}
                        else:
                            if frame['framing']['usable']:
                                frame['framing'].update(usable=False,ready_for_tracking=False,code='TARGET_AMBIGUOUS',message='目标连续性不确定，请原训练者回到取景位置')
                            previous=None
                            if rt.adapter:rt.adapter.reset('tracking_lost')
                            produced={}
                        rt.latest_status_frame=compact_frame(frame,coaching_observation(produced))
                        rt.latest_skeleton_frame=frame
                        # 处理进度与确认保存量分别呈现；本帧已推理但写入失败时也不倒退处理进度。
                        rt.processed_ms=stamp;rt.frames+=1
                        data=self.writer.snapshot(rt.user_id,rt.session_id)
                        if produced.get('posture_findings'):
                            data=self.writer.submit(rt.user_id,rt.session_id,rt.auth_id,'posture',{'set_index':1,'findings':produced['posture_findings'],'active_ms':rt.active_ms})
                            if data['save_state']!='clean':raise StorageError('姿态结果待保存')
                        for event in produced.get('events',[]):
                            data=self.writer.submit(rt.user_id,rt.session_id,rt.auth_id,'event',{**event,'event_seq':data['last_event_seq']+1,'set_index':1,'active_ms':rt.active_ms})
                            if data['save_state']!='clean':raise StorageError('动作事件待保存')
                        if produced.get('intervals'):
                            data=self.writer.submit(rt.user_id,rt.session_id,rt.auth_id,'intervals',{'set_index':1,'intervals':produced['intervals'],'active_ms':rt.active_ms})
                            if data['save_state']!='clean':raise StorageError('时间区间尚未保存')
                        issues=committed_issues(produced)
                        if issues:rt.latest_issues=issues;rt.latest_issue_ms=stamp
                        elif not usable or produced.get('posture_findings') or produced.get('events'):rt.latest_issues=[];rt.latest_issue_ms=None
                        if time.monotonic()-last_save>=1:
                            self._update(rt,processed_ms=rt.processed_ms);last_save=time.monotonic()
                            data=self._state(rt,'running','video_checkpoint')
                            if data['save_state']!='clean':raise StorageError('处理进度尚未保存')
                if rt.stop.is_set():outcome='cancelled' if rt.reason=='cancelled' else 'interrupted'
        except APIError as error:
            failure=error.code;outcome='interrupted' if error.code=='AUTH_REQUIRED' else 'failed'
            if error.code=='AUTH_REQUIRED':rt.reason='auth_revoked'
        except STORAGE_FAILURES:failure='STORAGE_UNAVAILABLE';outcome='failed'
        except Exception as error:
            failure='VIDEO_PROCESSING_FAILED';outcome='failed';logger.error('视频处理失败 job=%s type=%s',rt.job_id,type(error).__name__)
        finally:
            for resource,kind in ((iterator,'decoder'),(model,'pose')):
                try:
                    if resource is not None and hasattr(resource,'close'):resource.close()
                except Exception as error:
                    failure=failure or 'VIDEO_RELEASE_FAILED';outcome='failed'
                    logger.error('视频资源释放失败 job=%s resource=%s type=%s',rt.job_id,kind,type(error).__name__)
            with self.lock:
                # 分析器持有最近周期骨架；终止后只留摘要，重试写入使用writer原批次。
                rt.latest_skeleton_frame=None;rt.latest_status_frame=None
                rt.latest_issues=[];rt.latest_issue_ms=None;rt.adapter=None
                if rt.stop.is_set() and outcome=='completed':outcome='cancelled' if rt.reason=='cancelled' else 'interrupted'
                try:
                    data=self.writer.snapshot(rt.user_id,rt.session_id)
                    if data['save_state']=='clean':
                        state='ended_early' if outcome=='cancelled' else outcome
                        reason=rt.reason or failure or 'video_completed'
                        data=self._state(rt,state,reason,system=outcome=='interrupted' and reason in {'auth_revoked','auth_expired','service_stopped'})
                    if data['save_state']!='clean':failure='SAVE_FAILED';outcome='failed'
                    self._update(rt,status=outcome,processed_ms=rt.processed_ms,error_code=failure,ended_at_ms=self.store.clock())
                except Exception as error:
                    logger.error('视频终态尚未保存 job=%s type=%s',rt.job_id,type(error).__name__)
                rt.stopped=True;rt.final_status=outcome;rt.error_code=failure
                try:self.inputs.cleanup(rt.user_id,rt.input_id,allow_consumed=True)
                except Exception as error:rt.cleanup_error=True;logger.error('视频临时副本待清理 input=%s type=%s',rt.input_id,type(error).__name__)
                self.slot.release(rt.user_id,rt.job_id,'video')

    def cancel(self,uid,auth_id,jid,version,key):
        integer(version,'expected_version')
        with self.lock:
            with self.db.transaction() as c:
                row=self._row(c,uid,jid);self.store._actor(c,uid,auth_id)
                def write():
                    if row['status'] in FINAL:return {'id':jid}
                    if row['state_version']!=version:raise ConflictError('任务状态已变化，请重新读取')
                    rt=self.runs.get(jid)
                    if rt:rt.reason='cancelled';rt.stop.set()
                    c.execute(update(s.jobs).where(s.jobs.c.id==jid).values(status='cancelling',state_version=s.jobs.c.state_version+1))
                    return {'id':jid}
                idempotent(c,uid,uuid_id(key),{'expected_version':version},'job.cancel',write,jid)
            return self.snapshot(uid,jid)

    def revoke(self,user_id,auth_session_id=None):
        with self.lock:
            for rt in self.runs.values():
                if rt.user_id==user_id and (auth_session_id is None or rt.auth_id==auth_session_id) and not rt.stopped:
                    rt.reason='auth_revoked';rt.stop.set()

    def recovery(self,uid,auth_id,sid,key,payload):
        with self.lock:
            rt=next((rt for rt in self.runs.values() if rt.session_id==sid and rt.user_id==uid),None)
            if rt and not rt.stopped:raise APIError('INVALID_STATE','原任务仍在停止，请稍后核对',409)
            data=self.writer.recovery_command(uid,sid,auth_id,key,payload)
            if data['save_state']=='clean' and data['status'] not in TERMINAL:
                data=self.writer.submit(uid,sid,auth_id,'state',{'state':'interrupted','command_id':s.new_id(),'reason':'video_save_recovered'})
            if rt and rt.cleanup_error:
                self.inputs.cleanup(uid,rt.input_id,allow_consumed=True);rt.cleanup_error=False
            if rt and data['save_state']=='clean':self._update(rt,status=rt.final_status or 'interrupted',processed_ms=rt.processed_ms,error_code=rt.error_code,ended_at_ms=self.store.clock())
            return data

    def stop(self):
        with self.lock:
            self.closed=True;runs=list(self.runs.values())
            for rt in runs:
                if not rt.stopped:rt.reason='service_stopped';rt.stop.set()
        for rt in runs:rt.thread.join(timeout=2)
        if any(rt.thread.is_alive() for rt in runs):raise StorageError('视频仍在停止，请稍后再试')
        if self.writer.unhealthy:raise StorageError('仍有未保存视频结果，请先重试或放弃')
