"""本人/系统AI密钥与额度配置；保存不产生生成调用，不静默切换计费来源。"""
import os,threading
from decimal import Decimal,InvalidOperation
from datetime import datetime,timedelta,timezone
from flask import jsonify,request,current_app
from flask_login import current_user
from sqlalchemy import select,update,func,and_,or_
from . import db_schema as s
from .auth import APIError,body,clock
from .database import ConflictError,idempotent
from .records import integer,uuid_id
from .training_info import invalid
from .deepseek_client import DeepSeekClient,MODELS,PRICING_VERSION,validate_key,ProviderError
from .ai_secrets import SecretVault
from .paths import project_path

SETTINGS_FIELDS={'source','model','personal_key','history_reference'}
POLICY_FIELDS={'enabled','api_key','budget_yuan','per_user_daily_requests','max_output_tokens'}


def money(value,field):
    if type(value) not in (str,int,float):invalid(field)
    try:number=Decimal(str(value))
    except InvalidOperation:invalid(field)
    if not number.is_finite() or not 0<=number<=1000000 or number.as_tuple().exponent < -2:invalid(field,'预算必须为非负金额，最多两位小数')
    return int(number*1000000)


def secret(value,field):
    try:return validate_key(value)
    except ValueError as error:invalid(field,str(error))


def model_id(value):
    if not isinstance(value,str) or value not in MODELS:invalid('model','请选择受支持的DeepSeek模型')
    return value


def boolean(value,field):
    if type(value) is not bool:invalid(field,'请输入布尔值')
    return value


def provider_error(error):
    status=422 if error.code in {'AI_KEY_INVALID','AI_BALANCE_INSUFFICIENT','AI_REQUEST_REJECTED'} else 429 if error.code=='AI_RATE_LIMITED' else 503
    raise APIError(error.code,str(error),status) from None


class AISettingsStore:
    def __init__(self,sessions,vault,clock=s.now_ms,system_key=None):
        self.sessions=sessions;self.db=sessions.db;self.vault=vault;self.clock=clock;self.environment_key=system_key

    def policy(self,c):
        row=c.execute(select(s.ai_system_policy).where(s.ai_system_policy.c.id==1)).mappings().first()
        return {**row,'_exists':True} if row else {'_exists':False,'id':1,'version':0,'enabled':0,'key_ciphertext':None,'key_suffix':None,
                'budget_micro_cny':0,'spent_micro_cny':0,'reserved_micro_cny':0,'per_user_daily_requests':0,'max_output_tokens':1800}

    def personal(self,c,uid):
        row=c.execute(select(s.ai_settings).where(s.ai_settings.c.user_id==uid)).mappings().first()
        return {**row,'_exists':True} if row else {'_exists':False,'user_id':uid,'version':0,'source':'system','model':'deepseek-flash',
                'key_ciphertext':None,'key_suffix':None,'history_reference':1,'verified_at_ms':None}

    def _system_configured(self,p):return bool(p['key_ciphertext'] or self.environment_key)

    def _seal(self,c,owner,key):
        encrypted=c.scalar(select(func.count()).select_from(s.ai_settings).where(s.ai_settings.c.key_ciphertext.is_not(None)))
        encrypted+=c.scalar(select(func.count()).select_from(s.ai_system_policy).where(s.ai_system_policy.c.key_ciphertext.is_not(None)))
        # 已有密文时绝不自动生成另一主密钥，否则会破坏其他用户恢复原配置的机会。
        return self.vault.seal(owner,key,create=not encrypted)

    def used_today(self,c,uid):
        now=datetime.fromtimestamp(self.clock()/1000,timezone(timedelta(hours=8)))
        start=int(now.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()*1000)
        return c.scalar(select(func.count()).select_from(s.ai_requests).where(s.ai_requests.c.user_id==uid,
            s.ai_requests.c.source=='system',s.ai_requests.c.created_at_ms>=start,
            or_(s.ai_requests.c.started_at_ms.is_not(None),s.ai_requests.c.status.in_(('queued','running')))))

    def snapshot(self,c,uid):
        row=self.personal(c,uid);p=self.policy(c);used=self.used_today(c,uid)
        remaining=max(0,p['per_user_daily_requests']-used) if p['per_user_daily_requests'] else None
        system_available=bool(p['enabled'] and self._system_configured(p) and (remaining is None or remaining>0) and
                              (not p['budget_micro_cny'] or p['budget_micro_cny']>p['spent_micro_cny']+p['reserved_micro_cny']))
        personal_available=bool(row['key_ciphertext'] and self.vault.path.is_file())
        configured=system_available if row['source']=='system' else personal_available
        reason=None if configured else 'AI_PERSONAL_KEY_MISSING' if row['source']=='personal' and not row['key_ciphertext'] else 'AI_KEY_STORAGE_UNAVAILABLE' if row['source']=='personal' else 'AI_SYSTEM_UNAVAILABLE'
        return {'provider':'deepseek','source':row['source'],'model':row['model'],'version':row['version'],
                'history_reference':bool(row['history_reference']),'personal_key_configured':bool(row['key_ciphertext']),
                'personal_key_mask':('••••'+row['key_suffix']) if row['key_suffix'] else None,
                'personal_key_verified_at_ms':row.get('verified_at_ms'),'system_available':system_available,
                'system_daily_remaining':remaining,'system_used_today':used,'available':configured,'reason':reason,
                'pricing_version':PRICING_VERSION,'usage_is_provider_bill':False,
                'models':[{'id':k,'label':v['label']} for k,v in MODELS.items()]}

    def get(self,uid,auth):
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth);return self.snapshot(c,uid)

    def write(self,uid,auth,data,key,remove=False):
        version=integer(data.get('expected_version'),'expected_version');key=uuid_id(key)
        changes={}
        if 'source' in data:
            if not isinstance(data['source'],str) or data['source'] not in {'system','personal'}:invalid('source')
            changes['source']=data['source']
        if 'model' in data:changes['model']=model_id(data['model'])
        if 'history_reference' in data:changes['history_reference']=int(boolean(data['history_reference'],'history_reference'))
        raw=data.get('personal_key')
        if raw is not None and not isinstance(raw,str):invalid('personal_key')
        raw=secret(raw,'personal_key') if raw else None
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth)
            def change():
                old=self.personal(c,uid)
                if old['version']!=version:raise ConflictError('AI设置已更新，请重新读取')
                values={**changes,'version':version+1,'updated_at_ms':self.clock()}
                if remove:values.update(key_ciphertext=None,key_suffix=None,verified_at_ms=None)
                elif raw:values.update(key_ciphertext=self._seal(c,'user:'+uid,raw),key_suffix=raw[-4:],verified_at_ms=None)
                if old['_exists']:c.execute(update(s.ai_settings).where(s.ai_settings.c.user_id==uid).values(**values))
                else:c.execute(s.ai_settings.insert().values(user_id=uid,**values))
                return {'id':uid,'version':version+1}
            idempotent(c,uid,key,{**data,'remove':remove},'ai.settings',change,uid)
            return self.snapshot(c,uid)

    def active(self,c,uid,source=None):
        row=self.personal(c,uid);p=self.policy(c);source=source or row['source']
        if source=='personal':
            if not row['key_ciphertext']:raise APIError('AI_PERSONAL_KEY_MISSING','请设置个人密钥，或主动选择系统额度',409)
            key=self.vault.open('user:'+uid,row['key_ciphertext'])
        else:
            if not p['enabled'] or not self._system_configured(p):raise APIError('AI_SYSTEM_UNAVAILABLE','请配置系统AI服务或使用个人密钥',409)
            key=self.vault.open('system',p['key_ciphertext']) if p['key_ciphertext'] else secret(self.environment_key,'system_key')
        return row,p,key,source

    def admin(self,c,uid,auth):
        self.sessions._actor(c,uid,auth)
        if c.scalar(select(s.users.c.role).where(s.users.c.id==uid))!='admin':raise APIError('FORBIDDEN','仅管理员可管理系统AI额度',403)

    def admin_snapshot(self,c):
        p=self.policy(c)
        return {k:p[k] for k in ('enabled','budget_micro_cny','spent_micro_cny','reserved_micro_cny','per_user_daily_requests','max_output_tokens','version')}|{
            'enabled':bool(p['enabled']),'system_key_configured':self._system_configured(p),
            'system_key_mask':'••••'+p['key_suffix'] if p['key_suffix'] else None,
            'environment_key_configured':bool(self.environment_key),'pricing_version':PRICING_VERSION,'usage_is_provider_bill':False}

    def admin_write(self,uid,auth,data,key):
        version=integer(data.get('expected_version'),'expected_version');key=uuid_id(key);values={}
        if 'enabled' in data:values['enabled']=int(boolean(data['enabled'],'enabled'))
        if 'budget_yuan' in data:values['budget_micro_cny']=money(data['budget_yuan'],'budget_yuan')
        if 'per_user_daily_requests' in data:values['per_user_daily_requests']=integer(data['per_user_daily_requests'],'per_user_daily_requests',0,10000)
        if 'max_output_tokens' in data:values['max_output_tokens']=integer(data['max_output_tokens'],'max_output_tokens',128,8192)
        raw=data.get('api_key')
        if raw is not None and not isinstance(raw,str):invalid('api_key')
        raw=secret(raw,'api_key') if raw else None
        with self.db.transaction() as c:
            self.admin(c,uid,auth)
            def change():
                old=self.policy(c)
                if old['version']!=version:raise ConflictError('系统AI策略已更新，请重新读取')
                changed={**values,'version':version+1,'updated_at_ms':self.clock()}
                if raw:changed.update(key_ciphertext=self._seal(c,'system',raw),key_suffix=raw[-4:])
                final={**old,**changed}
                if final['budget_micro_cny'] and final['budget_micro_cny']<final['spent_micro_cny']+final['reserved_micro_cny']:invalid('budget_yuan','请将预算设置为已用与预留金额之和以上')
                if final['enabled'] and not self._system_configured(final):
                    invalid('enabled','启用前需要系统密钥')
                if old['_exists']:c.execute(update(s.ai_system_policy).where(s.ai_system_policy.c.id==1).values(**changed))
                else:c.execute(s.ai_system_policy.insert().values(id=1,**changed))
                return {'id':'00000000-0000-0000-0000-000000000001','version':version+1}
            idempotent(c,uid,key,data,'ai.system-policy',change)
            return self.admin_snapshot(c)


def register_ai_settings(app):
    sessions=app.extensions['session_store']
    vault=SecretVault(project_path(sessions.db.path.parent/'private/ai-master.key'))
    store=AISettingsStore(sessions,vault,app.config.get('CLOCK') or s.now_ms,app.config.get('AI_SYSTEM_KEY') or os.environ.get('FITNESS_DEEPSEEK_API_KEY'))
    app.extensions['ai_settings']=store
    factory=app.config.get('AI_PROVIDER_FACTORY',DeepSeekClient) if app.config.get('TESTING') else DeepSeekClient
    app.extensions['ai_provider_factory']=factory
    test_windows={};test_lock=threading.Lock()

    def allow_connection_test(uid):
        now=store.clock()
        with test_lock:
            for owner,times in list(test_windows.items()):
                if not times or now-times[-1]>=60000:test_windows.pop(owner,None)
            times=[t for t in test_windows.get(uid,[]) if 0<=now-t<60000]
            if len(times)>=5 or len(test_windows)>=5000 and uid not in test_windows:
                raise APIError('AI_TEST_RATE_LIMITED','连接测试过于频繁，请稍后再试',429)
            test_windows[uid]=times+[now]

    @app.get('/api/v1/me/ai-settings')
    def ai_settings_get():return jsonify(data=store.get(current_user.user_id,current_user.id))

    @app.patch('/api/v1/me/ai-settings')
    def ai_settings_write():
        data=body(SETTINGS_FIELDS|{'expected_version'})
        if not SETTINGS_FIELDS&data.keys():invalid('settings','请修改至少一项设置')
        return jsonify(data=store.write(current_user.user_id,current_user.id,data,request.headers.get('Idempotency-Key')))

    @app.delete('/api/v1/me/ai-key')
    def ai_key_remove():
        return jsonify(data=store.write(current_user.user_id,current_user.id,body({'expected_version'}),request.headers.get('Idempotency-Key'),remove=True))

    @app.post('/api/v1/me/ai-settings/test')
    def ai_settings_test():
        data=body({'source','personal_key','model'});candidate=data.get('personal_key')
        allow_connection_test(current_user.user_id)
        source=data.get('source')
        if source is not None and (not isinstance(source,str) or source not in {'system','personal'}):invalid('source')
        if 'model' in data:model_id(data['model'])
        if candidate:candidate=secret(candidate,'personal_key')
        elif candidate is not None and not isinstance(candidate,str):invalid('personal_key')
        with store.db.transaction(False) as c:
            sessions._actor(c,current_user.user_id,current_user.id)
            row=store.personal(c,current_user.user_id)
            if candidate:
                if source not in (None,'personal'):invalid('source','个人候选密钥需使用个人来源')
                key=candidate
            else:row,_,key,source=store.active(c,current_user.user_id,source)
            version=row['version']
        try:result=current_app.extensions['ai_provider_factory']().test(key)
        except ProviderError as error:provider_error(error)
        if not candidate and source=='personal':
            with store.db.transaction() as c:
                sessions._actor(c,current_user.user_id,current_user.id)
                c.execute(update(s.ai_settings).where(s.ai_settings.c.user_id==current_user.user_id,s.ai_settings.c.version==version).values(verified_at_ms=clock()))
        return jsonify(data={**result,'candidate_only':bool(candidate),'generation_verified':False})

    @app.route('/api/v1/admin/ai-policy',methods=['GET','PATCH'])
    def ai_policy():
        if request.method=='PATCH':
            return jsonify(data=store.admin_write(current_user.user_id,current_user.id,body(POLICY_FIELDS|{'expected_version'}),request.headers.get('Idempotency-Key')))
        with store.db.transaction(False) as c:
            store.admin(c,current_user.user_id,current_user.id);result=store.admin_snapshot(c)
        return jsonify(data=result)
