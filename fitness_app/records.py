"""服务端生成事件／区间的事务存储。HTTP 不提供客户端自报计数接口。"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import Counter,defaultdict
import hashlib
import json
import uuid
import re

from sqlalchemy import select, update, delete, func

from . import db_schema as s
from .auth import APIError
from .catalogue import get_action,definitions
from .database import ConflictError, idempotent, json_text,command_digest

TERMINAL={"completed","ended_early","interrupted","failed"}
STATES=set(s.SESSION_STATES)


def integer(value,name,minimum=0,maximum=2**63-1):
    if type(value) is not int or not minimum<=value<=maximum:
        raise APIError("VALIDATION_FAILED","数值字段不合法",422,{name:"请检查整数范围"})
    return value


def uuid_id(value):
    try:return str(uuid.UUID(value))
    except (ValueError,TypeError,AttributeError) as error:
        raise APIError("INVALID_REQUEST","标识格式不正确",400) from error


def effective_records():
    """原记录默认有效；重分析中/已替代修订由服务器标记，不重复参与本人统计。"""
    return func.coalesce(func.json_extract(s.analysis_sessions.c.config_json,'$.included_in_statistics'),1)==1


def union_segments(items):
    """同判项按端点切分，保留重叠原因；只合并相同语义的相邻区间。"""
    boundaries=defaultdict(list)
    for item in items:
        key=(item['algorithm_version'],item.get('reason') or '')
        boundaries[item['start_ms']].append((key,1));boundaries[item['end_ms']].append((key,-1))
    points=sorted(boundaries);active=Counter();output=[]
    for start,end in zip(points,points[1:]):
        for key,delta in boundaries[start]:
            active[key]+=delta
            if active[key]==0:del active[key]
        if not active:continue
        versions={key[0] for key in active}
        if len(versions)>1:raise APIError("VALIDATION_FAILED","相交区间算法版本不一致",422)
        reason='；'.join(sorted({key[1] for key in active}-{''})) or None
        current={'start_ms':start,'end_ms':end,'algorithm_version':next(iter(versions)),'reason':reason}
        if output and output[-1]['end_ms']==start and all(output[-1][key]==current[key] for key in ('algorithm_version','reason')):
            output[-1]['end_ms']=end
        else:output.append(current)
    return output


def interval_length(items):
    if not items:return 0
    pairs=sorted((item['start_ms'],item['end_ms']) for item in items)
    total=0;start,end=pairs[0]
    for left,right in pairs[1:]:
        if left<=end:end=max(end,right)
        else:total+=end-start;start,end=left,right
    return total+end-start


class SessionStore:
    def __init__(self,db,clock=s.now_ms,fault=None,processing_policy=None):
        self.delete_hooks=[]
        from .pose_input import input_policy_snapshot,ContentPoseService
        self.db,self.clock,self.fault=db,clock,fault
        self.processing_policy=json.loads(json_text(processing_policy if processing_policy is not None else input_policy_snapshot(ContentPoseService)))

    def require_input_policy(self,config):
        if config.get('input_policy')!=self.processing_policy:
            raise APIError('STALE_INPUT_POLICY','输入处理策略已变化，请结束原会话后新建分析；原记录保留。',409)

    def _fault(self,point):
        if self.fault:self.fault(point)

    def _session(self,c,user_id,session_id):
        row=c.execute(select(s.analysis_sessions).where(s.analysis_sessions.c.id==session_id,s.analysis_sessions.c.user_id==user_id)).mappings().first()
        if not row:raise APIError("NOT_FOUND","记录不存在",404)
        return row

    def _actor(self,c,user_id,auth_id):
        auth=c.execute(select(s.auth_sessions).where(s.auth_sessions.c.id==auth_id,s.auth_sessions.c.user_id==user_id)).mappings().first()
        user=c.execute(select(s.users).where(s.users.c.id==user_id)).mappings().first()
        if not user or not user['enabled'] or not auth or auth['revoked_at_ms'] is not None or auth['expires_at_ms']<=self.clock() or auth['auth_version']!=user['auth_version']:
            raise APIError("AUTH_REQUIRED","原登录已失效，请本人重新认证核对",401)

    def _definition(self,c,row):
        return json.loads(c.scalar(select(s.action_capabilities.c.definition_json).where(
            s.action_capabilities.c.action_id==row['action_id'],s.action_capabilities.c.capability_version==row['capability_version'])))

    def create(self,user_id,auth_id,config,command_id,*,video_revision_id=None,stability=False):
        from .movement_stability import VERSION as STABILITY_VERSION
        # 原创建已确认后，输入可能已消费／清理或能力已更新，仍按原摘要返回同一成果。
        with self.db.transaction(False) as c:
            self._actor(c,user_id,auth_id)
            previous=c.execute(select(s.command_receipts).where(s.command_receipts.c.command_id==uuid_id(command_id))).mappings().first()
            if previous:
                if previous['actor_user_id']!=user_id or previous['request_digest']!=command_digest('session.create',None,config):
                    raise ConflictError('幂等标识与请求不匹配','IDEMPOTENCY_CONFLICT')
                if previous['status']!='succeeded':raise ConflictError('原命令尚无成功结果','INVALID_STATE')
                prior_id=json.loads(previous['result_json'])['id']
            else:prior_id=None
        if prior_id:return self.snapshot(user_id,prior_id)
        if type(config.get('save_skeleton',False)) is not bool:raise APIError('VALIDATION_FAILED','骨架保存选项无效',422)
        item=get_action(config.get('action_id'))
        if not item:raise APIError("VALIDATION_FAILED","请选择已有动作",422)
        if config.get('capability_version')!=item['capability_version']:
            raise APIError("STALE_CAPABILITY","动作能力已更新，请重新读取",422)
        if not isinstance(config.get('coaching_view','auto'),str) or config.get('coaching_view','auto') not in {'auto','side','front','oblique'}:raise APIError('VALIDATION_FAILED','机位只能为auto/side/front/oblique',422)
        mode=config.get('mode');source=config.get('source')
        if config.get('save_skeleton') and source in {'video','demo'} and video_revision_id is None:raise APIError('SKELETON_PIPELINE_REQUIRED','视频骨架复盘请使用上传自动/手动分段分析入口',409)
        if not isinstance(mode,str) or not isinstance(source,str) or mode not in {'free','target'} or source not in {'camera','video','demo'}:
            raise APIError("VALIDATION_FAILED","来源或训练模式不合法",422)
        if source!='camera' and mode!='free':raise APIError('VALIDATION_FAILED','视频按整段分析，不设置训练组目标',422)
        target=config.get('target')
        if mode=='free':
            if target is not None:raise APIError("VALIDATION_FAILED","自由模式不提交目标",422)
        else:
            required={'sets','value','unit','rest_seconds'}
            if not isinstance(target,dict):raise APIError('VALIDATION_FAILED','请填写目标设置',422,{'target':'目标设置必填'})
            if set(target)-required:raise APIError('INVALID_REQUEST','目标含未知字段',400)
            if required-set(target):raise APIError('VALIDATION_FAILED','目标字段不完整',422,{'target.'+key:'必填' for key in required-set(target)})
            integer(target['sets'],'target.sets',1,100);integer(target['rest_seconds'],'target.rest_seconds',0,3600)
            integer(target['value'],'target.value',1,3600 if item['unit']=='seconds' else 1000)
            if target['unit']!=item['unit']:raise APIError("VALIDATION_FAILED","目标单位与动作不一致",422,{'target.unit':'单位由动作决定'})
        with self.db.transaction() as c:
            self._actor(c,user_id,auth_id)
            media=None
            if source in {'video','demo'}:
                media=c.execute(select(s.media_inputs).where(s.media_inputs.c.id==uuid_id(config.get('input_id')),s.media_inputs.c.user_id==user_id)).mappings().first()
                trusted_revision=False
                if video_revision_id is not None:
                    revision=c.execute(select(s.video_revisions).where(s.video_revisions.c.id==uuid_id(video_revision_id),s.video_revisions.c.user_id==user_id)).mappings().first()
                    segment=c.execute(select(s.video_segments).where(s.video_segments.c.id==revision['segment_id'],s.video_segments.c.user_id==user_id)).mappings().first() if revision else None
                    trusted_revision=bool(revision and segment and revision['status'] in {'queued','running'} and revision['owner_auth_session_id']==auth_id and
                        source=='video' and revision['input_id']==config.get('input_id') and revision['action_id']==item['action_id'] and
                        config.get('video_revision_id')==revision['id'] and config.get('video_segment_id')==segment['id'] and
                        config.get('video_analysis_id')==segment['analysis_id'] and config.get('included_in_statistics') is False and
                        config.get('source_start_ms')==segment['start_ms'] and config.get('source_end_ms')==segment['end_ms'])
                    if not trusted_revision:raise APIError('INVALID_STATE','内部视频修订归属或状态不匹配',409)
                if not media or (media['status']!='consumed' if trusted_revision else media['status']!='ready' or media['expires_at_ms']<=self.clock()):
                    raise APIError("VALIDATION_FAILED","请重新选择视频并完成校验",422)
                metadata=json.loads(media['encoding_json'] or '{}')
                unknown_duration=media['duration_ms'] is None and metadata.get('duration_known') is False and metadata.get('validation')=='container_and_first_decoded_frame'
                if not media['sha256'] or len(media['sha256'])!=64 or not media['bytes'] or (not media['duration_ms'] and not unknown_duration):
                    raise APIError("VALIDATION_FAILED","视频校验信息不完整",422)
                if source=='demo':
                    from .presentation_assets import demo_resource
                    demo=demo_resource(config.get('demo_id'),require_track=False)
                    if not demo or metadata.get('demo_id')!=demo['id'] or item['action_id']!=demo['action_id'] or mode!='free':
                        raise APIError('VALIDATION_FAILED','演示输入、动作或模式与登记信息不符',422)
                    if (media['sha256'],media['bytes'])!=(demo['video']['sha256'],demo['video']['bytes']):raise APIError('SOURCE_MISMATCH','演示输入内容不符',422)
                elif metadata.get('demo_id') or config.get('demo_id'):
                    raise APIError('VALIDATION_FAILED','内置演示必须使用演示来源，不计入个人训练',422)
            elif config.get('input_id') or config.get('demo_id'):
                raise APIError("VALIDATION_FAILED","相机来源不提交文件标识",422)
            def write():
                sid=s.new_id();now=self.clock()
                c.execute(s.analysis_sessions.insert().values(id=sid,user_id=user_id,owner_auth_session_id=auth_id,
                    action_id=item['action_id'],capability_version=item['capability_version'],source=source,mode=mode,
                    config_json=json_text({**config,'input_policy':self.processing_policy,'coaching_view':config.get('coaching_view','auto'),'posture_policy_version':item.get('posture_policy_version'),
                        **({'stability_policy_version':STABILITY_VERSION} if stability else {})}),created_at_ms=now,
                    source_sha256=media['sha256'] if media else None,source_bytes=media['bytes'] if media else None,
                    source_duration_ms=media['duration_ms'] if media else None))
                for index in range(1,(target['sets'] if target else 1)+1):
                    c.execute(s.session_sets.insert().values(session_id=sid,set_index=index,target_value=target['value'] if target else None,
                        target_unit=target['unit'] if target else None,rest_seconds=target['rest_seconds'] if target else None))
                self._fault('before_commit')
                return {'id':sid,'session_id':sid}
            result=idempotent(c,user_id,uuid_id(command_id),config,'session.create',write)
        self._fault('after_commit')
        return self.snapshot(user_id,result['id'])

    def set_state(self,user_id,session_id,state,command_id,expected_version=None,auth_id=None,reason=None,extra=None,system=False,context=None):
        if state not in STATES:raise APIError("VALIDATION_FAILED","会话状态不合法",422)
        extra=extra or {}
        allowed={'resume_from','rest_remaining_ms','completed_sets','active_ms'}
        if set(extra)-allowed:raise APIError("INVALID_REQUEST","不允许修改统计字段")
        for key,value in extra.items():
            if key=='resume_from':
                if value not in {'running','resting'}:raise APIError("VALIDATION_FAILED","恢复阶段无效",422)
            else:integer(value,key)
        if system and (state!='interrupted' or reason not in {'auth_revoked','auth_expired','control_lost','service_stopped'}):
            raise ValueError('系统收尾只允许明确原因的中断')
        payload={'state':state,'expected_version':expected_version,'reason':reason,'extra':extra}
        if system:payload['system']=True
        if context is not None:payload['context']=context
        with self.db.transaction() as c:
            row=self._session(c,user_id,session_id)
            if not system:self._actor(c,user_id,auth_id or row['owner_auth_session_id'])
            def write():
                if expected_version is not None and row['state_version']!=expected_version:raise ConflictError("会话已更新，请重新读取")
                if row['status'] in TERMINAL:
                    if state!=row['status']:raise APIError("INVALID_STATE","请创建新的训练会话",409)
                    return {'id':session_id,'state_version':row['state_version'],**({'context_digest':context['request_digest']} if context else {})}
                if extra.get('active_ms',row['active_ms'])<row['active_ms']:
                    raise APIError("VALIDATION_FAILED","请核对训练时长",422)
                changes={**extra,'status':state,'state_version':row['state_version']+1}
                if state=='running' and row['started_at_ms'] is None:changes['started_at_ms']=self.clock()
                if state in TERMINAL:
                    changes.update(ended_at_ms=self.clock(),end_reason=reason or state)
                    c.execute(update(s.session_sets).where(s.session_sets.c.session_id==session_id,s.session_sets.c.status=='active')
                        .values(status='completed' if state=='completed' else 'ended_early'))
                c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==session_id).values(**changes))
                self._fault('before_commit')
                return {'id':session_id,'state_version':changes['state_version'],**({'context_digest':context['request_digest']} if context else {})}
            idempotent(c,user_id,uuid_id(command_id),payload,'session.state',write,session_id)
        self._fault('after_commit')
        return self.snapshot(user_id,session_id)

    def command_result(self,user_id,session_id,command_id):
        with self.db.transaction(False) as c:
            self._session(c,user_id,session_id)
            row=c.execute(select(s.command_receipts).where(s.command_receipts.c.actor_user_id==user_id,
                s.command_receipts.c.command_id==command_id,s.command_receipts.c.resource_id==session_id,
                s.command_receipts.c.status=='succeeded')).mappings().first()
        return json.loads(row['result_json']) if row and row['result_json'] else None

    def _assessment(self,data,definition,start,end):
        if not isinstance(data,list) or len(data)>32:raise APIError("VALIDATION_FAILED","评估项格式无效",422)
        supported={item['code']:item for item in definition.get('issues',[])};codes=set()
        for item in data:
            if not isinstance(item,dict) or set(item)-{'code','status','reason','start_ms','end_ms','rule_version','evidence_level','body_parts','message','suggestion','evidence'}:
                raise APIError("VALIDATION_FAILED","评估项字段无效",422)
            code=item.get('code');status=item.get('status')
            if not isinstance(code,str) or not isinstance(status,str) or code in codes or code not in supported or status not in {'issue','not_observed','unassessable'}:
                raise APIError("VALIDATION_FAILED","检测项或评估状态无效",422)
            codes.add(code)
            reference=supported[code].get('status')=='reference'
            if reference and item.get('evidence_level')=='reference':
                if item.get('rule_version')!=supported[code].get('rule_version'):raise APIError('VALIDATION_FAILED','姿态规则版本不符',422)
                if item.get('body_parts')!=supported[code].get('body_parts'):raise APIError('VALIDATION_FAILED','姿态部位不符',422)
                if any(not isinstance(item.get(k),str) or len(item[k])>1024 for k in ('message','suggestion')):raise APIError('VALIDATION_FAILED','姿态提示无效',422)
                if not isinstance(item.get('evidence'),dict) or len(json_text(item['evidence']))>4096:raise APIError('VALIDATION_FAILED','姿态证据无效',422)
            elif reference and status!='unassessable':raise APIError('VALIDATION_FAILED','参考姿态结论需要规则版本与证据',422)
            if status!='unassessable' and supported[code].get('status') not in {'verified','reference'}:
                raise APIError("VALIDATION_FAILED","请使用已验证的检查项",422)
            left=integer(item.get('start_ms'),'assessment.start_ms');right=integer(item.get('end_ms'),'assessment.end_ms')
            if not start<=left<right<=end:raise APIError("VALIDATION_FAILED","证据区间超出本次动作",422)
            if not isinstance(item.get('reason',''),str) or len(item.get('reason',''))>512:
                raise APIError("VALIDATION_FAILED","请填写评估原因",422)
            if status=='unassessable' and not item.get('reason','').strip():raise APIError("VALIDATION_FAILED","请填写评估原因",422)
        return json_text(data,list)

    def append_event(self,user_id,session_id,event,auth_id=None):
        allowed={'event_seq','set_index','side','start_ms','end_ms','algorithm_version','feature_schema_version','assessments','active_ms'}
        if set(event)-allowed:raise APIError("INVALID_REQUEST","请核对事件字段")
        sequence=integer(event.get('event_seq'),'event_seq',1);index=integer(event.get('set_index'),'set_index',1,100)
        start=integer(event.get('start_ms'),'start_ms');end=integer(event.get('end_ms'),'end_ms')
        if end<=start:raise APIError("VALIDATION_FAILED","动作区间无效",422)
        for key in ('algorithm_version','feature_schema_version'):
            if not isinstance(event.get(key),str) or not 1<=len(event[key])<=100:raise APIError("VALIDATION_FAILED","算法版本缺失",422)
        command=str(uuid.uuid5(uuid.UUID(session_id),'event:'+str(sequence)))
        with self.db.transaction() as c:
            row=self._session(c,user_id,session_id);self._actor(c,user_id,auth_id or row['owner_auth_session_id'])
            definition=self._definition(c,row)
            unit=definition['unit'];side=event.get('side')
            valid_sides={'left','right'} if unit=='per_side_reps' or row['action_id']=='high_knees' else {'none'}
            if unit=='seconds' or side not in valid_sides:raise APIError("VALIDATION_FAILED","侧别／计数单位不合法",422)
            assessments=self._assessment(event.get('assessments',[]),definition,start,end)
            group=c.execute(select(s.session_sets).where(s.session_sets.c.session_id==session_id,s.session_sets.c.set_index==index)).mappings().first()
            if not group:raise APIError("VALIDATION_FAILED","组不属于该会话",422)
            def write():
                if row['status']!='running':raise APIError("INVALID_STATE","请刷新训练状态后完成动作",409)
                if group['status'] not in {'pending','active'}:raise APIError('INVALID_STATE','请开始新的一组训练',409)
                self._current_group(c,row,group)
                if sequence!=row['last_event_seq']+1:raise ConflictError("事件序号不连续，请先同步已提交事件")
                c.execute(s.action_events.insert().values(session_id=session_id,set_id=group['id'],event_seq=sequence,side=side,
                    start_ms=start,end_ms=end,algorithm_version=event['algorithm_version'],feature_schema_version=event['feature_schema_version'],assessment_json=assessments))
                changes={'total_count':group['total_count']+1,'status':'active'}
                if side in {'left','right'}:changes[side+'_count']=group[side+'_count']+1
                c.execute(update(s.session_sets).where(s.session_sets.c.id==group['id']).values(**changes))
                session_change={'last_event_seq':sequence,'state_version':row['state_version']+1}
                if 'active_ms' in event:session_change['active_ms']=max(row['active_ms'],integer(event['active_ms'],'active_ms'))
                session_change.update(self._target_transition(c,row,{**group,**changes},unit))
                c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==session_id).values(**session_change))
                self._fault('before_commit')
                return {'id':session_id,'event_seq':sequence,'state_version':session_change['state_version']}
            idempotent(c,user_id,command,event,'session.event',write,session_id)
        self._fault('after_commit')
        return self.snapshot(user_id,session_id)

    def append_posture(self,user_id,session_id,index,findings,auth_id=None,active_ms=None):
        """姿态观察独立持久化，不改完成量；同一连续观察仅延长自己的区间。"""
        integer(index,'set_index',1,100)
        if not isinstance(findings,list) or not 1<=len(findings)<=32:
            raise APIError('VALIDATION_FAILED','姿态批次无效',422)
        payload={'set_index':index,'findings':findings}
        if active_ms is not None:payload['active_ms']=integer(active_ms,'active_ms')
        command=str(uuid.uuid5(uuid.UUID(session_id),'posture:'+hashlib.sha256(json_text(payload).encode()).hexdigest()))
        with self.db.transaction() as c:
            row=self._session(c,user_id,session_id);self._actor(c,user_id,auth_id or row['owner_auth_session_id'])
            definition=self._definition(c,row)
            group=c.execute(select(s.session_sets).where(s.session_sets.c.session_id==session_id,s.session_sets.c.set_index==index)).mappings().first()
            if not group:raise APIError('VALIDATION_FAILED','姿态观察不属于本会话',422)
            for item in findings:
                if not isinstance(item,dict) or not isinstance(item.get('side'),str) or item.get('side') not in {'none','left','right'}:
                    raise APIError('VALIDATION_FAILED','姿态侧别无效',422)
                start=integer(item.get('start_ms'),'start_ms');end=integer(item.get('end_ms'),'end_ms')
                if end<=start:raise APIError('VALIDATION_FAILED','姿态区间无效',422)
                self._assessment([{k:v for k,v in item.items() if k!='side'}],definition,start,end)
                if item.get('evidence_level')!='reference':raise APIError('VALIDATION_FAILED','姿态观察需要明确参考依据',422)
            def write():
                if row['status']!='running' or group['status'] not in {'pending','active'}:
                    raise APIError('INVALID_STATE','请刷新训练状态后记录姿态',409)
                self._current_group(c,row,group)
                for item in findings:
                    condition=(s.posture_observations.c.session_id==session_id,s.posture_observations.c.set_id==group['id'],
                        s.posture_observations.c.code==item['code'],s.posture_observations.c.side==item['side'],
                        s.posture_observations.c.start_ms==item['start_ms'],s.posture_observations.c.rule_version==item['rule_version'])
                    old=c.execute(select(s.posture_observations).where(*condition)).mappings().first()
                    encoded=json_text(item)
                    if old:
                        if item['end_ms']<old['end_ms'] or (item['end_ms']==old['end_ms'] and encoded!=old['data_json']):
                            raise ConflictError('请按时间顺序提交新的姿态观察')
                        c.execute(update(s.posture_observations).where(s.posture_observations.c.id==old['id']).values(end_ms=item['end_ms'],data_json=encoded))
                    else:
                        c.execute(s.posture_observations.insert().values(session_id=session_id,set_id=group['id'],
                            code=item['code'],side=item['side'],start_ms=item['start_ms'],end_ms=item['end_ms'],
                            rule_version=item['rule_version'],data_json=encoded))
                changes={'state_version':row['state_version']+1}
                if active_ms is not None:changes['active_ms']=max(row['active_ms'],active_ms)
                c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==session_id).values(**changes))
                self._fault('before_commit')
                return {'id':session_id,'state_version':changes['state_version']}
            idempotent(c,user_id,command,payload,'session.posture',write,session_id)
        self._fault('after_commit')
        return self.snapshot(user_id,session_id)

    def append_intervals(self,user_id,session_id,index,intervals,auth_id=None,active_ms=None):
        integer(index,'set_index',1,100)
        if not isinstance(intervals,list) or not 1<=len(intervals)<=100:raise APIError("VALIDATION_FAILED","区间批次无效",422)
        payload={'set_index':index,'intervals':intervals}
        if active_ms is not None:payload['active_ms']=integer(active_ms,'active_ms')
        command=str(uuid.uuid5(uuid.UUID(session_id),'interval:'+hashlib.sha256(json_text(payload).encode()).hexdigest()))
        with self.db.transaction() as c:
            row=self._session(c,user_id,session_id);self._actor(c,user_id,auth_id or row['owner_auth_session_id'])
            definition=self._definition(c,row);supported={item['code']:item for item in definition.get('issues',[])}
            group=c.execute(select(s.session_sets).where(s.session_sets.c.session_id==session_id,s.session_sets.c.set_index==index)).mappings().first()
            if not group or definition['unit']!='seconds':raise APIError('VALIDATION_FAILED','保持区间仅用于当前静态动作',422)
            for item in intervals:
                if not isinstance(item,dict) or set(item)-{'kind','item_code','start_ms','end_ms','algorithm_version','reason'}:raise APIError("VALIDATION_FAILED","区间字段无效",422)
                left=integer(item.get('start_ms'),'start_ms');right=integer(item.get('end_ms'),'end_ms')
                if left>=right or not isinstance(item.get('kind'),str) or item['kind'] not in {'hold','issue','unassessable'}:raise APIError("VALIDATION_FAILED","区间类型／范围无效",422)
                code=item.get('item_code')
                if code is not None and not isinstance(code,str):raise APIError("VALIDATION_FAILED","区间检测项格式无效",422)
                if item['kind']=='hold' and code is not None:raise APIError("VALIDATION_FAILED","保持区间不绑定质量代码",422)
                if code is not None and code not in supported:raise APIError("VALIDATION_FAILED","区间检测项无效",422)
                if item['kind']=='issue' and (code not in supported or supported[code].get('status')!='verified'):
                    raise APIError("VALIDATION_FAILED","请使用已验证的检查项生成问题区间",422)
                if not isinstance(item.get('algorithm_version'),str) or not 1<=len(item['algorithm_version'])<=100:raise APIError("VALIDATION_FAILED","区间算法版本无效",422)
                if item.get('reason') is not None and (not isinstance(item['reason'],str) or len(item['reason'])>512):raise APIError("VALIDATION_FAILED","区间原因无效",422)
                if item['kind']=='unassessable' and not (item.get('reason') or '').strip():raise APIError("VALIDATION_FAILED","请填写该时段的评估原因",422)
            def write():
                if row['status'] not in {'running','waiting'} or (row['status']=='waiting' and any(item['kind']=='hold' for item in intervals)):
                    raise APIError("INVALID_STATE","请刷新训练状态后记录保持时长",409)
                if group['status'] not in {'pending','active'}:raise APIError('INVALID_STATE','请开始新的一组动作观察',409)
                self._current_group(c,row,group)
                keys={(item['kind'],item.get('item_code')) for item in intervals}
                for kind,code in keys:
                    added=[item for item in intervals if (item['kind'],item.get('item_code'))==(kind,code)]
                    left=min(item['start_ms'] for item in added);right=max(item['end_ms'] for item in added)
                    old=[dict(item) for item in c.execute(select(s.observation_intervals).where(s.observation_intervals.c.session_id==session_id,
                        s.observation_intervals.c.set_id==group['id'],s.observation_intervals.c.kind==kind,
                        s.observation_intervals.c.item_code.is_(None) if code is None else s.observation_intervals.c.item_code==code,
                        s.observation_intervals.c.start_ms<=right,s.observation_intervals.c.end_ms>=left)).mappings()]
                    merged=union_segments(old+added)
                    if old:c.execute(delete(s.observation_intervals).where(s.observation_intervals.c.id.in_([item['id'] for item in old])))
                    for item in merged:c.execute(s.observation_intervals.insert().values(session_id=session_id,set_id=group['id'],kind=kind,item_code=code,**item))
                holding=s.observation_intervals.alias('confirmed_hold');unknown=s.observation_intervals.alias('unknown_support')
                conflict=c.scalar(select(func.count()).select_from(holding.join(unknown,holding.c.set_id==unknown.c.set_id)).where(
                    holding.c.session_id==session_id,holding.c.set_id==group['id'],holding.c.kind=='hold',
                    unknown.c.kind=='unassessable',unknown.c.item_code.is_(None),
                    holding.c.start_ms<unknown.c.end_ms,holding.c.end_ms>unknown.c.start_ms))
                if conflict:raise APIError("VALIDATION_FAILED","请为不同支撑状态选择独立的观察时段",422)
                holds=c.scalar(select(func.coalesce(func.sum(s.observation_intervals.c.end_ms-s.observation_intervals.c.start_ms),0))
                    .where(s.observation_intervals.c.session_id==session_id,s.observation_intervals.c.set_id==group['id'],s.observation_intervals.c.kind=='hold'))
                c.execute(update(s.session_sets).where(s.session_sets.c.id==group['id']).values(hold_ms=holds,status='active'))
                total=c.scalar(select(func.sum(s.session_sets.c.hold_ms)).where(s.session_sets.c.session_id==session_id)) or 0
                transition=self._target_transition(c,row,{**group,'hold_ms':holds},definition['unit'])
                active_change={'active_ms':max(row['active_ms'],active_ms)} if active_ms is not None else {}
                c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==session_id).values(hold_ms=total,state_version=row['state_version']+1,**active_change,**transition))
                self._fault('before_commit')
                return {'id':session_id,'hold_ms':total,'state_version':row['state_version']+1}
            idempotent(c,user_id,command,payload,'session.interval',write,session_id)
        self._fault('after_commit')
        return self.snapshot(user_id,session_id)

    @staticmethod
    def _current_group(c,row,group):
        if row['mode']!='target':return
        current=c.scalar(select(s.session_sets.c.id).where(s.session_sets.c.session_id==row['id'],s.session_sets.c.status.in_(['pending','active'])).order_by(s.session_sets.c.set_index).limit(1))
        if current!=group['id']:raise APIError('INVALID_STATE','只能推进当前未完成组',409)

    def _target_transition(self,c,row,group,unit):
        """与事件／保持区间同一事务提交，重复原批次不会再次换组。"""
        if row['mode']!='target':return {}
        value=group['target_value']
        reached=(group['hold_ms']>=value*1000 if unit=='seconds' else
                 min(group['left_count'],group['right_count'])>=value if unit=='per_side_reps' else group['total_count']>=value)
        if not reached:return {}
        c.execute(update(s.session_sets).where(s.session_sets.c.id==group['id']).values(status='completed'))
        complete=c.scalar(select(func.count()).select_from(s.session_sets).where(s.session_sets.c.session_id==row['id'],s.session_sets.c.status=='completed'))
        total=c.scalar(select(func.count()).select_from(s.session_sets).where(s.session_sets.c.session_id==row['id']))
        if complete==total:return {'completed_sets':complete,'status':'completed','ended_at_ms':self.clock(),'end_reason':'target_completed','rest_remaining_ms':0}
        rest=group['rest_seconds']*1000
        return {'completed_sets':complete,'status':'resting' if rest else 'waiting','rest_remaining_ms':rest,'resume_from':'resting' if rest else 'running'}

    def confirmed(self,user_id,session_id,operation,payload):
        if operation=='event':
            command=str(uuid.uuid5(uuid.UUID(session_id),'event:'+str(payload['event_seq'])));kind='session.event';data=payload
        elif operation=='intervals':
            command=str(uuid.uuid5(uuid.UUID(session_id),'interval:'+hashlib.sha256(json_text(payload).encode()).hexdigest()));kind='session.interval';data=payload
        elif operation=='posture':
            command=str(uuid.uuid5(uuid.UUID(session_id),'posture:'+hashlib.sha256(json_text(payload).encode()).hexdigest()));kind='session.posture';data=payload
        elif operation=='state':
            command=payload['command_id'];kind='session.state'
            data={'state':payload['state'],'expected_version':payload.get('expected_version'),'reason':payload.get('reason'),'extra':payload.get('extra') or {}}
            if payload.get('system'):data['system']=True
            if payload.get('context') is not None:data['context']=payload['context']
        else:return False
        with self.db.transaction(False) as c:
            receipt=c.execute(select(s.command_receipts).where(s.command_receipts.c.command_id==command,s.command_receipts.c.actor_user_id==user_id,
                s.command_receipts.c.resource_id==session_id,s.command_receipts.c.status=='succeeded')).mappings().first()
        return bool(receipt and receipt['request_digest']==command_digest(kind,session_id,data))

    def snapshot(self,user_id,session_id,include_details=False):
        with self.db.transaction(False) as c:
            row=dict(self._session(c,user_id,session_id));definition=self._definition(c,row)
            groups=[dict(group) for group in c.execute(select(s.session_sets).where(s.session_sets.c.session_id==session_id).order_by(s.session_sets.c.set_index)).mappings()]
            events=[dict(event) for event in c.execute(select(s.action_events).where(s.action_events.c.session_id==session_id).order_by(s.action_events.c.event_seq)).mappings()] if include_details else []
            intervals=[dict(item) for item in c.execute(select(s.observation_intervals).where(s.observation_intervals.c.session_id==session_id).order_by(s.observation_intervals.c.start_ms)).mappings()] if include_details else []
            posture=[json.loads(item) for item in c.execute(select(s.posture_observations.c.data_json).where(s.posture_observations.c.session_id==session_id).order_by(s.posture_observations.c.start_ms)).scalars()] if include_details else []
            evaluable=c.exec_driver_sql("SELECT count(*) FROM action_events WHERE session_id=? AND EXISTS "
                "(SELECT 1 FROM json_each(assessment_json) WHERE json_extract(value,'$.status') IN ('issue','not_observed'))",(session_id,)).scalar()
            last_saved=c.scalar(select(func.max(s.command_receipts.c.ended_at_ms)).where(s.command_receipts.c.actor_user_id==user_id,
                s.command_receipts.c.resource_id==session_id,s.command_receipts.c.resource_type.like('session.%')))
        for event in events:event['assessments']=json.loads(event.pop('assessment_json'))
        total_count=sum(group['total_count'] for group in groups)
        config=json.loads(row['config_json'])
        result={'session_id':session_id,'action_id':row['action_id'],'action_name':definition.get('name',row['action_id']),
            'included_in_statistics':config.get('included_in_statistics',True) is True,'superseded_by':config.get('superseded_by'),
            'capability_version':row['capability_version'],'unit':definition['unit'],'source':row['source'],'mode':row['mode'],
            'counting_available':bool(definition.get('counting_available')),'count_accuracy_status':definition.get('count_accuracy_status','not_available'),
            'config':config,'input_policy':config.get('input_policy'),'status':row['status'],'state_version':row['state_version'],
            'started_at_ms':row['started_at_ms'],'ended_at_ms':row['ended_at_ms'],'created_at_ms':row['created_at_ms'],
            'end_reason':row['end_reason'],'active_ms':row['active_ms'],'hold_ms':row['hold_ms'],'last_event_seq':row['last_event_seq'],
            'completed_sets':row['completed_sets'],'resume_from':row['resume_from'],'rest_remaining_ms':row['rest_remaining_ms'],
            'total_count':total_count,'left_count':sum(group['left_count'] for group in groups),
            'right_count':sum(group['right_count'] for group in groups),'evaluable_count':evaluable,'unassessable_count':total_count-evaluable,
            'last_saved_at_ms':last_saved or row['created_at_ms'],'save_state':'clean','groups':groups,'events':events,'intervals':intervals,
            'supported_items':definition.get('issues',[]),'source_sha256':row['source_sha256'],'source_bytes':row['source_bytes'],
            'source_duration_ms':row['source_duration_ms'],'posture_available':bool(definition.get('posture_available')),'posture_policy_version':definition.get('posture_policy_version'),
            'posture_observations':posture}
        if include_details:
            from .posture_rules import report_findings
            result['posture']=report_findings(result)
            from .presentation_assets import confirmed_demo_findings
            result['confirmed_demo_findings']=confirmed_demo_findings(result)
        return result

    def list_records(self,user_id,page=1,page_size=20,action_id=None,source=None,date_from=None,date_to=None,search=None):
        integer(page,'page',1);integer(page_size,'page_size',1,100)
        conditions=[s.analysis_sessions.c.user_id==user_id,s.analysis_sessions.c.started_at_ms.is_not(None),s.analysis_sessions.c.source!='demo',effective_records()]
        if search:
            if not isinstance(search,str) or len(search)>64:raise APIError('VALIDATION_FAILED','搜索词最多 64 字符',422)
            word=search.strip().casefold()
            matching=[item['action_id'] for item in definitions() if any(word in value.casefold() for value in [item['name'],*item.get('aliases',[])])]
            conditions.append(s.analysis_sessions.c.action_id.in_(matching))
        if action_id:
            if not get_action(action_id):raise APIError("VALIDATION_FAILED","动作筛选无效",422)
            conditions.append(s.analysis_sessions.c.action_id==action_id)
        if source:
            if source not in {'camera','video'}:raise APIError("VALIDATION_FAILED","来源筛选无效",422)
            conditions.append(s.analysis_sessions.c.source==source)
        if date_from and date_to and date_from>date_to:raise APIError("VALIDATION_FAILED","请按先后顺序选择起止日期",422)
        for key,value in [('date_from',date_from),('date_to',date_to)]:
            if not value:continue
            try:
                if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError("date format")
                date=datetime.strptime(value,'%Y-%m-%d').replace(tzinfo=ZoneInfo('Asia/Shanghai'))
                boundary=int((date+(timedelta(days=1) if key=='date_to' else timedelta())).timestamp()*1000)
            except (ValueError,TypeError,OverflowError) as error:raise APIError("VALIDATION_FAILED","日期筛选无效",422,{key:"格式为 YYYY-MM-DD"}) from error
            conditions.append(s.analysis_sessions.c.started_at_ms<boundary if key=='date_to' else s.analysis_sessions.c.started_at_ms>=boundary)
        with self.db.transaction(False) as c:
            total=c.scalar(select(func.count()).select_from(s.analysis_sessions).where(*conditions))
            page=min(page,max(1,(total+page_size-1)//page_size))
            ids=c.execute(select(s.analysis_sessions.c.id).where(*conditions).order_by(s.analysis_sessions.c.started_at_ms.desc(),s.analysis_sessions.c.id.desc())
                .offset((page-1)*page_size).limit(page_size)).scalars().all()
        items=[self.snapshot(user_id,sid) for sid in ids]
        for item in items:
            item.pop('events');item.pop('intervals')
        return {'records':items,'page':page,'page_size':page_size,'total':total}

    def _validate_record_deletion(self,c,user_id,ids,versions,unstarted_parent=None):
        for offset in range(0,len(ids),250):
            batch=ids[offset:offset+250]
            rows={r['id']:r for r in c.execute(select(s.analysis_sessions).where(s.analysis_sessions.c.user_id==user_id,s.analysis_sessions.c.id.in_(batch))).mappings()}
            for sid in batch:
                if sid not in rows:self._session(c,user_id,sid)
                row=rows[sid];config=json.loads(row['config_json'])
                hidden_orphan=(unstarted_parent and row['status']=='ready' and row['started_at_ms'] is None and row['source']=='video' and config.get('video_analysis_id')==unstarted_parent and config.get('included_in_statistics') is False)
                if row['status'] not in TERMINAL and not hidden_orphan:raise APIError('INVALID_STATE','活动记录请先结束后再删除',409)
                if row['state_version']!=versions[sid]:raise ConflictError('记录已更新，未删除任何一条')
            if c.execute(select(1).select_from(s.jobs).where(s.jobs.c.session_id.in_(batch),s.jobs.c.status.not_in(TERMINAL|{'cancelled'})).limit(1)).first():
                raise APIError('INVALID_STATE','记录仍有活动任务，未删除',409)
            revision=s.video_revisions;segment=s.video_segments;parent=s.video_analyses;media=s.media_inputs
            inactive=('completed','cancelled','interrupted','failed')
            busy=select(1).select_from(revision.join(segment,revision.c.segment_id==segment.c.id).join(parent,segment.c.analysis_id==parent.c.id).join(media,revision.c.input_id==media.c.id)).where(
                revision.c.user_id==user_id,revision.c.session_id.in_(batch),
                revision.c.status.not_in(inactive)|parent.c.status.not_in(inactive)|(revision.c.cleanup_pending==1)|(parent.c.cleanup_pending==1)|media.c.temporary_relative_path.is_not(None)).limit(1)
            if c.execute(busy).first():raise APIError('INVALID_STATE','关联视频分析仍在处理或等待清理，请先结束并恢复原任务',409)

    def _delete_record_rows(self,c,user_id,ids):
        for offset in range(0,len(ids),250):
            batch=ids[offset:offset+250]
            job_batch=c.execute(select(s.jobs.c.id).where(s.jobs.c.session_id.in_(batch),s.jobs.c.owner_id==user_id)).scalars().all()
            affected=c.execute(select(s.video_revisions.c.segment_id).where(s.video_revisions.c.user_id==user_id,s.video_revisions.c.session_id.in_(batch))).scalars().all()
            if affected:c.execute(update(s.video_segments).where(s.video_segments.c.id.in_(affected),s.video_segments.c.user_id==user_id).values(state_version=s.video_segments.c.state_version+1))
            report_records=func.json_each(s.report_exports.c.dependencies_json,'$.records').table_valued('key','value').alias('export_source')
            report_aux=func.json_each(s.report_exports.c.dependencies_json,'$.auxiliary').table_valued('key','value').alias('export_aux')
            depends=select(1).select_from(report_records).where(func.json_extract(report_records.c.value,'$.id').in_(batch)).exists()|select(1).select_from(report_aux).where(func.json_extract(report_aux.c.value,'$.id').in_(batch)).exists()
            c.execute(update(s.report_exports).where(s.report_exports.c.user_id==user_id,s.report_exports.c.status.in_(('queued','running','ready')),depends).values(status='invalidated',error_code='source_deleted',pdf_blob=None,pdf_sha256=None,data_bytes=0,snapshot_json=None,state_version=s.report_exports.c.state_version+1))
            c.execute(delete(s.command_receipts).where(s.command_receipts.c.actor_user_id==user_id,s.command_receipts.c.resource_id.in_(batch+job_batch),
                (s.command_receipts.c.resource_type.like('session.%')|s.command_receipts.c.resource_type.like('job.%'))))
            for hook in self.delete_hooks:hook(c,user_id,batch)
            c.execute(delete(s.analysis_sessions).where(s.analysis_sessions.c.id.in_(batch),s.analysis_sessions.c.user_id==user_id))

    def delete_records(self,user_id,auth_id,ids,versions,command_id):
        if not isinstance(ids,list) or not 1<=len(ids)<=20 or not all(isinstance(value,str) for value in ids) or len(set(ids))!=len(ids):raise APIError("VALIDATION_FAILED","请选择当前页 1–20 条不重复记录",422)
        if not isinstance(versions,dict) or set(versions)!=set(ids):raise APIError("VALIDATION_FAILED","记录版本不完整",422)
        ids=[uuid_id(value) for value in ids]
        versions={uuid_id(key):value for key,value in versions.items()}
        if len(set(ids))!=len(ids) or set(versions)!=set(ids):raise APIError("VALIDATION_FAILED","记录标识重复或版本不完整",422)
        for sid in ids:integer(versions[sid],'expected_versions')
        payload={'ids':sorted(ids),'expected_versions':versions}
        with self.db.transaction() as c:
            self._actor(c,user_id,auth_id)
            def write():
                self._validate_record_deletion(c,user_id,ids,versions)
                self._delete_record_rows(c,user_id,ids)
                self._fault('before_commit')
                return {'deleted_ids':ids}
            result=idempotent(c,user_id,uuid_id(command_id),payload,'records.delete',write)
        self._fault('after_commit')
        return result

    def recover_interrupted(self):
        """仅由持有正式服务实例锁的新进程启动调用，不因页面重连执行。"""
        with self.db.transaction() as c:
            c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.status.not_in(TERMINAL),s.analysis_sessions.c.started_at_ms.is_not(None))
                .values(status='interrupted',end_reason='process_restart',ended_at_ms=self.clock(),state_version=s.analysis_sessions.c.state_version+1))
            c.execute(update(s.jobs).where(s.jobs.c.status.not_in(TERMINAL|{'cancelled'})).values(status='interrupted',error_code='PROCESS_RESTART',ended_at_ms=self.clock(),state_version=s.jobs.c.state_version+1))
