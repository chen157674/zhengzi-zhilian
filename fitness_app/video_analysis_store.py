"""上传视频父任务与片段修订；成功保存后才切换当前结果，旧版不重复进入统计。"""
import hashlib,json,re
from sqlalchemy import select,update,delete,func
from . import db_schema as s
from .auth import APIError
from .catalogue import get_action
from .database import ConflictError,StorageError,idempotent,command_digest,json_text
from .records import uuid_id,integer,TERMINAL
from .video_segmentation import POLICY_VERSION

FINAL={'completed','cancelled','interrupted','failed'}


class VideoAnalysisStore:
    def __init__(self,sessions,inputs):
        self.sessions=sessions;self.inputs=inputs;self.db=sessions.db;self.clock=sessions.clock

    def _parent(self,c,uid,pid):
        row=c.execute(select(s.video_analyses).where(s.video_analyses.c.id==pid,s.video_analyses.c.user_id==uid)).mappings().first()
        if not row:raise APIError('NOT_FOUND','视频分析不存在',404)
        return dict(row)

    def _segment(self,c,uid,sid):
        row=c.execute(select(s.video_segments).where(s.video_segments.c.id==sid,s.video_segments.c.user_id==uid)).mappings().first()
        if not row:raise APIError('NOT_FOUND','动作片段不存在',404)
        return dict(row)

    def _revision(self,c,uid,rid):
        row=c.execute(select(s.video_revisions).where(s.video_revisions.c.id==rid,s.video_revisions.c.user_id==uid)).mappings().first()
        if not row:raise APIError('NOT_FOUND','片段修订不存在',404)
        return dict(row)

    def delete_payload(self,data):
        if not isinstance(data,dict) or set(data)-{'expected_version','expected_inventory','confirm'}:raise APIError('INVALID_REQUEST','删除字段无效',400)
        if data.get('confirm') is not True:raise APIError('VALIDATION_FAILED','请明确确认删除整次分析及全部关联记录',422)
        version=integer(data.get('expected_version'),'expected_version');inventory=data.get('expected_inventory')
        if not isinstance(inventory,str) or not re.fullmatch('[0-9a-f]{64}',inventory):raise APIError('VALIDATION_FAILED','删除范围版本无效，请重新核对',422)
        return {'expected_version':version,'expected_inventory':inventory,'confirm':True}

    def deletion_receipt(self,uid,auth,pid,payload,key):
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth)
            if c.scalar(select(s.command_receipts.c.command_id).where(s.command_receipts.c.command_id==key,s.command_receipts.c.actor_user_id==uid)) is None:return None
            if not self._command(c,uid,key,'video.analysis.delete',payload,pid):return None
            return json.loads(c.scalar(select(s.command_receipts.c.result_json).where(s.command_receipts.c.command_id==key)))

    def _deletion_inventory(self,c,uid,pid):
        parent=self._parent(c,uid,pid)
        segments=[dict(r) for r in c.execute(select(s.video_segments).where(s.video_segments.c.user_id==uid,s.video_segments.c.analysis_id==pid).order_by(s.video_segments.c.id)).mappings()]
        segment_ids=select(s.video_segments.c.id).where(s.video_segments.c.user_id==uid,s.video_segments.c.analysis_id==pid)
        revisions=[dict(r) for r in c.execute(select(s.video_revisions).where(s.video_revisions.c.user_id==uid,s.video_revisions.c.segment_id.in_(segment_ids)).order_by(s.video_revisions.c.id)).mappings()]
        linked=select(s.video_revisions.c.session_id).where(s.video_revisions.c.user_id==uid,s.video_revisions.c.segment_id.in_(segment_ids),s.video_revisions.c.session_id.is_not(None))
        sessions=s.analysis_sessions
        records=[dict(r) for r in c.execute(select(sessions,s.training_notes.c.version.label('note_version'),s.skeleton_tracks.c.state_version.label('skeleton_version'),s.skeleton_tracks.c.frame_count.label('skeleton_frames'),s.stability_runs.c.state_version.label('stability_version'))
            .outerjoin(s.training_notes,s.training_notes.c.session_id==sessions.c.id).outerjoin(s.skeleton_tracks,s.skeleton_tracks.c.id==sessions.c.id).outerjoin(s.stability_runs,s.stability_runs.c.id==sessions.c.id)
            .where(sessions.c.user_id==uid,sessions.c.id.in_(linked)|(func.json_extract(sessions.c.config_json,'$.video_analysis_id')==pid)).order_by(sessions.c.id)).mappings()]
        input_ids={parent['input_id'],*[r['input_id'] for r in revisions]}
        for record in records:
            config=json.loads(record['config_json'])
            if record['source']!='video' or config.get('video_analysis_id')!=pid:raise StorageError('片段记录与父任务归属不一致，未删除')
            if config.get('input_id'):input_ids.add(uuid_id(config['input_id']))
        media=[]
        for offset in range(0,len(input_ids),250):
            chunk=sorted(input_ids)[offset:offset+250]
            media.extend(dict(r) for r in c.execute(select(s.media_inputs).where(s.media_inputs.c.user_id==uid,s.media_inputs.c.id.in_(chunk)).order_by(s.media_inputs.c.id)).mappings())
        if len(media)!=len(input_ids):raise StorageError('关联输入登记不完整，未删除')
        if parent['status'] not in FINAL or any(r['status'] not in FINAL for r in revisions):raise APIError('ANALYSIS_BUSY','请先结束整次视频及所有片段重分析，再删除',409)
        if parent['cleanup_pending'] or any(r['cleanup_pending'] for r in revisions) or any(r['temporary_relative_path'] is not None or r['status']!='cleaned' for r in media):raise APIError('ANALYSIS_CLEANUP_REQUIRED','关联视频副本仍待清理，请先重试相应任务的清理',409)
        ids=[r['id'] for r in records];versions={r['id']:r['state_version'] for r in records}
        self.sessions._validate_record_deletion(c,uid,ids,versions,unstarted_parent=pid)
        inventory=hashlib.sha256(json_text({'parent':parent,'segments':segments,'revisions':revisions,'records':records,'media':sorted(media,key=lambda r:r['id'])}).encode()).hexdigest()
        return {'analysis_id':pid,'expected_version':parent['state_version'],'inventory_version':inventory,'segment_count':len(segments),'unknown_segment_count':sum(r['kind']=='unknown' for r in segments),
                'revision_count':len(revisions),'record_count':len(records),'current_record_count':sum(json.loads(r['config_json']).get('included_in_statistics') is True for r in records),
                'record_ids':ids,'input_ids':sorted(input_ids),'revision_ids':[r['id'] for r in revisions],'segment_ids':[r['id'] for r in segments]}

    def deletion_options(self,uid,auth,pid):
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth);return self._deletion_inventory(c,uid,pid)

    def delete_analysis(self,uid,auth,pid,data,key):
        payload=self.delete_payload(data);key=uuid_id(key)
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth)
            def write():
                scope=self._deletion_inventory(c,uid,pid)
                if scope['expected_version']!=payload['expected_version'] or scope['inventory_version']!=payload['expected_inventory']:raise ConflictError('分析或关联内容已变化，请重新核对删除范围')
                self.sessions._delete_record_rows(c,uid,scope['record_ids'])
                c.execute(delete(s.video_analyses).where(s.video_analyses.c.id==pid,s.video_analyses.c.user_id==uid))
                deleted_inputs=0
                for offset in range(0,len(scope['input_ids']),250):
                    batch=scope['input_ids'][offset:offset+250];used=set()
                    for table in (s.video_analyses,s.video_revisions,s.jobs):used.update(c.execute(select(table.c.input_id).where(table.c.input_id.in_(batch))).scalars())
                    orphan_input=func.json_extract(s.analysis_sessions.c.config_json,'$.input_id')
                    used.update(c.execute(select(orphan_input).where(s.analysis_sessions.c.user_id==uid,orphan_input.in_(batch))).scalars())
                    unused=set(batch)-used
                    if unused:deleted_inputs+=c.execute(delete(s.media_inputs).where(s.media_inputs.c.id.in_(unused),s.media_inputs.c.user_id==uid)).rowcount
                resources=[pid]+scope['segment_ids']+scope['revision_ids']
                for offset in range(0,len(resources),250):
                    c.execute(delete(s.command_receipts).where(s.command_receipts.c.actor_user_id==uid,s.command_receipts.c.resource_id.in_(resources[offset:offset+250]),s.command_receipts.c.resource_type.in_(('video.analysis.create','video.analysis.cancel','video.revision.create'))))
                self.sessions._fault('before_commit')
                return {'id':pid,'deleted':True,'deleted_ids':scope['record_ids'],'segment_count':scope['segment_count'],'revision_count':scope['revision_count'],'input_metadata_deleted':deleted_inputs,'original_device_video_unchanged':True}
            result=idempotent(c,uid,key,payload,'video.analysis.delete',write,pid)
        self.sessions._fault('after_commit')
        return result

    def command_result(self,uid,auth,key,kind,payload,resource=None):
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth)
            return self._command(c,uid,key,kind,payload,resource)

    def _command(self,c,uid,key,kind,payload,resource=None):
        row=c.execute(select(s.command_receipts).where(s.command_receipts.c.command_id==uuid_id(key))).mappings().first()
        if row:
            if row['actor_user_id']!=uid or row['request_digest']!=command_digest(kind,resource,payload):raise ConflictError('幂等标识与请求不匹配','IDEMPOTENCY_CONFLICT')
            if row['status']!='succeeded':raise ConflictError('请先查看原操作状态')
            return json.loads(row['result_json'])['id']
        return None

    def normalize(self,data):
        if not isinstance(data,dict) or set(data)-{'input_id','mode','action_id','coaching_view','save_skeleton'}:raise APIError('INVALID_REQUEST','视频分析字段无效',400)
        if type(data.get('save_skeleton',False)) is not bool:raise APIError('VALIDATION_FAILED','骨架保存选项无效',422)
        mode=data.get('mode','auto');action=data.get('action_id');view=data.get('coaching_view','auto')
        if not isinstance(mode,str) or mode not in {'auto','manual'}:raise APIError('VALIDATION_FAILED','分析模式无效',422)
        if not isinstance(view,str) or view not in {'auto','side','front','oblique'}:raise APIError('VALIDATION_FAILED','机位无效',422)
        if mode=='auto' and action is not None or mode=='manual' and (not isinstance(action,str) or not get_action(action)):
            raise APIError('VALIDATION_FAILED','自动模式不指定动作，手动模式须选择已有动作',422)
        return {'input_id':uuid_id(data.get('input_id')),'mode':mode,'action_id':action,'coaching_view':view}|({'save_skeleton':data['save_skeleton']} if 'save_skeleton' in data else {})

    def create(self,uid,auth,data,key,pid=None):
        payload=self.normalize(data);key=uuid_id(key)
        old=self.command_result(uid,auth,key,'video.analysis.create',payload)
        if old:return self.snapshot(uid,auth,old)
        pid=uuid_id(pid) if pid is not None else s.new_id()
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth)
            old=self._command(c,uid,key,'video.analysis.create',payload)
            if old:return self.snapshot(uid,auth,old)
            media=self.inputs._row(c,uid,payload['input_id']);info=json.loads(media['encoding_json'] or '{}')
            if media['status']!='ready' or media['expires_at_ms']<=self.clock():raise APIError('INPUT_UNAVAILABLE','请先选择已校验、未过期的视频',409)
            if info.get('demo_id'):raise APIError('DEMO_SOURCE_REQUIRED','请选择示例视频入口打开内置视频',422)
            if not media['sha256'] or len(media['sha256'])!=64 or not media['bytes'] or not info.get('width') or not info.get('height'):
                raise APIError('INPUT_UNAVAILABLE','视频校验信息不完整',409)
            def write():
                c.execute(s.video_analyses.insert().values(id=pid,user_id=uid,owner_auth_session_id=auth,input_id=media['id'],
                    source_sha256=media['sha256'],source_bytes=media['bytes'],duration_ms=media['duration_ms'],
                    source_width=info['width'],source_height=info['height'],mode=payload['mode'],manual_action_id=payload['action_id'],
                    config_json=json_text({'coaching_view':payload['coaching_view'],'save_skeleton':payload.get('save_skeleton',False),'input_policy':self.sessions.processing_policy,'segmentation_policy':POLICY_VERSION}),created_at_ms=self.clock()))
                c.execute(update(s.media_inputs).where(s.media_inputs.c.id==media['id']).values(status='consumed'))
                return {'id':pid}
            value=idempotent(c,uid,key,payload,'video.analysis.create',write)
        return self.snapshot(uid,auth,value['id'])

    def snapshot(self,uid,auth,pid):
        with self.db.transaction(False) as c:
            if auth:self.sessions._actor(c,uid,auth)
            row=self._parent(c,uid,uuid_id(pid));media=self.inputs._row(c,uid,row['input_id'])
            segments=c.execute(select(s.video_segments).where(s.video_segments.c.analysis_id==pid,s.video_segments.c.user_id==uid)
                .order_by(s.video_segments.c.segment_index)).mappings().all()
            revisions=c.execute(select(s.video_revisions).where(s.video_revisions.c.user_id==uid,
                s.video_revisions.c.segment_id.in_(select(s.video_segments.c.id).where(s.video_segments.c.analysis_id==pid,s.video_segments.c.user_id==uid)))
                .order_by(s.video_revisions.c.revision_index)).mappings().all()
            groups={}
            for revision in revisions:
                value={k:revision[k] for k in ('id','segment_id','revision_index','origin','action_id','session_id','previous_revision_id',
                    'status','state_version','error_code','created_at_ms','started_at_ms','ended_at_ms')}
                value['is_current']=bool(revision['is_current']);value['cleanup_pending']=bool(revision['cleanup_pending']);value['coaching_view']=json.loads(revision['config_json']).get('coaching_view','auto');groups.setdefault(revision['segment_id'],[]).append(value)
            result=[]
            for raw in segments:
                segment={k:raw[k] for k in ('id','analysis_id','segment_index','start_ms','end_ms','action_id','kind','reason','recognition_model_version','state_version')}
                segment['metadata']=json.loads(raw['metadata_json']);segment['revisions']=groups.get(raw['id'],[])
                segment['current_revision']=next((r for r in segment['revisions'] if r['is_current']),None)
                segment['effective_action_id']=(segment['current_revision'] or {}).get('action_id') or raw['action_id']
                action=get_action(segment['effective_action_id']) if segment['effective_action_id'] else None
                segment['action_name']=action['name'] if action else '未确认动作'
                segment['time_base']='source_ms';segment['interval']='half_open';result.append(segment)
            return {k:row[k] for k in ('id','input_id','mode','manual_action_id','status','phase','result_completeness','processed_ms','duration_ms',
                'state_version','recognition_model_version','error_code','source_sha256','source_bytes','source_width','source_height','created_at_ms','started_at_ms','ended_at_ms')}|{
                'analysis_id':row['id'],'segments':result,'coaching_view':json.loads(row['config_json']).get('coaching_view','auto'),'cancel_requested':bool(row['cancel_requested']),'cleanup_pending':bool(row['cleanup_pending']),
                'source_available':media['status']=='consumed' and bool(media['temporary_relative_path']),
                'new_source_must_match_original':True,'source_retention':'processing_only','independent_specific_error_accuracy':None}

    def begin(self,uid,auth,pid):
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth);row=self._parent(c,uid,pid)
            if row['status']!='queued' or row['cancel_requested']:raise ConflictError('视频任务已经开始或已取消')
            config=json.loads(row['config_json']);self.sessions.require_input_policy(config)
            c.execute(update(s.video_analyses).where(s.video_analyses.c.id==pid).values(status='running',phase='recognizing',started_at_ms=self.clock(),state_version=row['state_version']+1))

    def progress(self,uid,pid,phase,processed_ms,model_version=None):
        if phase not in {'recognizing','posture','finalizing'}:raise ValueError('处理阶段无效')
        integer(processed_ms,'processed_ms',0,1800000)
        with self.db.transaction() as c:
            row=self._parent(c,uid,pid)
            if row['status']!='running' or row['cancel_requested']:raise APIError('ANALYSIS_CANCELLED','分析已停止',409)
            if phase==row['phase'] and processed_ms<row['processed_ms']:raise ValueError('请核对当前处理进度')
            c.execute(update(s.video_analyses).where(s.video_analyses.c.id==pid).values(phase=phase,processed_ms=processed_ms,
                recognition_model_version=model_version or row['recognition_model_version'],state_version=row['state_version']+1))

    def register_segments(self,uid,auth,pid,segments,duration):
        integer(duration,'duration_ms',1,1800000)
        if not isinstance(segments,list) or not segments:raise ValueError('必须保留完整源范围，包括未知')
        expected=0
        for index,item in enumerate(segments,1):
            if item['segment_index']!=index or item['start_ms']!=expected or not expected<item['end_ms']<=duration:raise ValueError('片段必须连续覆盖源范围')
            if item.get('action_id') is not None and not get_action(item['action_id']):raise ValueError('动作类别无效')
            expected=item['end_ms']
        if expected!=duration:raise ValueError('片段未覆盖完整源范围')
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth);parent=self._parent(c,uid,pid)
            if parent['status']!='running' or parent['cancel_requested']:raise APIError('ANALYSIS_CANCELLED','分析已停止',409)
            if c.scalar(select(func.count()).select_from(s.video_segments).where(s.video_segments.c.analysis_id==pid)):
                raise ConflictError('片段已保存，不重复建立')
            for item in segments:
                metadata={k:item[k] for k in ('policy_version','decision_basis','unassessable_ranges','recognition_unassessable_ranges') if k in item}
                c.execute(s.video_segments.insert().values(id=s.new_id(),user_id=uid,analysis_id=pid,segment_index=item['segment_index'],start_ms=item['start_ms'],end_ms=item['end_ms'],
                    action_id=item['action_id'],kind='action' if item['action_id'] else 'unknown',reason=item.get('reason'),
                    recognition_model_version=item.get('model_version'),metadata_json=json_text(metadata)))
            c.execute(update(s.video_analyses).where(s.video_analyses.c.id==pid).values(duration_ms=duration,phase='posture',processed_ms=0,
                result_completeness='partial',state_version=parent['state_version']+1))
        return self.snapshot(uid,auth,pid)['segments']

    def revision_payload(self,data,automatic=False):
        if not isinstance(data,dict) or set(data)-{'action_id','input_id','expected_version','coaching_view','save_skeleton'}:raise APIError('INVALID_REQUEST','修订字段无效',400)
        action=data.get('action_id')
        if not isinstance(action,str) or not get_action(action):raise APIError('VALIDATION_FAILED','请选择已有动作',422)
        payload={'action_id':action,'input_id':uuid_id(data.get('input_id')),'expected_version':integer(data.get('expected_version'),'expected_version'),'automatic':automatic}
        if 'save_skeleton' in data:
            if type(data['save_skeleton']) is not bool:raise APIError('VALIDATION_FAILED','骨架保存选项无效',422)
            payload['save_skeleton']=data['save_skeleton']
        if 'coaching_view' in data:
            if not isinstance(data['coaching_view'],str) or data['coaching_view'] not in {'auto','front','side','oblique'}:raise APIError('VALIDATION_FAILED','拍摄机位无效',422)
            payload['coaching_view']=data['coaching_view']
        return payload

    def queue_revision(self,uid,auth,segment_id,data,key,automatic=False):
        payload=self.revision_payload(data,automatic);action=payload['action_id']
        segment_id=uuid_id(segment_id);key=uuid_id(key)
        old=self.command_result(uid,auth,key,'video.revision.create',payload,segment_id)
        if old:return old
        rid=s.new_id()
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth)
            old=self._command(c,uid,key,'video.revision.create',payload,segment_id)
            if old:return old
            segment=self._segment(c,uid,segment_id);parent=self._parent(c,uid,segment['analysis_id'])
            if segment['state_version']!=payload['expected_version']:raise ConflictError('片段已经改变，请重新读取')
            if automatic:
                if parent['status']!='running' or parent['cancel_requested'] or action!=segment['action_id'] or payload['input_id']!=parent['input_id']:raise ConflictError('自动分析已停止或片段不匹配')
            elif parent['status'] not in FINAL:raise APIError('ANALYSIS_BUSY','请等待整段视频分析结束后再修改片段',409)
            active=c.scalar(select(func.count()).select_from(s.video_revisions).where(s.video_revisions.c.segment_id==segment_id,s.video_revisions.c.status.not_in(FINAL)))
            if active:raise APIError('ANALYSIS_BUSY','该片段已有重分析任务',409)
            media=self.inputs._row(c,uid,payload['input_id'])
            if media['sha256']!=parent['source_sha256'] or media['bytes']!=parent['source_bytes']:raise APIError('SOURCE_MISMATCH','请重新选择与原视频内容一致的文件',422)
            if media['status']!='ready' and not (automatic and media['status']=='consumed' and media['id']==parent['input_id']):raise APIError('INPUT_UNAVAILABLE','请重新选择已校验的原视频',409)
            if not automatic and media['expires_at_ms']<=self.clock():raise APIError('INPUT_UNAVAILABLE','所选输入已过期',409)
            previous=c.execute(select(s.video_revisions).where(s.video_revisions.c.segment_id==segment_id,s.video_revisions.c.is_current==1)).mappings().first()
            index=c.scalar(select(func.coalesce(func.max(s.video_revisions.c.revision_index),0)).where(s.video_revisions.c.segment_id==segment_id))+1
            config=json.loads(parent['config_json'])
            if 'coaching_view' in payload:config={**config,'coaching_view':payload['coaching_view']}
            if not automatic:config={**config,'save_skeleton':payload.get('save_skeleton',False)}
            def write():
                c.execute(s.video_revisions.insert().values(id=rid,user_id=uid,segment_id=segment_id,input_id=media['id'],owner_auth_session_id=auth,
                    revision_index=index,origin='auto' if automatic and parent['mode']=='auto' else 'manual',action_id=action,previous_revision_id=previous['id'] if previous else None,
                    config_json=json_text({**config,'parent_pass':automatic,'source_start_ms':segment['start_ms'],'source_end_ms':segment['end_ms']}),created_at_ms=self.clock()))
                c.execute(update(s.video_segments).where(s.video_segments.c.id==segment_id).values(state_version=segment['state_version']+1))
                c.execute(update(s.media_inputs).where(s.media_inputs.c.id==media['id']).values(status='consumed'))
                return {'id':rid}
            result=idempotent(c,uid,key,payload,'video.revision.create',write,segment_id)
        return result['id']

    def child(self,uid,auth,rid):
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth);revision=self._revision(c,uid,rid);segment=self._segment(c,uid,revision['segment_id'])
            if revision['session_id']:return self.sessions.snapshot(uid,revision['session_id'])
            config=json.loads(revision['config_json'])
        action=get_action(revision['action_id'])
        child_config={'action_id':action['action_id'],'capability_version':action['capability_version'],'source':'video','mode':'free','target':None,
            'input_id':revision['input_id'],'coaching_view':config['coaching_view'],'save_skeleton':config.get('save_skeleton',False),'included_in_statistics':False,'video_revision_id':rid,
            'video_analysis_id':segment['analysis_id'],'video_segment_id':segment['id'],'source_start_ms':segment['start_ms'],'source_end_ms':segment['end_ms'],
            'exercise_basis':'automatically_recognized' if revision['origin']=='auto' else 'user_selected'}
        data=self.sessions.create(uid,auth,child_config,rid,video_revision_id=rid,stability=True)
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth);revision=self._revision(c,uid,rid)
            if revision['status'] not in {'queued','running'}:raise ConflictError('片段重算已停止')
            c.execute(update(s.video_revisions).where(s.video_revisions.c.id==rid).values(session_id=data['session_id'],status='running',started_at_ms=self.clock(),state_version=revision['state_version']+1))
        return data

    def promote(self,uid,auth,rid):
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth);revision=self._revision(c,uid,rid);segment=self._segment(c,uid,revision['segment_id'])
            if revision['status']=='completed' and revision['is_current']:return revision['session_id']
            if revision['status']!='running' or not revision['session_id']:raise ConflictError('正在处理本次分析')
            parent=self._parent(c,uid,segment['analysis_id'])
            if json.loads(revision['config_json']).get('parent_pass') and (parent['status']!='running' or parent['cancel_requested']):raise APIError('ANALYSIS_CANCELLED','取消已生效，未发布迟到结果',409)
            child=self.sessions._session(c,uid,revision['session_id']);config=json.loads(child['config_json'])
            if child['status']!='completed' or config.get('video_revision_id')!=rid or config.get('included_in_statistics') is not False:raise ConflictError('请重试保存新结果')
            current=c.execute(select(s.video_revisions).where(s.video_revisions.c.segment_id==segment['id'],s.video_revisions.c.is_current==1)).mappings().first()
            if (current['id'] if current else None)!=revision['previous_revision_id']:raise ConflictError('原有效修订已改变，未替换记录')
            if current:
                old=self.sessions._session(c,uid,current['session_id']);old_config=json.loads(old['config_json'])
                from .training_allocations import invalidate_records
                invalidate_records(c,uid,[old['id']])
                if old_config.get('included_in_statistics') is not True:raise StorageError('当前修订与统计标记不一致')
                c.execute(update(s.video_revisions).where(s.video_revisions.c.id==current['id']).values(is_current=0,state_version=current['state_version']+1))
                c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==old['id']).values(
                    config_json=json_text({**old_config,'included_in_statistics':False,'superseded_by':child['id']}),state_version=old['state_version']+1))
            c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==child['id']).values(config_json=json_text({**config,'included_in_statistics':True}),state_version=child['state_version']+1))
            c.execute(update(s.video_revisions).where(s.video_revisions.c.id==rid).values(status='completed',is_current=1,error_code=None,ended_at_ms=self.clock(),state_version=revision['state_version']+1))
            c.execute(update(s.video_segments).where(s.video_segments.c.id==segment['id']).values(state_version=segment['state_version']+1))
            return child['id']

    def fail_revision(self,uid,rid,status,error):
        if status not in {'failed','cancelled','interrupted'}:raise ValueError('修订失败状态无效')
        with self.db.transaction() as c:
            revision=self._revision(c,uid,rid)
            if revision['status'] in FINAL:return
            c.execute(update(s.video_revisions).where(s.video_revisions.c.id==rid).values(status=status,error_code=error,ended_at_ms=self.clock(),state_version=revision['state_version']+1))
            c.execute(update(s.video_segments).where(s.video_segments.c.id==revision['segment_id']).values(state_version=s.video_segments.c.state_version+1))

    def finish(self,uid,pid,status,error=None,cleanup_pending=False):
        if status not in FINAL:raise ValueError('视频终态无效')
        with self.db.transaction() as c:
            row=self._parent(c,uid,pid)
            if row['status'] in FINAL:return
            pending=c.scalar(select(func.count()).select_from(s.video_revisions).where(s.video_revisions.c.user_id==uid,
                s.video_revisions.c.segment_id.in_(select(s.video_segments.c.id).where(s.video_segments.c.analysis_id==pid)),s.video_revisions.c.status.not_in(FINAL)))
            if status=='completed' and (pending or row['cancel_requested']):raise ConflictError('仍有片段未完成或取消已生效')
            if status=='completed':
                segments=c.execute(select(s.video_segments.c.id,s.video_segments.c.kind).where(s.video_segments.c.analysis_id==pid)).mappings().all()
                current=set(c.execute(select(s.video_revisions.c.segment_id).where(s.video_revisions.c.user_id==uid,s.video_revisions.c.is_current==1)).scalars())
                if row['status']!='running' or not segments or any(x['kind']=='action' and x['id'] not in current for x in segments):
                    raise ConflictError('请先保存全部动作片段的结果')
            c.execute(update(s.video_analyses).where(s.video_analyses.c.id==pid).values(status=status,phase='completed' if status=='completed' else row['phase'],
                error_code=error,cleanup_pending=int(cleanup_pending),result_completeness='full' if status=='completed' else row['result_completeness'],
                ended_at_ms=self.clock(),state_version=row['state_version']+1))

    def cancel(self,uid,auth,pid,version,key):
        integer(version,'expected_version');payload={'expected_version':version}
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth);row=self._parent(c,uid,uuid_id(pid))
            def write():
                if row['status'] in FINAL:return {'id':pid}
                if row['state_version']!=version:raise ConflictError('视频任务已更新，请重新读取')
                c.execute(update(s.video_analyses).where(s.video_analyses.c.id==pid).values(cancel_requested=1,state_version=version+1))
                return {'id':pid}
            idempotent(c,uid,uuid_id(key),payload,'video.analysis.cancel',write,pid)
        return self.snapshot(uid,auth,pid)

    def recover(self):
        with self.db.transaction() as c:
            # 恢复崩溃发生在“创建隐藏子记录→关联修订”之间的明确归属，不重新计算。
            orphans=c.execute(select(s.analysis_sessions.c.id,s.analysis_sessions.c.user_id,s.analysis_sessions.c.config_json)
                .where(func.json_extract(s.analysis_sessions.c.config_json,'$.video_revision_id').is_not(None))).mappings()
            for child in orphans:
                rid=json.loads(child['config_json'])['video_revision_id']
                c.execute(update(s.video_revisions).where(s.video_revisions.c.id==rid,s.video_revisions.c.user_id==child['user_id'],s.video_revisions.c.session_id.is_(None)).values(session_id=child['id']))
                revision=c.execute(select(s.video_revisions.c.status).where(s.video_revisions.c.id==rid,s.video_revisions.c.user_id==child['user_id'])).scalar()
                if revision and revision!='completed':
                    c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==child['id'],s.analysis_sessions.c.status.not_in(TERMINAL))
                        .values(status='interrupted',end_reason='process_restart',ended_at_ms=self.clock(),state_version=s.analysis_sessions.c.state_version+1))
                    status=c.scalar(select(s.analysis_sessions.c.status).where(s.analysis_sessions.c.id==child['id']))
                    if status not in {'completed','ended_early'}:c.execute(update(s.session_sets).where(s.session_sets.c.session_id==child['id'],s.session_sets.c.status.in_({'pending','active'})).values(status='ended_early'))
            for table in (s.video_revisions,s.video_analyses):
                c.execute(update(table).where(table.c.status.not_in(FINAL)).values(status='interrupted',error_code='SERVICE_RESTARTED',ended_at_ms=self.clock(),state_version=table.c.state_version+1))
