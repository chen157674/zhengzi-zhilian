"""本人周事实与有界解释入口；执行场次和分析记录分别计数。"""
import datetime as dt,hashlib,json
from flask import request,jsonify
from flask_login import current_user
from sqlalchemy import select
from . import db_schema as s
from .auth import APIError,body
from .training_plans import invalid,text,ZONE
from .training_insights import record_statistics,observation_groups

class TrainingReviews:
    def __init__(self,app):self.app=app;self.progress=app.extensions['training_progress'];self.plans=app.extensions['training_plans'];self.db=self.plans.db;self.sessions=app.extensions['session_store']
    def facts(self,uid,auth,week_start):
        # 跨模块读取期间发生写入则有界重读，避免新分子与旧分母组合。
        for _ in range(3):
            # 观察连接不执行BEGIN/SELECT，避免读事务把data_version锁在旧快照。
            with self.db.engine.connect() as c:
                before=c.exec_driver_sql('PRAGMA data_version').scalar()
                try:result=self._facts(uid,auth,week_start)
                except APIError:
                    if before==c.exec_driver_sql('PRAGMA data_version').scalar():raise
                    continue
                after=c.exec_driver_sql('PRAGMA data_version').scalar()
            if before==after:return result
        raise APIError('SOURCE_CHANGED','周事实正在变化，请稍后重新读取',409)
    def _facts(self,uid,auth,week_start):
        start=self.progress.monday(week_start);end=start+dt.timedelta(days=6)
        with self.db.transaction(False) as c:self.plans.actor(c,uid,auth)
        progress=self.progress.progress(uid,auth,start.isoformat());records=[];page=1
        while True:
            data=self.sessions.list_records(uid,page=page,page_size=100,date_from=start.isoformat(),date_to=end.isoformat())
            records.extend(self.sessions.snapshot(uid,x['session_id'],True) for x in data['records'])
            if page*100>=data['total']:break
            page+=1
        with self.db.transaction(False) as c:
            self.plans.actor(c,uid,auth)
            left=int(dt.datetime.combine(start,dt.time(),ZONE).timestamp()*1000);right=int(dt.datetime.combine(end+dt.timedelta(days=1),dt.time(),ZONE).timestamp()*1000)
            tasks=c.execute(select(s.coach_saved_retests.c.id).where(s.coach_saved_retests.c.user_id==uid,s.coach_saved_retests.c.created_at_ms>=left,s.coach_saved_retests.c.created_at_ms<right)).scalars().all()
            workouts=c.execute(select(s.training_workouts.c.id).join(s.training_plan_sessions,s.training_plan_sessions.c.id==s.training_workouts.c.plan_session_id).where(s.training_workouts.c.user_id==uid,s.training_plan_sessions.c.scheduled_date>=start.isoformat(),s.training_plan_sessions.c.scheduled_date<=end.isoformat())).scalars().all()
        workout_data=[self.app.extensions['training_workouts'].get(uid,auth,wid) for wid in workouts]
        valid=[x for x in records if x['status'] in {'completed','ended_early'} and x.get('included_in_statistics')is not False and self.app.extensions['persistence'].snapshot(uid,x['session_id'])['save_state']=='clean']
        by_action={};trends=[]
        for record in valid:
            item=by_action.setdefault(record['action_id'],{'action_id':record['action_id'],'action_name':record['action_name'],'record_count':0,'reference_reps':0,'reference_per_side_reps':0,'reference_hold_ms':0})
            item['record_count']+=1
            if record['counting_available']:
                if record['unit']=='seconds':item['reference_hold_ms']+=record['hold_ms']
                elif record['unit']=='per_side_reps':item['reference_per_side_reps']+=min(record['left_count'],record['right_count'])
                else:item['reference_reps']+=record['total_count']
            for stat in record_statistics(record):
                rule=next((x for x in record['supported_items'] if x['code']==stat['code']),{})
                trends.append({'record_id':record['session_id'],'record_version':record['state_version'],'action_id':record['action_id'],'title':rule.get('title',stat['code']),'started_at_ms':record['started_at_ms'],**stat})
        groups={}
        for item in trends:
            key=tuple(item[k] for k in ('action_id','code','side','rule_version','view','unit'))
            group=groups.setdefault(key,{k:item[k] for k in ('action_id','code','title','side','rule_version','view','unit')}|{'records':[],'totals':{'issue':0,'evaluated':0,'unknown':0},'comparability':'same_rule_view_side_only'})
            group['records'].append({k:item[k] for k in ('record_id','record_version','started_at_ms','issue','evaluated','unknown')})
            for metric in ('issue','evaluated','unknown'):group['totals'][metric]+=item[metric]
        for group in groups.values():
            totals=group['totals'];totals['ratio']=totals['issue']/totals['evaluated'] if totals['evaluated'] else None
        retests={'completed':0,'not_comparable':0,'awaiting':0};task_versions=[]
        for tid in tasks:
            try:task=self.app.extensions['coach_retests'].get(uid,auth,tid)
            except APIError:continue
            state=(task.get('comparison')or{}).get('state');task_versions.append((tid,task['state_version'],task.get('practice',{}).get('state_version') if task.get('practice') else None,task['status'],state,(task.get('practice')or{}).get('knowledge_status')))
            retests['completed' if state in {'reobserved','not_reobserved'} else 'not_comparable' if state else 'awaiting']+=1
        identity={'week':start.isoformat(),'progress':progress['data_version'],'records':[(x['session_id'],x['state_version'],x['session_id'] in {r['session_id'] for r in valid}) for x in records],'workouts':[(x['id'],x['state_version']) for x in workout_data],'retests':task_versions}
        version=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        return {'week_start':start.isoformat(),'week_end':end.isoformat(),'timezone':'Asia/Shanghai','data_version':version,'as_of_ms':self.plans.clock(),
                'plan_progress':progress if progress['plan_id'] else None,'actual_training':{'analysis_record_count':len(valid),'execution_session_count':len([x for x in workout_data if x['actual_started_at_ms'] is not None and x['status']!='voided']),'session_count':len([x for x in workout_data if x['actual_started_at_ms'] is not None and x['status']!='voided']),
                    'source_breakdown':{source:sum(x['source']==source for x in valid) for source in ('camera','video')},'per_action':list(by_action.values())},
                'retests':retests,'trends':trends,'trend_groups':observation_groups(valid,record_statistics),'focus_candidates':[{'record_id':r['session_id'],'message':i.get('message'),'suggestion':i.get('suggestion')} for r in valid for i in r.get('posture',{}).get('findings',[]) if i['status']=='issue'][:5],
                'records':[{'record_id':x['session_id'],'state_version':x['state_version'],'action_name':x['action_name'],'source':x['source'],'status':x['status']} for x in valid],
                'excluded':{'demo':'演示不进入本人统计','invalid':len(records)-len(valid),'version_mismatch':'不同规则/侧别/机位分别列出，不混算改善'},'links':{'records':'/records?view=list','plan':'/training/plan'}}
    def validate(self,inputs,raw):
        if not isinstance(raw,dict) or set(raw)!={'summary','focus','evidence_refs','knowledge_refs'}:invalid('ai_result','回顾结构不完整')
        summary=text(raw['summary'],'summary',500)
        focus=raw['focus'];refs=raw['evidence_refs'];facts=inputs['facts'];allowed={x['record_id'] for x in facts['records']}
        if not isinstance(focus,list) or len(focus)>3 or any(not isinstance(x,str) or not 1<=len(x)<=200 for x in focus):invalid('focus')
        if not isinstance(refs,list) or len(refs)>20 or any(not isinstance(x,str) or x not in allowed for x in refs) or raw['knowledge_refs']!=[]:invalid('evidence_refs')
        import re
        # 解释不重新输出数字，精确数量在本地事实卡中呈现，避免模型重算。
        if any(re.search(r'\d',x) for x in [summary,*focus]):invalid('summary','AI解释含额外数字，请使用本地事实卡核对')
        return {'summary':summary,'focus':focus,'evidence_refs':refs,'knowledge_refs':[],'data_version':inputs['data_version']}

def register_reviews(app):
    service=TrainingReviews(app);app.extensions['training_reviews']=service
    @app.get('/api/v1/training/reviews/week')
    def review_week():
        if set(request.args)-{'week_start'}:invalid('query')
        return jsonify(data=service.facts(current_user.user_id,current_user.id,request.args.get('week_start')))
    @app.post('/api/v1/training/review-jobs')
    def review_job_create():return jsonify(data=app.extensions['training_jobs'].create(current_user.user_id,current_user.id,request.headers.get('Idempotency-Key'),body({'week_start','data_version','consent','history_consent'}),'review')),202
    @app.get('/api/v1/training/review-jobs/<jid>')
    def review_job_get(jid):return jsonify(data=app.extensions['training_jobs'].get(current_user.user_id,current_user.id,jid,'review'))
    @app.post('/api/v1/training/review-jobs/<jid>/cancel')
    def review_job_cancel(jid):body(set());return jsonify(data=app.extensions['training_jobs'].cancel(current_user.user_id,current_user.id,jid,'review'))
