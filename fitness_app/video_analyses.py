"""自动上传视频/单段重分析执行器；同步识别、复用姿态、事务发布，不重放任务。"""
from bisect import bisect_left
from dataclasses import dataclass,field
import json,logging,threading,time
from sqlalchemy import select,update
from . import db_schema as s
from .auth import APIError
from .database import StorageError,ConflictError
from .records import TERMINAL,integer,uuid_id
from .video_analysis_store import VideoAnalysisStore,FINAL
from .video_segmentation import segment_decisions,POLICY_VERSION
from .offline_video import scan_video
from .semantic_recognition import shared_predictor
from .semantic_model import resources
from .gym_pose import GymPoseService
from .media import video_frames
from .posture_engine import create_analysis_engine
from .posture_rules import rules_for
from .sessions import IdentityContinuity
from .pose import model_status

logger=logging.getLogger(__name__)


@dataclass
class Run:
    id:str
    user_id:str
    auth_id:str
    parent_id:str
    input_id:str
    slot_id:str
    kind:str
    stop:threading.Event=field(default_factory=threading.Event)
    thread:object=None
    reason:str|None=None
    current_revision:str|None=None
    stopped:bool=False
    final_status:str|None=None
    error_code:str|None=None
    finalization_pending:bool=False
    cleanup_pending:bool=False
    skeleton_buffer:object=None
    stability_capture:object=None


class VideoAnalyses:
    def __init__(self,app):
        self.app=app;self.sessions=app.extensions['session_store'];self.writer=app.extensions['persistence']
        self.inputs=app.extensions['media_inputs'];self.slot=app.extensions['vision_slot'];self.db=self.sessions.db
        self.store=VideoAnalysisStore(self.sessions,self.inputs);self.lock=threading.RLock();self.runs={};self.closed=False
        testing=app.config.get('TESTING')
        self.pose_factory=app.config.get('PREVIEW_POSE_FACTORY',GymPoseService) if testing else GymPoseService
        self.predictor_provider=app.config.get('VIDEO_ANALYSIS_PREDICTOR',shared_predictor) if testing else shared_predictor
        self.frames=app.config.get('VIDEO_ANALYSIS_FRAMES',video_frames) if testing else video_frames
        self.engine_provider=app.config.get('SESSION_ENGINE_PROVIDER',create_analysis_engine) if testing else create_analysis_engine

    def _available(self):
        if self.closed:raise APIError('SERVICE_STOPPED','服务正在停止',409)
        if self.writer.unhealthy:raise APIError('STORAGE_UNAVAILABLE','请先处理未保存的分析结果',503)
        if any(run.finalization_pending for run in self.runs.values()):raise APIError('STORAGE_UNAVAILABLE','请先查看原视频任务的保存状态',503)
        for key,run in list(self.runs.items()):
            if run.stopped and not run.finalization_pending and run.thread and not run.thread.is_alive():self.runs.pop(key,None)
        if len(self.runs)>=64:raise APIError('ANALYSIS_BUSY','尚有任务未释放，请稍后再试',409)

    def start(self,uid,auth,data,key):
        payload=self.store.normalize(data);key=uuid_id(key)
        with self.lock:
            old=self.store.command_result(uid,auth,key,'video.analysis.create',payload)
            if old:return self.snapshot(uid,auth,old)
            self._available()
            if not self.app.config.get('TESTING') and not model_status()['available']:raise APIError('MODEL_UNAVAILABLE','请准备姿态模型后开始分析',503)
            if payload['mode']=='auto' and not self.app.config.get('TESTING') and resources()[0] is None:
                raise APIError('RECOGNITION_UNAVAILABLE','自动识别资源暂不可用，可主动改用手动分析',503)
            pid=s.new_id();self.slot.claim(uid,pid,'video_analysis');created=False
            try:
                self.store.create(uid,auth,payload,key,pid);created=True
                run=Run(pid,uid,auth,pid,payload['input_id'],pid,'analysis');self.runs[pid]=run
                run.thread=threading.Thread(target=self._work,args=(run,),name='fitness-upload-analysis',daemon=True);run.thread.start()
            except Exception:
                self.slot.release(uid,pid,'video_analysis')
                if created:self.store.finish(uid,pid,'failed','TASK_NOT_STARTED',True)
                raise
            return self.snapshot(uid,auth,pid)

    def reanalyse(self,uid,auth,segment_id,data,key):
        segment_id=uuid_id(segment_id);key=uuid_id(key);payload=self.store.revision_payload(data)
        with self.lock:
            old=self.store.command_result(uid,auth,key,'video.revision.create',payload,segment_id)
            if old:return self.revision(uid,auth,old)
            self._available();self.slot.claim(uid,key,'video_revision');rid=None
            try:
                rid=self.store.queue_revision(uid,auth,segment_id,data,key)
                with self.db.transaction(False) as c:segment=self.store._segment(c,uid,segment_id)
                run=Run(rid,uid,auth,segment['analysis_id'],payload['input_id'],key,'revision',current_revision=rid);self.runs[rid]=run
                run.thread=threading.Thread(target=self._work,args=(run,),name='fitness-segment-reanalysis',daemon=True);run.thread.start()
            except Exception:
                self.slot.release(uid,key,'video_revision')
                if rid:self.store.fail_revision(uid,rid,'failed','TASK_NOT_STARTED')
                raise
            return self.revision(uid,auth,rid)

    def snapshot(self,uid,auth,pid):
        with self.lock:
            # 数据库终态和线程终态必须在同一锁内读取，避免收尾期间将旧的running误报为保存失败。
            data=self.store.snapshot(uid,auth,uuid_id(pid))
            run=self.runs.get(pid);data['processing']=bool(run and not run.stopped)
            data['terminal_persisted']=data['status'] in FINAL;data['stored_status']=data['status']
            data['finalization_pending']=bool(run and run.finalization_pending)
            if run and run.stopped and not data['terminal_persisted']:data['status']='failed';data['error_code']='TASK_STATE_NOT_SAVED'
            for segment in data['segments']:
                for revision in segment['revisions']:
                    rt=self.runs.get(revision['id']);revision['finalization_pending']=bool(rt and rt.finalization_pending)
                    revision['processing']=bool(rt and not rt.stopped)
                    if rt and rt.stopped and revision['status'] not in FINAL:
                        revision['stored_status']=revision['status'];revision['status']='failed';revision['error_code']='TASK_STATE_NOT_SAVED'
        data['can_reanalyse']=data['terminal_persisted'] and not data['finalization_pending']
        data['save_failed_records']=[]
        if self.writer.unhealthy:
            for segment in data['segments']:
                for revision in segment['revisions']:
                    sid=revision['session_id']
                    if sid and self.writer.snapshot(uid,sid)['save_state']!='clean':data['save_failed_records'].append(sid)
        if data['save_failed_records'] or any(r.get('finalization_pending') for segment in data['segments'] for r in segment['revisions']):data['can_reanalyse']=False
        data['can_delete']=bool(data['can_reanalyse'] and not data['processing'] and not data['cleanup_pending'] and not data['source_available'] and all(r['status'] in FINAL and not r['cleanup_pending'] and not r['processing'] for segment in data['segments'] for r in segment['revisions']))
        return data

    def revision(self,uid,auth,rid):
        with self.lock:return self._revision_snapshot(uid,auth,rid)

    def _revision_snapshot(self,uid,auth,rid):
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth);row=self.store._revision(c,uid,uuid_id(rid));segment=self.store._segment(c,uid,row['segment_id'])
        result={k:row[k] for k in ('id','segment_id','revision_index','origin','action_id','session_id','status','state_version','error_code','created_at_ms','started_at_ms','ended_at_ms')}|{
            'revision_id':row['id'],'analysis_id':segment['analysis_id'],'is_current':bool(row['is_current']),'cleanup_pending':bool(row['cleanup_pending']),
            'source_start_ms':segment['start_ms'],'source_end_ms':segment['end_ms'],
            'session':self.writer.snapshot(uid,row['session_id']) if row['session_id'] else None}
        with self.lock:
            run=self.runs.get(rid);result['terminal_persisted']=row['status'] in FINAL;result['stored_status']=row['status'];result['finalization_pending']=bool(run and run.finalization_pending)
            if run and run.stopped and not result['terminal_persisted']:result['status']='failed';result['error_code']='TASK_STATE_NOT_SAVED'
        return result

    def _check(self,run):
        if run.stop.is_set():raise APIError('ANALYSIS_CANCELLED','视频处理已停止',409)
        with self.db.transaction(False) as c:
            self.sessions._actor(c,run.user_id,run.auth_id)
            if run.kind=='analysis':
                row=self.store._parent(c,run.user_id,run.parent_id)
                if row['cancel_requested'] or row['status'] not in {'queued','running'}:raise APIError('ANALYSIS_CANCELLED','视频任务已停止',409)
            else:
                revision=self.store._revision(c,run.user_id,run.id)
                if revision['status'] not in {'queued','running'}:raise APIError('ANALYSIS_CANCELLED','片段重算已停止',409)

    def _info(self,run):
        with self.db.transaction(False) as c:
            row=self.inputs._row(c,run.user_id,run.input_id)
            info=json.loads(row['encoding_json'] or '{}');info['duration_ms']=row['duration_ms']
        return info

    def _scan(self,run,automatic,start_ms=0,end_ms=None):
        progress=(lambda stamp,model:self.store.progress(run.user_id,run.parent_id,'recognizing',stamp,model)) if run.kind=='analysis' else (lambda stamp,model:None)
        return scan_video(self.inputs._path(run.input_id),self._info(run),automatic=automatic,start_ms=start_ms,end_ms=end_ms,
            pose_factory=self.pose_factory,predictor_provider=self.predictor_provider,frames=self.frames,
            check=lambda:self._check(run),cancelled=run.stop.is_set,progress=progress)

    def _saved(self,run,sid,operation,payload):
        self._check(run)
        data=self.writer.submit(run.user_id,sid,run.auth_id,operation,payload)
        if data['save_state']!='clean':raise StorageError('请先保存当前结果批次')
        return data

    @staticmethod
    def _merge_ranges(items):
        result=[]
        for item in sorted(items,key=lambda x:(x['start_ms'],x['end_ms'])):
            if result and item['start_ms']<=result[-1][1]:result[-1]=(result[-1][0],max(result[-1][1],item['end_ms']))
            else:result.append((item['start_ms'],item['end_ms']))
        return result

    def _posture(self,run,rid,segment,rows,stamps):
        run.current_revision=rid;self._check(run);child=self.store.child(run.user_id,run.auth_id,rid);sid=child['session_id'];action=child['action_id']
        adapter=self.engine_provider(action)
        if adapter is None:raise APIError('ACTION_UNAVAILABLE','该动作尚无可用的姿态分析器',409)
        config=child['config'];active=0;previous=None;identity=IdentityContinuity();began=None;missing_reason=None
        from .stability_store import StabilityCapture
        run.stability_capture=StabilityCapture(self.app.extensions['stability_store'],run.user_id,action,sid,segment['start_ms'])
        if config.get('save_skeleton'):
            from .skeleton_store import SkeletonBuffer
            run.skeleton_buffer=SkeletonBuffer(self.app.extensions['skeleton_store'],run.user_id,sid,segment['start_ms'])
        metadata=segment.get('metadata',{});masks=self._merge_ranges(metadata.get('unassessable_ranges',[])+metadata.get('recognition_unassessable_ranges',[]));mask_index=0
        left=bisect_left(stamps,segment['start_ms']);right=bisect_left(stamps,segment['end_ms']);checkpoint=time.monotonic()
        with self.lock:self._saved(run,sid,'state',{'state':'running','command_id':s.new_id(),'reason':'video_segment_started'})

        def unknown(end):
            nonlocal began,missing_reason
            if began is None or end<=began:return
            findings=[{'code':rule['code'],'status':'unassessable','reason':missing_reason,'side':'none','start_ms':began,'end_ms':end,
                'rule_version':rule['rule_version'],'evidence_level':'reference','body_parts':rule['body_parts'],
                'message':'此范围缺少可评估的姿态证据','suggestion':'先检查动作与取景，再进行可控制的动作。',
                'evidence':{'time_base':'source_ms','exercise_basis':config['exercise_basis'],'parameter_source':rule['parameter_source']}}
                for rule in rules_for(action)]
            if findings:self._saved(run,sid,'posture',{'set_index':1,'findings':findings,'active_ms':active})
            began=missing_reason=None

        for index in range(left,right):
            self._check(run);row=rows[index];frame=row.frame(action,index-left)
            while mask_index<len(masks) and masks[mask_index][1]<=row.stamp:mask_index+=1
            masked=mask_index<len(masks) and masks[mask_index][0]<=row.stamp<masks[mask_index][1]
            good=frame['framing']['usable'] and row.identity_accepted and identity.accept(frame) and not masked
            with self.lock:
                self._check(run)
                if not good:
                    run.stability_capture.push({**frame,'identity_accepted':False},{},1,rid)
                    if run.skeleton_buffer:run.skeleton_buffer.push({**frame,'identity_accepted':False})
                    if began is None:began=row.stamp;missing_reason='action_unconfirmed' if masked else 'required_parts_or_identity_missing'
                    previous=None;identity.reset();adapter.reset('tracking_lost');continue
                unknown(row.stamp)
                if previous is not None and 0<row.stamp-previous<=500:active+=row.stamp-previous
                previous=row.stamp
                output=adapter.process(frame,{'active_ms':active,'session_id':sid,'stream_epoch':rid,'set_index':1,'time_base':'source_ms',
                    'coaching_view':config['coaching_view'],'exercise_basis':config['exercise_basis']})
                groups=[output.get('posture_findings',[]),output.get('observation',{}).get('posture',{}).get('items',[])]+[e.get('assessments',[]) for e in output.get('events',[])]
                for group in groups:
                    for item in group:item.setdefault('evidence',{}).update(exercise_basis=config['exercise_basis'])
                data=self.writer.snapshot(run.user_id,sid)
                if output.get('posture_findings'):data=self._saved(run,sid,'posture',{'set_index':1,'findings':output['posture_findings'],'active_ms':active})
                for event in output.get('events',[]):data=self._saved(run,sid,'event',{**event,'event_seq':data['last_event_seq']+1,'set_index':1,'active_ms':active})
                if output.get('intervals'):self._saved(run,sid,'intervals',{'set_index':1,'intervals':output['intervals'],'active_ms':active})
                run.stability_capture.push(frame,output,1,rid)
                if run.skeleton_buffer:
                    from .coaching import committed_issues
                    issues=committed_issues(output)
                    run.skeleton_buffer.push(frame,{'state':'needs_adjustment','primary_cue':issues[0]} if issues else None)
                if run.kind=='analysis' and time.monotonic()-checkpoint>=1:
                    self.store.progress(run.user_id,run.parent_id,'posture',row.stamp);checkpoint=time.monotonic()
        with self.lock:
            unknown(segment['end_ms']);self._check(run)
            if run.skeleton_buffer:run.skeleton_buffer.close();run.skeleton_buffer=None
            if run.stability_capture:run.stability_capture.close();run.stability_capture=None
            self._saved(run,sid,'state',{'state':'completed','command_id':s.new_id(),'reason':'video_segment_completed','extra':{'active_ms':active}})
            self.store.promote(run.user_id,run.auth_id,rid)
        run.current_revision=None

    def _work(self,run):
        scan=None;outcome='completed';error=None
        try:
            if run.kind=='analysis':
                self.store.begin(run.user_id,run.auth_id,run.parent_id)
                with self.db.transaction(False) as c:parent=self.store._parent(c,run.user_id,run.parent_id)
                scan=self._scan(run,parent['mode']=='auto');self._check(run)
                if parent['mode']=='auto':segments=segment_decisions(scan.decisions,scan.end_ms,scan.unassessable)
                else:segments=[{'segment_index':1,'start_ms':0,'end_ms':scan.end_ms,'action_id':parent['manual_action_id'],'model_version':'manual-selection',
                    'policy_version':POLICY_VERSION,'decision_basis':'user_selected','unassessable_ranges':scan.unassessable,'recognition_unassessable_ranges':[]}]
                segments=self.store.register_segments(run.user_id,run.auth_id,run.parent_id,segments,scan.end_ms);stamps=[r.stamp for r in scan.rows]
                for segment in segments:
                    self._check(run)
                    if segment['action_id']:
                        rid=self.store.queue_revision(run.user_id,run.auth_id,segment['id'],{'action_id':segment['action_id'],'input_id':run.input_id,'expected_version':segment['state_version']},s.new_id(),True)
                        self._posture(run,rid,segment,scan.rows,stamps)
                    self.store.progress(run.user_id,run.parent_id,'posture',segment['end_ms'])
                self.store.progress(run.user_id,run.parent_id,'finalizing',scan.end_ms)
            else:
                with self.db.transaction(False) as c:
                    revision=self.store._revision(c,run.user_id,run.id);segment=self.store._segment(c,run.user_id,revision['segment_id'])
                scan=self._scan(run,False,segment['start_ms'],segment['end_ms'])
                segment['metadata']={'unassessable_ranges':scan.unassessable,'recognition_unassessable_ranges':[]}
                self._posture(run,run.id,segment,scan.rows,[r.stamp for r in scan.rows])
        except APIError as failure:
            error=failure.code;outcome='cancelled' if failure.code=='ANALYSIS_CANCELLED' and run.reason not in {'auth_revoked','service_stopped'} else 'interrupted' if failure.code=='AUTH_REQUIRED' or run.reason in {'auth_revoked','service_stopped'} else 'failed'
        except (StorageError,OSError):error='SAVE_OR_STORAGE_FAILED';outcome='failed'
        except Exception as failure:
            error='VIDEO_ANALYSIS_FAILED';outcome='failed';logger.error('上传视频处理失败 id=%s type=%s',run.id,type(failure).__name__)
        finally:
            if run.stability_capture:run.stability_capture.close(interrupted=True);run.stability_capture=None
            if run.skeleton_buffer:
                run.skeleton_buffer.close(interrupted=True);run.skeleton_buffer=None
            if scan is not None:scan.clear()
            with self.lock:
                if run.stop.is_set() and outcome=='completed':outcome='cancelled' if run.reason=='cancelled' else 'interrupted';error=run.reason or 'ANALYSIS_CANCELLED'
                run.final_status=outcome;run.error_code=error
                try:
                    if run.current_revision:self._failed_child(run,outcome if outcome!='completed' else 'failed',error or 'ANALYSIS_INTERRUPTED')
                except Exception as failure:
                    run.finalization_pending=True;logger.error('片段终态待恢复 id=%s type=%s',run.id,type(failure).__name__)
                cleanup=False
                try:self.inputs.cleanup(run.user_id,run.input_id,allow_consumed=True)
                except Exception as failure:cleanup=True;logger.error('输入副本清理待处理 id=%s type=%s',run.id,type(failure).__name__)
                run.cleanup_pending=cleanup
                try:
                    if run.kind=='analysis':self.store.finish(run.user_id,run.parent_id,outcome,error,cleanup)
                    else:
                        if outcome!='completed':self.store.fail_revision(run.user_id,run.id,outcome,error)
                        with self.db.transaction() as c:c.execute(update(s.video_revisions).where(s.video_revisions.c.id==run.id,s.video_revisions.c.user_id==run.user_id).values(cleanup_pending=int(cleanup)))
                except Exception as failure:
                    run.finalization_pending=True;logger.error('上传任务终态待恢复 id=%s type=%s',run.id,type(failure).__name__)
                run.stopped=True;self.slot.release(run.user_id,run.slot_id,'video_analysis' if run.kind=='analysis' else 'video_revision')

    def _failed_child(self,run,status,error):
        with self.db.transaction(False) as c:revision=self.store._revision(c,run.user_id,run.current_revision)
        if revision['session_id']:
            data=self.writer.snapshot(run.user_id,revision['session_id'])
            if data['save_state']=='clean' and data['status'] not in TERMINAL:
                system=run.reason in {'auth_revoked','service_stopped'} or error=='AUTH_REQUIRED'
                reason=run.reason or ('auth_revoked' if error=='AUTH_REQUIRED' else error)
                self.writer.submit(run.user_id,revision['session_id'],run.auth_id,'state',{'state':'interrupted' if system else 'ended_early' if status=='cancelled' else 'failed',
                    'command_id':s.new_id(),'reason':reason,'system':system})
        self.store.fail_revision(run.user_id,revision['id'],status if status in {'failed','cancelled','interrupted'} else 'failed',error)

    def cancel(self,uid,auth,pid,version,key):
        with self.lock:
            data=self.store.cancel(uid,auth,uuid_id(pid),version,key);run=self.runs.get(pid)
            if run and not run.stopped:run.reason='cancelled';run.stop.set()
            elif run and run.finalization_pending and data['status'] not in FINAL:run.final_status='cancelled';run.error_code='ANALYSIS_CANCELLED'
            return data

    def cancel_revision(self,uid,auth,rid,version):
        integer(version,'expected_version')
        with self.lock,self.db.transaction() as c:
            self.sessions._actor(c,uid,auth);row=self.store._revision(c,uid,uuid_id(rid))
            if row['status'] not in FINAL:
                if row['state_version']!=version:raise ConflictError('重分析状态已更新，请重新读取')
                c.execute(update(s.video_revisions).where(s.video_revisions.c.id==rid).values(status='cancelling',state_version=version+1))
                run=self.runs.get(rid)
                if run:run.reason='cancelled';run.stop.set()
        return self.revision(uid,auth,rid)

    def cleanup(self,uid,auth,identity,revision=False):
        with self.lock,self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth);row=self.store._revision(c,uid,identity) if revision else self.store._parent(c,uid,identity)
            if row['status'] not in FINAL:raise APIError('ANALYSIS_BUSY','请等待分析结束后清理视频',409)
            if revision and json.loads(row['config_json']).get('parent_pass'):
                segment=self.store._segment(c,uid,row['segment_id']);parent=self.store._parent(c,uid,segment['analysis_id'])
                if parent['status'] not in FINAL:raise APIError('ANALYSIS_BUSY','视频分析进行中',409)
            iid=row['input_id']
        self.inputs.cleanup(uid,iid,allow_consumed=True)
        with self.db.transaction() as c:
            table=s.video_revisions if revision else s.video_analyses
            c.execute(update(table).where(table.c.id==identity,table.c.user_id==uid).values(cleanup_pending=0))
        return {'cleaned':True}

    def _deletion_idle(self,uid,pid):
        for run in self.runs.values():
            if run.user_id==uid and run.parent_id==pid and (not run.stopped or run.finalization_pending or (run.thread and run.thread.is_alive())):
                raise APIError('ANALYSIS_BUSY','关联视频任务仍在处理或收尾，请先恢复并等待结束',409)

    def deletion_options(self,uid,auth,pid):
        with self.lock:
            scope=self.store.deletion_options(uid,auth,uuid_id(pid));self._deletion_idle(uid,pid)
            with self.writer.deletion_guard(uid,scope['record_ids']):return scope

    def delete(self,uid,auth,pid,data,key):
        pid=uuid_id(pid);key=uuid_id(key);payload=self.store.delete_payload(data)
        with self.lock:
            result=self.store.deletion_receipt(uid,auth,pid,payload,key)
            if result is None:
                scope=self.deletion_options(uid,auth,pid)
                with self.writer.deletion_guard(uid,scope['record_ids']):result=self.store.delete_analysis(uid,auth,pid,payload,key)
            for identity,run in list(self.runs.items()):
                if run.user_id==uid and run.parent_id==pid:self.runs.pop(identity,None)
        self.app.extensions['record_deletion_complete'](uid,result['deleted_ids'])
        return result

    def finalize(self,uid,auth,identity,version,revision=False):
        integer(version,'expected_version')
        with self.lock:
            with self.db.transaction(False) as c:
                self.sessions._actor(c,uid,auth)
                row=self.store._revision(c,uid,identity) if revision else self.store._parent(c,uid,identity)
            run=self.runs.get(identity)
            if not run or not run.finalization_pending:
                if row['status'] not in FINAL:raise APIError('ANALYSIS_BUSY','任务正在处理，请等待保存完成',409)
                return self.revision(uid,auth,identity) if revision else self.snapshot(uid,auth,identity)
            if not run.stopped:raise APIError('ANALYSIS_BUSY','工作线程仍在释放，请稍后核对',409)
            if row['state_version']!=version:raise ConflictError('任务状态已更新，请重新读取')
            run.auth_id=auth
            if run.current_revision:self._failed_child(run,run.final_status if run.final_status!='completed' else 'failed',run.error_code or 'ANALYSIS_INTERRUPTED')
            cleanup=False
            try:self.inputs.cleanup(uid,run.input_id,allow_consumed=True)
            except (OSError,StorageError):cleanup=True
            if revision:
                if run.final_status!='completed':self.store.fail_revision(uid,identity,run.final_status,run.error_code)
                with self.db.transaction() as c:c.execute(update(s.video_revisions).where(s.video_revisions.c.id==identity,s.video_revisions.c.user_id==uid).values(cleanup_pending=int(cleanup)))
            else:self.store.finish(uid,identity,run.final_status,run.error_code,cleanup)
            run.cleanup_pending=cleanup;run.finalization_pending=False
            return self.revision(uid,auth,identity) if revision else self.snapshot(uid,auth,identity)

    def recover(self):
        self.store.recover()
        with self.db.transaction() as c:
            cleaned=select(s.media_inputs.c.id).where(s.media_inputs.c.status=='cleaned',s.media_inputs.c.temporary_relative_path.is_(None))
            for table in (s.video_analyses,s.video_revisions):c.execute(update(table).where(table.c.input_id.in_(cleaned)).values(cleanup_pending=0))

    def revoke(self,user_id,auth_session_id=None):
        with self.lock:
            for run in self.runs.values():
                if run.user_id==user_id and (auth_session_id is None or run.auth_id==auth_session_id) and not run.stopped:run.reason='auth_revoked';run.stop.set()

    def stop(self):
        with self.lock:
            self.closed=True;runs=list(self.runs.values())
            for run in runs:
                if not run.stopped:run.reason='service_stopped';run.stop.set()
        for run in runs:
            if run.thread:run.thread.join(2)
        if any(run.thread and run.thread.is_alive() for run in runs):raise StorageError('视频模型仍在释放，请稍后再停止')
        if any(run.finalization_pending for run in runs):raise StorageError('还有未保存的视频任务终态，请先重试保存任务状态')
        if self.writer.unhealthy:raise StorageError('还有未保存的片段，请先处理保存状态')
