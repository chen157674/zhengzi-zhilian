"""AI请求身份、事务额度预留/结算和取消；不重复发送，不把估算当供应商账单。"""
import json
from sqlalchemy import select,update,func
from . import db_schema as s
from .auth import APIError
from .database import ConflictError,StorageError,json_text
from .records import integer,uuid_id
from .deepseek_client import PRICING_VERSION,estimate_reservation

TERMINAL={'succeeded','failed','cancelled','uncertain'}
ERRORS={
 'AI_KEY_INVALID':'AI密钥无效或已失效，请到AI服务设置检查',
 'AI_KEY_STORAGE_UNAVAILABLE':'AI密钥存储无法读取；本次未发送，请到AI服务设置重新核对',
 'AI_BALANCE_INSUFFICIENT':'AI账户余额不足',
 'AI_RATE_LIMITED':'AI服务限流，请稍后再试',
 'AI_NETWORK_UNCERTAIN':'上游状态暂不能确认，可能已产生用量；请先核对请求状态',
 'AI_RESPONSE_INVALID':'AI返回内容无效，没有生成有效报告',
 'AI_OUTPUT_INCOMPLETE':'AI回复被截断，没有生成有效报告',
 'AI_EVIDENCE_INVALID':'AI回复与检测依据不一致，没有生成有效报告',
 'AI_CONTEXT_CHANGED':'分析记录或AI设置已变化，请根据最新结果重新提问',
 'AI_SOURCE_DELETED':'来源训练记录已删除，关联AI正文已清除；已发生用量保留',
 'AI_AUTH_REVOKED':'原登录已失效，已停止本次AI任务',
 'AI_SERVICE_STOPPED':'AI服务已停止，本次请求未完成',
 'AI_BUSY':'AI正在处理其他请求，请稍后再试',
}


class AIUsageStore:
    def __init__(self,settings):
        self.settings=settings;self.db=settings.db;self.clock=settings.clock;self.sessions=settings.sessions

    def _row(self,c,uid,rid):
        row=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==rid,s.ai_requests.c.user_id==uid)).mappings().first()
        if not row:raise APIError('NOT_FOUND','AI请求不存在',404)
        return dict(row)

    def _snapshot(self,row):
        status='cancelling' if row['cancel_requested'] and row['status']=='running' else row['status']
        return {'request_id':row['id'],'kind':row['kind'],'status':status,'state_version':row['state_version'],
                'source':row['source'],'model':row['model'],'context':json.loads(row['context_json']),
                'result':json.loads(row['result_json']) if row['result_json'] else None,
                'result_retention':'record_report' if row['kind'] in {'review','comparison'} else 'current_conversation_memory',
                'usage':json.loads(row['usage_json']) if row['usage_json'] else None,
                'error_code':row['error_code'],'message':ERRORS.get(row['error_code'],'AI任务未完成，请稍后检查') if row['error_code'] else None,
                'reserved_micro_cny':row['reserved_micro_cny'],'estimated_micro_cny':row['estimated_micro_cny'],
                'usage_is_provider_bill':False,'pricing_version':row['pricing_version'],'created_at_ms':row['created_at_ms'],
                'started_at_ms':row['started_at_ms'],'ended_at_ms':row['ended_at_ms'],
                'upstream_state_uncertain':row['status']=='uncertain','cancel_requested':bool(row['cancel_requested'])}

    def get(self,uid,auth,rid):
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth);return self._snapshot(self._row(c,uid,uuid_id(rid)))

    def existing(self,uid,auth,rid,digest):
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth)
            row=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==rid)).mappings().first()
            if row:
                if row['user_id']!=uid or row['request_digest']!=digest:raise ConflictError('AI请求标识与内容不匹配','IDEMPOTENCY_CONFLICT')
                return self._snapshot(dict(row))
        return None

    def reserve(self,uid,auth,rid,digest,kind,context,messages,expected_settings_version,coach=False):
        rid=uuid_id(rid)
        if kind not in {'chat','interpretation','review','comparison','coach'} or (kind=='coach')!=coach:raise APIError('VALIDATION_FAILED','AI任务类型无效',422)
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth)
            previous=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==rid)).mappings().first()
            if previous:
                if previous['user_id']!=uid or previous['request_digest']!=digest:raise ConflictError('AI请求标识与内容不匹配','IDEMPOTENCY_CONFLICT')
                return self._snapshot(dict(previous)),False
            active=c.scalar(select(func.count()).select_from(s.ai_requests).where(s.ai_requests.c.user_id==uid,s.ai_requests.c.status.in_(('queued','running'))))
            if active:raise APIError('AI_BUSY','已有AI请求正在处理，请先等待或停止',409)
            settings,policy,_,source=self.settings.active(c,uid)
            if settings['version']!=expected_settings_version:raise ConflictError('AI设置已改变，请重新读取后提问')
            limit=min(1200,policy['max_output_tokens']) if coach else policy['max_output_tokens']
            quote=200000 if coach else estimate_reservation(settings['model'],messages,limit)
            if source=='system':
                if policy['per_user_daily_requests'] and self.settings.used_today(c,uid)>=policy['per_user_daily_requests']:raise APIError('AI_DAILY_QUOTA_EXHAUSTED','今日系统AI体验次数已用完，可主动选择个人密钥',429)
                updated=c.execute(update(s.ai_system_policy).where(s.ai_system_policy.c.id==1,s.ai_system_policy.c.enabled==1,
                    (s.ai_system_policy.c.budget_micro_cny==0)|(s.ai_system_policy.c.budget_micro_cny>=s.ai_system_policy.c.spent_micro_cny+s.ai_system_policy.c.reserved_micro_cny+quote))
                    .values(reserved_micro_cny=s.ai_system_policy.c.reserved_micro_cny+quote,updated_at_ms=self.clock()))
                if updated.rowcount!=1:raise APIError('AI_SYSTEM_BUDGET_EXHAUSTED','系统AI额度不足，可稍后再试或主动选择个人密钥',429)
            c.execute(s.ai_requests.insert().values(id=rid,user_id=uid,auth_id=auth,kind=kind,source=source,status='queued',
                model=settings['model'],request_digest=digest,context_json=json_text(context),reserved_micro_cny=quote if source=='system' else 0,
                settings_version=settings['version'],policy_version=policy['version'],max_output_tokens=limit,pricing_version=PRICING_VERSION,
                created_at_ms=self.clock()))
            return self._snapshot(self._row(c,uid,rid)),True

    def resume_coach(self,uid,auth,rid):
        """恢复同一父任务，保留次数/累计支出，不另建账本行。"""
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth);row=self._row(c,uid,rid)
            if row['kind']!='coach' or row['status']!='succeeded':raise ConflictError('该任务无法继续')
            settings,policy,_,source=self.settings.active(c,uid)
            if source!=row['source'] or settings['version']!=row['settings_version'] or source=='system' and policy['version']!=row['policy_version']:raise ConflictError('AI设置已改变，请重新开始')
            if c.scalar(select(func.count()).select_from(s.ai_requests).where(s.ai_requests.c.user_id==uid,s.ai_requests.c.status.in_(('queued','running')))):raise APIError('AI_BUSY','已有AI请求正在处理',409)
            quote=max(0,200000-(row['estimated_micro_cny'] or 0))
            if source=='system':
                changed=c.execute(update(s.ai_system_policy).where(s.ai_system_policy.c.id==1,s.ai_system_policy.c.enabled==1,
                    (s.ai_system_policy.c.budget_micro_cny==0)|(s.ai_system_policy.c.budget_micro_cny>=s.ai_system_policy.c.spent_micro_cny+s.ai_system_policy.c.reserved_micro_cny+quote))
                    .values(reserved_micro_cny=s.ai_system_policy.c.reserved_micro_cny+quote))
                if changed.rowcount!=1:raise APIError('AI_SYSTEM_BUDGET_EXHAUSTED','系统额度不足',429)
            c.execute(update(s.ai_requests).where(s.ai_requests.c.id==rid).values(status='queued',ended_at_ms=None,
                estimated_micro_cny=None,reserved_micro_cny=quote if source=='system' else 0,state_version=row['state_version']+1))
            return self._snapshot(self._row(c,uid,rid))

    def begin(self,uid,rid):
        with self.db.transaction() as c:
            row=self._row(c,uid,rid)
            if row['status']!='queued':return None
            self.sessions._actor(c,uid,row['auth_id'])
            settings,policy,key,source=self.settings.active(c,uid)
            if source!=row['source'] or settings['version']!=row['settings_version'] or source=='system' and policy['version']!=row['policy_version']:
                raise APIError('AI_CONTEXT_CHANGED','AI设置已改变，本次请求未发送',409)
            c.execute(update(s.ai_requests).where(s.ai_requests.c.id==rid).values(status='running',started_at_ms=self.clock(),state_version=row['state_version']+1))
            return {'key':key,'model':row['model'],'max_output_tokens':row['max_output_tokens'],'auth_id':row['auth_id']}

    def _settle(self,c,row,cost=None,uncertain=False):
        reserve=row['reserved_micro_cny']
        if row['source']=='system' and not uncertain:
            changed=c.execute(update(s.ai_system_policy).where(s.ai_system_policy.c.id==1,s.ai_system_policy.c.reserved_micro_cny>=reserve)
                .values(reserved_micro_cny=s.ai_system_policy.c.reserved_micro_cny-reserve,
                        spent_micro_cny=s.ai_system_policy.c.spent_micro_cny+(cost or 0),updated_at_ms=self.clock()))
            if changed.rowcount!=1:raise StorageError('AI额度预留状态不一致，原请求保留待核对')

    def finish(self,uid,rid,result=None,usage=None,cost=None,error=None,uncertain=False,prior_cost=0):
        if cost is not None:integer(cost,'cost_micro_cny')
        with self.db.transaction() as c:
            row=self._row(c,uid,rid)
            if row['status'] in TERMINAL:return self._snapshot(row)
            self._settle(c,row,cost,uncertain)
            source_deleted=row['error_code']=='AI_SOURCE_DELETED'
            state='uncertain' if uncertain else 'failed' if source_deleted else 'cancelled' if row['cancel_requested'] else 'failed' if error else 'succeeded'
            if state=='succeeded' and not isinstance(result,dict):raise ValueError('成功AI任务需要已验证结果')
            c.execute(update(s.ai_requests).where(s.ai_requests.c.id==rid).values(status=state,
                result_json=json_text(result) if state=='succeeded' and row['kind'] in {'review','comparison'} else None,usage_json=json_text(usage) if usage else None,
                estimated_micro_cny=None if uncertain else prior_cost+(cost or 0),error_code='AI_SOURCE_DELETED' if source_deleted else error,
                reserved_micro_cny=row['reserved_micro_cny'] if uncertain else 0,ended_at_ms=self.clock(),state_version=row['state_version']+1))
            return self._snapshot(self._row(c,uid,rid))

    def cancel(self,uid,auth,rid,version):
        integer(version,'expected_version')
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth);row=self._row(c,uid,uuid_id(rid))
            if row['status'] in TERMINAL:return self._snapshot(row)
            if row['state_version']!=version:raise ConflictError('AI请求状态已变化，请重新读取')
            changes={'cancel_requested':1,'state_version':row['state_version']+1}
            if row['status']=='queued':
                self._settle(c,row,0)
                changes.update(status='cancelled',reserved_micro_cny=0,estimated_micro_cny=0,ended_at_ms=self.clock())
            c.execute(update(s.ai_requests).where(s.ai_requests.c.id==rid).values(**changes))
            return self._snapshot(self._row(c,uid,rid))

    def cancellation_requested(self,uid,rid):
        with self.db.transaction(False) as c:
            row=self._row(c,uid,rid);return bool(row['cancel_requested'] or row['status']!='running')

    def clear_source_records(self,c,uid,ids):
        """在记录删除事务内清除衍生正文/停止排队；不加AI内存锁、不丢实际费用。"""
        from .training_insights import filters
        table=s.ai_requests
        rows=c.execute(select(table.c.id,table.c.source,table.c.status,table.c.error_code,table.c.state_version,table.c.context_json,table.c.reserved_micro_cny)
            .where(table.c.user_id==uid,table.c.result_json.is_not(None)|table.c.status.in_(('queued','running')))).mappings().all()
        removed=set(ids)
        for raw in rows:
            row=dict(raw);ref=json.loads(row['context_json'])
            direct=set(ref.get('record_ids',[]))|{v['id'] for v in ref.get('dependencies',[])}
            source=(ref.get('live_dependency') or {}).get('source_record_id')
            affected=bool(direct&removed or source in removed)
            if not affected and ref.get('history_fingerprint'):
                history=dict(ref.get('history_filters') or {});action=history.pop('action_id',None)
                conditions=filters(uid,history,ref['as_of_ms'])
                if action:conditions.append(s.analysis_sessions.c.action_id==action)
                affected=c.execute(select(s.analysis_sessions.c.id).where(*conditions,s.analysis_sessions.c.id.in_(ids)).limit(1)).first() is not None
            if not affected or row['error_code']=='AI_SOURCE_DELETED':continue
            changes={'result_json':None,'error_code':'AI_SOURCE_DELETED','state_version':row['state_version']+1}
            if row['status']=='queued':
                self._settle(c,row,0);changes.update(status='cancelled',cancel_requested=1,reserved_micro_cny=0,estimated_micro_cny=0,ended_at_ms=self.clock())
            elif row['status']=='running':changes['cancel_requested']=1
            c.execute(update(table).where(table.c.id==row['id'],table.c.user_id==uid).values(**changes))

    def recover(self):
        with self.db.transaction() as c:
            rows=c.execute(select(s.ai_requests).where(s.ai_requests.c.status.in_(('queued','running')))).mappings().all()
            for raw in rows:
                row=dict(raw);uncertain=row['status']=='running'
                if not uncertain:self._settle(c,row,0)
                c.execute(update(s.ai_requests).where(s.ai_requests.c.id==row['id']).values(
                    status='uncertain' if uncertain else 'cancelled',error_code='AI_SERVICE_STOPPED',
                    reserved_micro_cny=row['reserved_micro_cny'] if uncertain else 0,
                    estimated_micro_cny=None if uncertain else 0,ended_at_ms=self.clock(),state_version=row['state_version']+1))

    def reconcile(self,admin_uid,auth,rid,version,cost,note):
        integer(version,'expected_version');integer(cost,'estimated_micro_cny')
        if not isinstance(note,str) or not 1<=len(note.strip())<=300 or 'sk-' in note:raise APIError('VALIDATION_FAILED','请填写不含密钥的核对依据',422)
        with self.db.transaction() as c:
            self.settings.admin(c,admin_uid,auth)
            raw=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==uuid_id(rid))).mappings().first()
            if not raw:raise APIError('NOT_FOUND','AI请求不存在',404)
            row=dict(raw)
            if row['status']!='uncertain' or row['state_version']!=version:raise ConflictError('请求不再处于原待核对状态')
            self._settle(c,row,cost)
            c.execute(update(s.ai_requests).where(s.ai_requests.c.id==rid).values(status='failed',reserved_micro_cny=0,estimated_micro_cny=cost,
                usage_json=json_text({'reconciliation_source':'admin_entered','note':note.strip(),'reconciled_by':admin_uid}),
                ended_at_ms=self.clock(),state_version=row['state_version']+1))
            return {'request_id':rid,'estimated_micro_cny':cost,'usage_is_provider_bill':False,'reconciled':True}
