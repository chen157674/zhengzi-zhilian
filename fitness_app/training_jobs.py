"""计划草稿与周解释的有界临时任务；原账本结算，明确保存才进入计划。"""
import copy,datetime as dt,hashlib,hmac,json,secrets,threading,time
from flask import request,jsonify
from flask_login import current_user
from . import db_schema as s
from .auth import APIError,body
from .records import uuid_id,integer
from .database import ConflictError,json_text
from .ai_usage import AIUsageStore,TERMINAL
from .deepseek_client import DeepSeekClient,ProviderError
from .coach_contracts import TOOL_DEFINITIONS,tool_quote
from .training_plans import clean_draft,clean_constraints,date,text,invalid

class TrainingJobs:
    def __init__(self,app):
        self.app=app;self.plans=app.extensions['training_plans'];self.usage=AIUsageStore(app.extensions['ai_settings']);self.jobs={};self.lock=threading.RLock();self.secret=secrets.token_bytes(32);self.closed=False;self.workers=set()
    def actor(self,uid,auth):
        with self.plans.db.transaction(False) as c:self.plans.actor(c,uid,auth)
    def prune(self):
        now=time.monotonic()
        for identifier,job in list(self.jobs.items()):
            if job['expires']<now:
                job.update(cancel=True,result=None,inputs={},sources=[],state='expired')
                # 到期清正文同时收尾排队预留；已发送调用仅请求取消，晚到费用仍正常结算。
                with self.usage.db.transaction() as c:
                    from sqlalchemy import select,update
                    row=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==identifier,s.ai_requests.c.user_id==job['uid'])).mappings().first()
                    if row and row['status']=='queued':
                        self.usage._settle(c,dict(row),0)
                        c.execute(update(s.ai_requests).where(s.ai_requests.c.id==identifier).values(status='cancelled',cancel_requested=1,reserved_micro_cny=0,estimated_micro_cny=0,ended_at_ms=self.plans.clock(),state_version=row['state_version']+1))
                    elif row and row['status']=='running' and not row['cancel_requested']:
                        c.execute(update(s.ai_requests).where(s.ai_requests.c.id==identifier).values(cancel_requested=1,state_version=row['state_version']+1))
                if job.get('thread') is None or not job['thread'].is_alive():self.jobs.pop(identifier,None)
    def _job(self,uid,auth,jid,kind):
        self.actor(uid,auth);self.prune();job=self.jobs.get(uuid_id(jid))
        if not job or job['uid']!=uid or job['auth']!=auth or job['kind']!=kind:raise APIError('EPHEMERAL_CONTEXT_LOST','临时任务已关闭或过期，请重新生成',410)
        return job
    def current(self,job):
        self.actor(job['uid'],job['auth'])
        if job['cancel'] or job['expires']<time.monotonic():raise APIError('AI_CANCELLED','任务已停止或过期',409)
        with self.usage.db.transaction(False) as c:
            row=self.usage._row(c,job['uid'],job['id']);settings=self.usage.settings.personal(c,job['uid']);policy=self.usage.settings.policy(c)
            if settings['version']!=row['settings_version'] or settings['source']!=row['source'] or row['source']=='system' and (not policy['enabled'] or policy['version']!=row['policy_version']):raise APIError('SOURCE_CHANGED','AI设置或来源已变化',409)
        if job.get('profile_version') is not None and self.app.extensions['training_info'].get_profile(job['uid'],job['auth'])['version']!=job['profile_version']:raise APIError('SOURCE_CHANGED','采用的档案已更新',409)
        for row in job['sources']:
            current=self.app.extensions['knowledge_store'].published(row['id'],row['version'])
            if not current or current['status']!='published':raise APIError('SOURCE_CHANGED','参考知识已失效',409)
        if job['kind']=='review' and self.app.extensions['training_reviews'].facts(job['uid'],job['auth'],job['inputs']['week_start'])['data_version']!=job['inputs']['data_version']:raise APIError('SOURCE_CHANGED','本周事实已变化，请重新读取',409)
    def prepare(self,uid,auth,data,kind):
        if data.get('consent') is not True:raise APIError('AI_CONSENT_REQUIRED','请确认本次发送的范围',422)
        profile=None;sources=[]
        if kind=='plan':
            goal=data.get('goal')
            from .training_info import GOALS
            if not isinstance(goal,str) or goal not in GOALS|{'general'}:invalid('goal')
            start=date(data.get('start_date'),'start_date');weeks=integer(data.get('weeks'),'weeks',1,8)
            if start.weekday()!=0:invalid('start_date')
            constraints=clean_constraints(data.get('constraints'))
            if type(data.get('use_profile'))is not bool:invalid('use_profile')
            if data['use_profile']:
                profile=self.app.extensions['training_info'].get_profile(uid,auth)
                if integer(data.get('profile_expected_version'),'profile_expected_version')!=profile['version']:raise ConflictError('采用档案版本已变化')
            if data.get('adult_confirmed') is not True:raise APIError('AI_PLAN_ADULT_REQUIRED','AI一般训练草稿需明确本人健康成人适用条件，其他情况可手动安排',422)
            for aid in ('machine_chest_press','standing_dumbbell_curl','bodyweight_squat'):
                result=self.app.extensions['knowledge_store'].retrieval({'machine_chest_press':'推胸控制','standing_dumbbell_curl':'弯举控制','bodyweight_squat':'深蹲控制'}[aid],'movement',aid,2)
                sources.extend(result['matches'])
            if not sources:raise APIError('KNOWLEDGE_UNAVAILABLE','没有适用方法来源，仍可手动创建计划',422)
            inputs={'goal':goal,'start_date':start.isoformat(),'weeks':weeks,'constraints':constraints}
        else:
            if data.get('history_consent') is not True:raise APIError('AI_CONSENT_REQUIRED','请明确采用本周训练摘要',422)
            facts=self.app.extensions['training_reviews'].facts(uid,auth,data.get('week_start'))
            if facts['data_version']!=data.get('data_version'):raise ConflictError('本周事实已变化')
            inputs={'week_start':facts['week_start'],'data_version':facts['data_version'],'facts':facts}
        return inputs,sources,profile
    def create(self,uid,auth,jid,data,kind):
        jid=uuid_id(jid);self.actor(uid,auth);digest=hmac.new(self.secret,json_text(data).encode(),hashlib.sha256).hexdigest()
        with self.lock:
            self.prune()
            if jid in self.jobs:
                job=self._job(uid,auth,jid,kind)
                if job['digest']!=digest:raise ConflictError('原任务标识与本次条件不同','IDEMPOTENCY_CONFLICT')
                return self.get(uid,auth,jid,kind)
            if len(self.jobs)>=64:raise APIError('AI_BUSY','临时任务较多，请稍后重试',429)
            inputs,sources,profile=self.prepare(uid,auth,data,kind)
            settings=self.app.extensions['ai_settings'].get(uid,auth)
            if settings['model']!='deepseek-flash':raise APIError('COACH_MODEL_UNAVAILABLE','请在AI设置选择快速解读',422)
            self.usage.reserve(uid,auth,jid,secrets.token_hex(32),'coach',{'storage_mode':'ephemeral','protocol':'training-'+kind+'-1'},[],settings['version'],coach=True)
            job={'id':jid,'uid':uid,'auth':auth,'kind':kind,'digest':digest,'inputs':inputs,'sources':sources,'profile_version':profile['version'] if profile else None,'state':'queued','state_version':0,'result_version':0,'result':None,'events':[{'seq':1,'kind':'prepare','label':'准备本次明确范围','status':'completed'}],'cancel':False,'expires':time.monotonic()+1800,'error':None,'usage_summary':None,'created_at_ms':self.plans.clock()}
            self.jobs[jid]=job;t=threading.Thread(target=self.work,args=(job,),daemon=True,name='training-'+kind);job['thread']=t;self.workers.add(t);t.start();return self.get(uid,auth,jid,kind)
    def get(self,uid,auth,jid,kind):
        with self.lock:
            job=self._job(uid,auth,jid,kind)
            if job['state'] in {'ready','partial'}:
                try:self.current(job)
                except APIError:job.update(state='source_invalid',result=None,inputs={},sources=[])
            return {k:job[k] for k in ('id','state_version','result_version','created_at_ms','events','error','usage_summary')}|{'status':job['state'],'result':copy.deepcopy(job['result']),'validated_draft':copy.deepcopy(job['result'].get('draft')) if job['result'] and kind=='plan' else None,'expires_at_ms':self.plans.clock()+max(0,int((job['expires']-time.monotonic())*1000))}
    def cancel(self,uid,auth,jid,kind):
        with self.lock:
            job=self._job(uid,auth,jid,kind);job.update(cancel=True,result=None,state='cancelled',state_version=job['state_version']+1,inputs={},sources=[])
            row=self.usage.get(uid,auth,jid)
            if row['status'] not in TERMINAL:self.usage.cancel(uid,auth,jid,row['state_version'])
            return self.get(uid,auth,jid,kind)
    def prompt(self,job):
        if job['kind']=='plan':
            schema={'title':'计划标题','weekly_sessions':[{'weekday':1,'title':'安排名称','estimated_minutes':30,'items':[{'action_id':'bodyweight_squat','target_sets':2,'target_reps':10,'target_duration_ms':None,'rest_seconds':60}]}],'knowledge_ids':['实际条目ID']}
            system='根据本次明确条件和资料，只生成本人健康成人一般训练草稿，不作处方。只用资料中覆盖且匹配器械的动作，不猜身体资料。返回严格JSON：'+json.dumps(schema,ensure_ascii=False)+'。每周日期来自training_weekdays，保持时间与次数按动作单位互斥。服务器展开日期，模型不能保存或启用。'
            data={'conditions':job['inputs'],'sources':[{k:r[k] for k in ('id','action_id','text','source_type')} for r in job['sources']]}
        else:
            system='仅解释本次授权周内事实，不改写或计算新数字，不声称长期训练效果。返回JSON：summary（≤500字）、focus（≤3条文字）、evidence_refs（只用事实中的真实记录ID）、knowledge_refs（空数组）。'
            data=job['inputs']
        return [{'role':'system','content':system},{'role':'user','content':json.dumps(data,ensure_ascii=False,allow_nan=False)}]
    def validate_plan(self,job,raw):
        if not isinstance(raw,dict) or set(raw)!={'title','weekly_sessions','knowledge_ids'}:invalid('ai_result','AI草稿结构不完整，请改用手动安排')
        refs=raw['knowledge_ids'];allowed={r['id']:r for r in job['sources']}
        if not isinstance(refs,list) or not refs or len(refs)>10 or any(not isinstance(x,str) or x not in allowed for x in refs):invalid('knowledge_ids','草稿缺少本次适用引用')
        week=raw['weekly_sessions'];conditions=job['inputs'];days=conditions['constraints']['training_weekdays']
        if not isinstance(week,list) or not 1<=len(week)<=7:invalid('weekly_sessions')
        mapping={}
        for session in week:
            if not isinstance(session,dict) or set(session)!={'weekday','title','estimated_minutes','items'}:invalid('weekly_sessions')
            weekday=integer(session['weekday'],'weekday',1,7)
            if weekday not in days or weekday in mapping:invalid('weekday')
            if not isinstance(session['items'],list) or not 1<=len(session['items'])<=12:invalid('items')
            for item in session['items']:
                if not isinstance(item,dict) or set(item)!={'action_id','target_sets','target_reps','target_duration_ms','rest_seconds'}:invalid('items')
                if not any(allowed[r]['action_id']==item['action_id'] for r in refs):invalid('action_id','生成动作没有适用知识引用')
                from .catalogue import get_action
                action=get_action(item['action_id']);equipment=conditions['constraints']['equipment']
                equipment_type={'bodyweight_squat':'bodyweight','standing_dumbbell_curl':'dumbbell','machine_chest_press':'machine'}.get(item['action_id'])
                if equipment and equipment_type not in equipment:invalid('equipment','生成动作不匹配明确器械')
            mapping[weekday]=session
        if set(mapping)!=set(days):invalid('training_weekdays','生成安排缺少训练日，请手动完善')
        sessions=[];start=date(conditions['start_date'])
        for index in range(conditions['weeks']*7):
            current=start+dt.timedelta(days=index);source=mapping.get(current.isoweekday())
            if not source:continue
            sessions.append({'client_id':'ai-day-'+str(index),'date':current.isoformat(),'day_kind':'training','title':source['title'],'estimated_minutes':source['estimated_minutes'],
                'items':[{'client_id':'ai-item-'+str(index)+'-'+str(j),'variant':None,'load':None,'focus_task_id':None,**x} for j,x in enumerate(source['items'])]})
        draft=clean_draft({'title':raw['title'],'goal':conditions['goal'],'start_date':conditions['start_date'],'weeks':conditions['weeks'],'timezone':'Asia/Shanghai','constraints':conditions['constraints'],'sessions':sessions})
        return {'draft':draft,'knowledge_sources':[allowed[r] for r in refs],'template_version':'bounded-weekly-plan-1','warnings':['一般训练草稿，启用前核对实际能力与条件。'],'missing_fields':[]}
    def work(self,job):
        cost=0;usage=None;uncertain=False
        try:
            self.current(job);run=self.usage.begin(job['uid'],job['id'])
            if not run:return
            job.update(state='running',state_version=job['state_version']+1);messages=self.prompt(job)
            tools=[t for t in TOOL_DEFINITIONS if t['function']['name']=='search_knowledge']
            if tool_quote(run['model'],messages,tools,run['max_output_tokens'])>200000:raise APIError('AI_BUDGET_LIMIT','本次上下文超过任务上限',429)
            job['events'].append({'seq':2,'kind':'generate','label':'根据当前来源生成草稿' if job['kind']=='plan' else '解释本周结构化事实','status':'running'})
            self.current(job)
            provider=self.app.extensions.get('training_provider_factory',DeepSeekClient)();response=provider.tool_completion(run['key'],run['model'],messages,tools,max_output_tokens=run['max_output_tokens'],allow_tools=False)
            cost=response['estimated_micro_cny'];usage=response['usage'];self.current(job)
            if type(cost)is not int or cost<0 or cost>200000:raise APIError('AI_BUDGET_LIMIT','本次调用超过任务上限',429)
            if response.get('calls'):raise APIError('AI_TOOL_DENIED','该任务不开放额外工具调用',422)
            raw=json.loads(response['message']['content']);result=self.validate_plan(job,raw) if job['kind']=='plan' else self.app.extensions['training_reviews'].validate(job['inputs'],raw)
            self.current(job);self.usage.finish(job['uid'],job['id'],result={'validated':True},usage=usage,cost=cost)
            with self.lock:
                self.current(job);job.update(state='ready',state_version=job['state_version']+1,result_version=1,result=result,usage_summary={'estimated_micro_cny':cost,'model_requests':1,'tool_calls':3 if job['kind']=='plan' else 0,'provider_bill':False});job['events'][-1]['status']='completed'
        except Exception as error:
            if isinstance(error,ProviderError):
                if error.estimated_micro_cny is not None:cost+=error.estimated_micro_cny
                uncertain=error.uncertain_charge
            code=error.code if isinstance(error,(APIError,ProviderError)) else 'AI_RESULT_INVALID'
            try:self.usage.finish(job['uid'],job['id'],error=code,cost=cost,usage=usage,uncertain=uncertain)
            except Exception:self.app.logger.error('训练AI账本待核对 task_id=%s',job['id'])
            with self.lock:job.update(state='cancelled' if job['cancel'] else 'source_invalid' if code=='SOURCE_CHANGED' else 'failed',error=code,result=None,state_version=job['state_version']+1)
        finally:
            if job['state'] not in {'ready','partial'}:job.update(inputs={},sources=[])
            self.workers.discard(threading.current_thread())
    def save_plan(self,uid,auth,data,key):
        job=self._job(uid,auth,data.get('job_id'),'plan');self.current(job)
        if job['state']!='ready' or data.get('result_version')!=job['result_version']:raise ConflictError('草稿结果已变化或失效')
        edited=clean_draft(data.get('edited_draft'));refs=[{'id':x['id'],'version':x['version']} for x in job['result']['knowledge_sources']]
        return self.plans.create(uid,auth,edited,key,source='ai_draft',refs=refs)
    def revoke(self,user_id,auth_session_id=None):
        uid,auth=user_id,auth_session_id
        with self.lock:
            for job in self.jobs.values():
                if job['uid']!=uid or auth is not None and job['auth']!=auth:continue
                job.update(cancel=True,result=None,inputs={},sources=[],state='cancelled',state_version=job['state_version']+1)
                with self.usage.db.transaction() as c:
                    from sqlalchemy import select,update
                    row=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==job['id'],s.ai_requests.c.user_id==uid)).mappings().first()
                    if row and row['status']=='queued':
                        self.usage._settle(c,dict(row),0)
                        c.execute(update(s.ai_requests).where(s.ai_requests.c.id==job['id']).values(status='cancelled',cancel_requested=1,reserved_micro_cny=0,estimated_micro_cny=0,ended_at_ms=self.plans.clock(),state_version=row['state_version']+1))
                    elif row and row['status']=='running':c.execute(update(s.ai_requests).where(s.ai_requests.c.id==job['id']).values(cancel_requested=1,state_version=row['state_version']+1))

    def stop(self):
        self.closed=True
        for job in self.jobs.values():job['cancel']=True
        for worker in list(self.workers):worker.join(2)

def register_training_jobs(app):
    service=TrainingJobs(app);app.extensions['training_jobs']=service;app.extensions['stop_hooks'].append(service.stop);app.extensions['auth_revoke_hooks'].append(service.revoke)
    @app.post('/api/v1/training/plan-draft-jobs')
    def plan_job_create():return jsonify(data=service.create(current_user.user_id,current_user.id,request.headers.get('Idempotency-Key'),body({'goal','start_date','weeks','constraints','use_profile','profile_expected_version','consent','adult_confirmed'}),'plan')),202
    @app.get('/api/v1/training/plan-draft-jobs/<jid>')
    def plan_job_get(jid):return jsonify(data=service.get(current_user.user_id,current_user.id,jid,'plan'))
    @app.post('/api/v1/training/plan-draft-jobs/<jid>/cancel')
    def plan_job_cancel(jid):body(set());return jsonify(data=service.cancel(current_user.user_id,current_user.id,jid,'plan'))
    @app.post('/api/v1/training/plan-drafts/from-job')
    def plan_job_save():return jsonify(data=service.save_plan(current_user.user_id,current_user.id,body({'job_id','result_version','edited_draft'},max_bytes=131072),request.headers.get('Idempotency-Key'))),201
