"""本人选定记录的只读PDF快照与删除/版本依赖；不生成AI答复。"""
import hashlib,json
from collections import defaultdict
from sqlalchemy import select,func
from . import db_schema as s
from .auth import APIError
from .records import uuid_id,integer,TERMINAL
from .database import ConflictError,json_text
from .training_insights import compact_record,record_statistics,filters,history_header_query
from .ai_context import redact,PROMPT_VERSION
from .posture_rules import policy_version

# 外观改版参与新导出去重，不重写或撤销旧报告。
VERSION='training-pdf-5'
MAX_RECORDS=20
MAX_RAW_ROWS=20000
MAX_EPISODES=1000
MAX_SNAPSHOT_BYTES=2*1024*1024
NOTE_FIELDS=('load_value','load_unit','load_basis','difficulty','note')


def normalize(payload):
    if not isinstance(payload,dict) or set(payload)-{'record_ids','expected_versions','include_user_notes','include_ai_report','ai_report_ids','format'}:raise APIError('INVALID_REQUEST','导出字段无效',400)
    ids=payload.get('record_ids');versions=payload.get('expected_versions')
    if not isinstance(ids,list) or not 1<=len(ids)<=MAX_RECORDS:raise APIError('VALIDATION_FAILED','请选择1至20条记录',422)
    ids=sorted(uuid_id(v) for v in ids)
    if not isinstance(versions,dict):raise APIError('VALIDATION_FAILED','记录或版本字段无效',422)
    canonical_versions={uuid_id(k):v for k,v in versions.items()}
    if len(ids)!=len(set(ids)) or len(canonical_versions)!=len(versions) or set(canonical_versions)!=set(ids):raise APIError('VALIDATION_FAILED','记录或版本字段无效',422)
    versions=canonical_versions
    versions={sid:integer(versions[sid],'expected_version') for sid in ids}
    notes=payload.get('include_user_notes',False);ai=payload.get('include_ai_report',False);reports=payload.get('ai_report_ids',[])
    if type(notes) is not bool or type(ai) is not bool or not isinstance(reports,list) or len(reports)>3:raise APIError('VALIDATION_FAILED','报告包含选项无效',422)
    reports=sorted(uuid_id(v) for v in reports)
    if len(set(reports))!=len(reports) or bool(reports)!=ai:raise APIError('VALIDATION_FAILED','请明确选择要包含的AI报告',422)
    if payload.get('format','pdf')!='pdf':raise APIError('VALIDATION_FAILED','只支持PDF格式',422)
    return {'record_ids':ids,'expected_versions':versions,'include_user_notes':notes,'include_ai_report':ai,'ai_report_ids':reports,'format':'pdf'}


def episodes(data):
    groups=defaultdict(list)
    def add(item,side):
        if item.get('status') not in {'issue','unassessable'}:return
        start,end=item.get('start_ms'),item.get('end_ms')
        if type(start) is not int or type(end) is not int or end<=start:return
        evidence=item.get('evidence') or {};base=evidence.get('time_base') or ('source_ms' if data['config'].get('video_revision_id') else 'session_elapsed_ms')
        key=(item.get('code','unknown'),side,item['status'],item.get('rule_version','legacy-unknown'),evidence.get('view','unknown'),base,item.get('reason'),item.get('message') or '',item.get('suggestion') or '',tuple(sorted(evidence.get('affected_sides',[]))) if item['status']=='issue' else ())
        groups[key].append((start,end))
    for item in data.get('posture_observations',[]):add(item,item.get('side','none'))
    for event in data.get('events',[]):
        for item in event.get('assessments',[]):add(item,event.get('side','none'))
    result=[]
    for key,parts in groups.items():
        merged=[]
        for start,end in sorted(parts):
            if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
            else:merged.append((start,end))
        for start,end in merged:result.append(dict(zip(('code','side','status','rule_version','view','time_base','reason','message','suggestion','affected_sides'),key))|{'start_ms':start,'end_ms':end})
    return sorted(result,key=lambda v:(v['start_ms'],v['code'],v['side']))


class ExportSnapshots:
    def __init__(self,app):
        self.app=app;self.sessions=app.extensions['session_store'];self.db=self.sessions.db;self.info=app.extensions['training_info'];self.clock=self.sessions.clock
    def _history_ids(self,c,uid,reference):
        if not reference.get('history_fingerprint'):return []
        conditions=filters(uid,reference.get('history_filters'),reference.get('as_of_ms'))
        return list(c.execute(select(s.analysis_sessions.c.id).where(*conditions).limit(MAX_RECORDS+1)).scalars())
    def ai_valid(self,c,uid,entry,allowed):
        row=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==entry['id'],s.ai_requests.c.user_id==uid)).mappings().first()
        if not row or row['kind'] not in {'review','comparison'} or row['status']!='succeeded' or not row['result_json']:return False
        if row['state_version']!=entry['version'] or hashlib.sha256(row['result_json'].encode()).hexdigest()!=entry['sha256']:return False
        ref=json.loads(row['context_json'])
        if ref.get('kind') not in {'record','comparison','overview'} or not set(ref.get('record_ids',[]))<=allowed or ref.get('prompt_version')!=PROMPT_VERSION or ref.get('rules_version')!=policy_version():return False
        history_ids=set(self._history_ids(c,uid,ref))
        if not (set(ref.get('record_ids',[]))|history_ids) or not history_ids<=allowed:return False
        if (c.scalar(select(s.training_profiles.c.version).where(s.training_profiles.c.user_id==uid)) or 0)!=ref['profile_version']:return False
        if (c.scalar(select(s.ai_settings.c.version).where(s.ai_settings.c.user_id==uid)) or 0)!=ref['settings_version']:return False
        for dep in ref['dependencies']:
            source=c.execute(select(s.analysis_sessions).where(s.analysis_sessions.c.id==dep['id'],s.analysis_sessions.c.user_id==uid)).mappings().first()
            if not source or source['state_version']!=dep['version'] or source['action_id']!=dep['action_id']:return False
            if dep.get('kind')!='retest_source' and dep['id'] not in allowed:return False
            if (c.scalar(select(s.training_notes.c.version).where(s.training_notes.c.session_id==dep['id'],s.training_notes.c.user_id==uid)) or 0)!=dep['note_version']:return False
        if ref.get('history_fingerprint'):
            digest=hashlib.sha256()
            for raw in c.execute(history_header_query(filters(uid,ref.get('history_filters'),ref.get('as_of_ms')))).mappings():digest.update(json_text({k:v for k,v in dict(raw).items() if k!='note_json'}).encode())
            if digest.hexdigest()!=ref['history_fingerprint']:return False
        for dep in ref.get('skeleton_dependencies',[]):
            track=c.execute(select(s.skeleton_tracks).where(s.skeleton_tracks.c.id==dep['id'],s.skeleton_tracks.c.user_id==uid)).mappings().first()
            if not track or track['status'] in {'deleted','expired'} or track['expires_at_ms']<=self.clock() or track['frame_count']<dep['frame_count'] or track['visible_frame_count']<dep['visible_frame_count']:return False
        return True
    def ai_options(self,uid,auth,ids):
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth);rows=c.execute(select(s.ai_requests).where(s.ai_requests.c.user_id==uid,s.ai_requests.c.kind.in_(('review','comparison')),s.ai_requests.c.status=='succeeded',s.ai_requests.c.result_json.is_not(None)).order_by(s.ai_requests.c.created_at_ms.desc()).limit(50)).mappings().all();values=[]
            for row in rows:
                ref=json.loads(row['context_json'])
                entry={'id':row['id'],'version':row['state_version'],'sha256':hashlib.sha256(row['result_json'].encode()).hexdigest()}
                if self.ai_valid(c,uid,entry,set(ids)):
                    values.append({'id':row['id'],'kind':row['kind'],'model':row['model'],'created_at_ms':row['created_at_ms']})
                if len(values)>=10:break
            return values
    def options(self,uid,auth,ids):
        if not isinstance(ids,list) or not 1<=len(ids)<=MAX_RECORDS:raise APIError('VALIDATION_FAILED','请选择1至20条记录',422)
        ids=[uuid_id(v) for v in ids]
        if len(set(ids))!=len(ids):raise APIError('VALIDATION_FAILED','记录重复',422)
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth);records=[]
            for sid in ids:
                row=self.sessions._session(c,uid,sid);config=json.loads(row['config_json'])
                if row['status'] not in TERMINAL or row['started_at_ms'] is None or row['source']=='demo' or config.get('included_in_statistics',True) is not True:raise APIError('EXPORT_SOURCE_UNAVAILABLE','请选择已训练并结束的本人当前有效记录，演示或旧修订不能新导出',409)
                definition=json.loads(c.scalar(select(s.action_capabilities.c.definition_json).where(s.action_capabilities.c.action_id==row['action_id'],s.action_capabilities.c.capability_version==row['capability_version'])))
                records.append({'id':sid,'version':row['state_version'],'action_id':row['action_id'],'action_name':definition.get('name',row['action_id']),'started_at_ms':row['started_at_ms']})
        return {'records':records,'expected_versions':{v['id']:v['version'] for v in records},'ai_reports':self.ai_options(uid,auth,ids),'retention_hours':24,'export_does_not_call_ai':True}
    def freeze(self,uid,auth,payload):
        ids=payload['record_ids'];deps={'records':[],'notes':[],'auxiliary':[],'movement':[],'ai':[]};content={'policy_version':VERSION,'records':[],'ai_reports':[],'include_user_notes':payload['include_user_notes']}
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth);total=sum(c.scalar(select(func.count()).select_from(table).where(table.c.session_id.in_(ids))) or 0 for table in (s.action_events,s.posture_observations,s.observation_intervals))
            if total>MAX_RAW_ROWS:raise APIError('EXPORT_TOO_LARGE','所选训练明细较多，请减少记录范围',422)
        episode_count=0
        for sid in ids:
            data=self.sessions.snapshot(uid,sid,True)
            if data['state_version']!=payload['expected_versions'][sid]:raise ConflictError('记录已更新，请重新核对导出范围')
            if data['status'] not in TERMINAL or data['started_at_ms'] is None or data['source']=='demo' or not data['included_in_statistics']:raise APIError('EXPORT_SOURCE_UNAVAILABLE','只能新导出已训练、已结束的本人当前有效记录',409)
            note=self.info.get_note(uid,auth,sid) if payload['include_user_notes'] else None
            value=compact_record(data,note);value['statistics']=record_statistics(data);value['episodes']=episodes(data);value['supported_items']=data['supported_items'];value['end_reason']=data['end_reason'];value['source_sha256']=data['source_sha256'];value['recognition_model_version']=data['config'].get('recognition_model_version')
            value['completion'].update(left_count=data['left_count'] if data['counting_available'] else None,right_count=data['right_count'] if data['counting_available'] else None)
            value['processing_policy_id']=hashlib.sha256(json_text(data.get('input_policy') or {}).encode()).hexdigest()[:16]
            value['retest']=self.app.extensions['retests'].summary(uid,data);value['movement_stability']=self.app.extensions['stability_store'].safe_snapshot(uid,sid)
            value['revision']=None
            with self.db.transaction(False) as c:
                revision=c.execute(select(s.video_revisions).where(s.video_revisions.c.session_id==sid,s.video_revisions.c.user_id==uid)).mappings().first()
                if revision:value['revision']={k:revision[k] for k in ('revision_index','origin','is_current')}
                if value['retest'] and value['retest']['state']!='source_unavailable':
                    original=self.sessions._session(c,uid,value['retest']['source_record_id']);deps['auxiliary'].append({'id':original['id'],'version':original['state_version']})
            episode_count+=len(value['episodes']);content['records'].append(value);deps['records'].append({'id':sid,'version':data['state_version']})
            deps['movement'].append({'id':sid,'version':value['movement_stability']['state_version']})
            if note:deps['notes'].append({'id':sid,'version':note['version'],'had_values':any(note.get(k) is not None for k in NOTE_FIELDS),'present_fields':[k for k in NOTE_FIELDS if note.get(k) is not None]})
        if episode_count>MAX_EPISODES:raise APIError('EXPORT_TOO_LARGE','报告问题时段较多，请减少记录范围',422)
        content['records'].sort(key=lambda v:(v['started_at_ms'] or 0,v['session_id']))
        if len(ids)==2:
            comparison=self.app.extensions['training_insights'].comparison(uid,auth,[v['session_id'] for v in content['records']],payload['expected_versions'],include_notes=False)
            content['comparison']={k:comparison[k] for k in ('comparable','reason','comparisons','evidence_level','independent_specific_error_accuracy')}
        else:content['comparison']=None
        for rid in payload['ai_report_ids']:
            with self.db.transaction(False) as c:
                row=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==rid,s.ai_requests.c.user_id==uid)).mappings().first()
                if not row:raise APIError('NOT_FOUND','AI报告不存在',404)
                entry={'id':rid,'version':row['state_version'],'sha256':hashlib.sha256((row['result_json'] or '').encode()).hexdigest()}
                if not self.ai_valid(c,uid,entry,set(ids)):raise APIError('EXPORT_AI_SCOPE','AI报告已过期或含未选定的历史记录，请选择范围匹配的当前报告',409)
                from .ai_context import report_display
                answer=report_display(json.loads(row['result_json']));content['ai_reports'].append({k:row[k] for k in ('id','model','kind','created_at_ms')}|{'content':{k:answer[k] for k in ('summary','explanations','next_focus','limitations','read_scope','prompt_version')}});deps['ai'].append(entry)
                for dep in json.loads(row['context_json'])['dependencies']:
                    if dep.get('kind')=='retest_source' and not any(v['id']==dep['id'] for v in deps['auxiliary']):deps['auxiliary'].append({'id':dep['id'],'version':dep['version']})
        content=redact(content)
        content['auxiliary_source_count']=len({v['id'] for v in deps['auxiliary']})
        if len(json_text(content).encode())>MAX_SNAPSHOT_BYTES:raise APIError('EXPORT_TOO_LARGE','报告快照较大，请缩小记录范围',422)
        return content,deps,hashlib.sha256(json_text(content).encode()).hexdigest()
    def state(self,c,uid,deps):
        stale=False
        for dep in deps['records']+deps['auxiliary']:
            row=c.execute(select(s.analysis_sessions.c.state_version).where(s.analysis_sessions.c.id==dep['id'],s.analysis_sessions.c.user_id==uid)).first()
            if row is None:return 'source_deleted',False
            stale|=row[0]!=dep['version']
        for dep in deps['notes']:
            note=self.info.note(c,uid,dep['id'])
            cleared=any(note.get(k) is None for k in dep.get('present_fields',[]))
            if cleared or dep['had_values'] and not any(note.get(k) is not None for k in NOTE_FIELDS):return 'user_note_cleared',False
            stale|=note['version']!=dep['version']
        for dep in deps['movement']:
            version=c.scalar(select(s.stability_runs.c.state_version).where(s.stability_runs.c.id==dep['id'],s.stability_runs.c.user_id==uid)) or 0
            stale|=version!=dep['version']
        if any(not self.ai_valid(c,uid,dep,{v['id'] for v in deps['records']}) for dep in deps['ai']):return 'ai_basis_changed',False
        return None,stale
