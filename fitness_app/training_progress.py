"""今日/周安排及确定性进度；只读不创建执行、不调用模型。"""
import datetime as dt,json,hashlib
from flask import request,jsonify
from flask_login import current_user
from sqlalchemy import select,update
from . import db_schema as s
from .auth import APIError,body
from .database import ConflictError,idempotent
from .records import integer,uuid_id
from .training_plans import date,invalid,ZONE

class TrainingProgress:
    def __init__(self,app):self.app=app;self.plans=app.extensions['training_plans'];self.db=self.plans.db
    def monday(self,value=None):
        day=date(value,'week_start') if value else self.plans.today()-dt.timedelta(days=self.plans.today().weekday())
        if day.weekday()!=0:invalid('week_start','周起始须为周一')
        return day
    def _read(self,uid,auth,pid=None):
        with self.db.transaction(False) as c:
            self.plans.actor(c,uid,auth)
            if not pid:
                current=c.execute(select(s.training_plans.c.id).where(s.training_plans.c.user_id==uid,s.training_plans.c.status.in_(['active','paused']))).first()
                if not current:return None,[],[],[]
                pid=current[0]
            plan=self.plans.snapshot(c,uid,auth,pid)
            workouts=[dict(x) for x in c.execute(select(s.training_workouts).where(s.training_workouts.c.user_id==uid,s.training_workouts.c.plan_session_id.in_([x['id'] for x in plan['sessions']]))).mappings()] if plan['sessions'] else []
            for workout in workouts:
                snapshot=self.app.extensions['training_workouts'].snapshot(c,workout)
                workout['_completion']=snapshot['completion']
                workout['_needs_reconciliation']=any(x['status']=='needs_reconciliation' for item in snapshot['items'] for x in item['sets'])
            events=[dict(x) for x in c.execute(select(s.training_schedule_events).where(s.training_schedule_events.c.plan_id==pid,s.training_schedule_events.c.user_id==uid).order_by(s.training_schedule_events.c.plan_version,s.training_schedule_events.c.id)).mappings()]
        return plan,plan['sessions'],workouts,events
    def excluded_pause(self,session,workout,events,now):
        if workout and workout['status'] not in {'ready'}:return False
        pauses=[];start=None
        for event in events:
            day=dt.datetime.fromtimestamp(event['created_at_ms']/1000,ZONE).date()
            if event['event_type']=='pause':start=day
            elif event['event_type'] in {'resume','archive'} and start is not None:
                pauses.append((start,day));start=None
        if start is not None:pauses.append((start,dt.date.max))
        day=date(session['scheduled_date'])
        return any(a<=day<b for a,b in pauses)
    def progress(self,uid,auth,week_start=None,pid=None,_data=None):
        start=self.monday(week_start);end=start+dt.timedelta(days=6);plan,sessions,workouts,events=_data if _data is not None else self._read(uid,auth,pid)
        result={'week_start':start.isoformat(),'week_end':end.isoformat(),'timezone':'Asia/Shanghai','plan_id':plan['id'] if plan else None,'schedule_version':plan['state_version'] if plan else None,
                'scheduled_sessions':0,'completed_sessions':0,'partial_sessions':0,'skipped_sessions':0,'missed_sessions':0,'in_progress_sessions':0,'remaining_sessions':0,'ratio':None,'state':'no_plan','changes':[],'excluded_sessions':[],'as_of_ms':self.plans.clock()}
        if not plan:return result|{'data_version':'no_plan'}
        now=self.plans.today();by={x['plan_session_id']:x for x in workouts}
        for session in sessions:
            if not start<=date(session['scheduled_date'])<=end:continue
            workout=by.get(session['id'])
            if session['status']=='cancelled':result['excluded_sessions'].append({'id':session['id'],'reason':'superseded_or_cancelled'});continue
            if self.excluded_pause(session,workout,events,now):result['excluded_sessions'].append({'id':session['id'],'reason':'excluded_pause'});continue
            result['scheduled_sessions']+=1
            status=workout['status'] if workout else None
            if status=='completed' and workout['_needs_reconciliation']:status='ended_early'
            key='completed_sessions' if status=='completed' else 'partial_sessions' if status in {'ended_early','voided'} else 'in_progress_sessions' if status in {'running','paused'} else 'skipped_sessions' if session['status']=='skipped' else 'missed_sessions' if date(session['scheduled_date'])<now else 'remaining_sessions'
            result[key]+=1
        result['ratio']=result['completed_sessions']/result['scheduled_sessions'] if result['scheduled_sessions'] else None
        result['state']='out_of_range' if end<date(plan['start_date']) or start>date(plan['end_date']) else 'active' if result['scheduled_sessions'] else 'rest_only'
        for event in events:
            affected=[event.get('from_date'),event.get('to_date')]
            if any(v and start<=date(v)<=end for v in affected) or event['event_type'] in {'activate','pause','resume','archive'}:
                result['changes'].append({k:event[k] for k in ('id','session_id','event_type','from_date','to_date','reason','plan_version','created_at_ms')})
        version={'plan':(plan['id'],plan['state_version']),'sessions':[(x['id'],x['schedule_version'],x['status']) for x in sessions],'workouts':[(x['id'],x['state_version']) for x in workouts],'day':now.isoformat()}
        result['data_version']=hashlib.sha256(json.dumps(version,sort_keys=True).encode()).hexdigest()
        return result
    def week(self,uid,auth,pid,week_start=None):
        start=self.monday(week_start);plan,sessions,workouts,events=self._read(uid,auth,pid);by={x['plan_session_id']:x for x in workouts};days=[]
        for i in range(7):
            day=start+dt.timedelta(days=i);session=next((x for x in sessions if x['scheduled_date']==day.isoformat() and x['status']!='cancelled'),None);workout=by.get(session['id']) if session else None
            if workout and workout['status']=='completed' and workout['_needs_reconciliation']:workout={**workout,'status':'ended_early'}
            state='out_of_range' if not date(plan['start_date'])<=day<=date(plan['end_date']) else 'not_scheduled' if plan['status']=='draft' or plan['activated_at_ms'] is not None and day<dt.datetime.fromtimestamp(plan['activated_at_ms']/1000,ZONE).date() and workout is None else 'rest' if session is None else 'excluded_pause' if self.excluded_pause(session,workout,events,self.plans.today()) else 'completed' if workout and workout['status']=='completed' else 'partial' if workout and workout['status'] in {'ended_early','voided'} else 'in_progress' if workout and workout['status'] in {'running','paused'} else 'skipped' if session['status']=='skipped' else 'missed' if day<self.plans.today() else 'planned'
            can=bool(session and state=='planned' and plan['status']=='active' and day==self.plans.today())
            days.append({'date':day.isoformat(),'weekday':i+1,'day_kind':'training' if session else 'rest','state':state,'session_id':session['id'] if session else None,'workout_id':workout['id'] if workout else None,'workout_status':workout['status'] if workout else None,'scheduled_minutes':session['estimated_minutes'] if session else 0,'can_start':can,'reason':None if can else state,'session':session,'completion':workout['_completion'] if workout else None})
        progress=self.progress(uid,auth,start.isoformat(),pid,_data=(plan,sessions,workouts,events))
        return {'plan_id':pid,'week_start':start.isoformat(),'days':days,'effective_sessions':progress['scheduled_sessions'],'progress':progress,'schedule_changes':progress['changes'],'version':plan['state_version'],'data_version':progress['data_version']}
    def today(self,uid,auth,value=None):
        day=date(value) if value else self.plans.today();current=self.plans.current(uid,auth)['plan'];week=(day-dt.timedelta(days=day.weekday())).isoformat()
        if not current:return {'date':day.isoformat(),'timezone':'Asia/Shanghai','plan_status':'no_plan','session':None,'workout':None,'week_progress':self.progress(uid,auth,week),'next_action':'create_plan'}
        data=self.week(uid,auth,current['id'],week);selected=next(x for x in data['days'] if x['date']==day.isoformat());workout=None
        if selected['workout_id'] and 'training_workouts' in self.app.extensions:workout=self.app.extensions['training_workouts'].get(uid,auth,selected['workout_id'])
        return {'date':day.isoformat(),'timezone':'Asia/Shanghai','plan_status':current['status'],'session':selected['session'],'workout':workout,'day':selected,'week_progress':data['progress'],'next_action':'continue' if workout and workout['status'] in {'running','paused'} or workout and workout['status']=='ready' and selected['can_start'] else 'start' if selected['can_start'] else selected['state']}
    def schedule(self,uid,auth,pid,sid,kind,data,key):
        key=uuid_id(key);version=integer(data.get('expected_version'),'expected_version')
        with self.db.transaction() as c:
            plan,session=self.plans.session_row(c,uid,auth,pid,sid)
            def write():
                if plan['status'] not in {'active','paused'}:raise APIError('INVALID_STATE','当前计划不可调整安排',409)
                if session['schedule_version']!=version:raise ConflictError('日期安排已更新')
                workout=c.execute(select(s.training_workouts).where(s.training_workouts.c.plan_session_id==sid)).mappings().first()
                if workout and workout['status']!='ready':raise APIError('WORKOUT_ALREADY_STARTED','已开始的执行不能改期或跳过',409)
                values={'schedule_version':version+1};target=session['scheduled_date'];reason=None
                if kind=='reschedule':
                    if integer(data.get('plan_expected_version'),'plan_expected_version')!=plan['state_version']:raise ConflictError('计划已更新')
                    target=date(data.get('target_date'),'target_date').isoformat()
                    if date(target)<self.plans.today() or not date(plan['start_date'])<=date(target)<=date(plan['start_date'])+dt.timedelta(weeks=plan['weeks'],days=-1):invalid('target_date')
                    if session['status']!='planned':raise APIError('INVALID_STATE','跳过的安排请先恢复',409)
                    conflict=c.execute(select(s.training_plan_sessions.c.id).where(s.training_plan_sessions.c.plan_id==pid,s.training_plan_sessions.c.scheduled_date==target,s.training_plan_sessions.c.id!=sid,s.training_plan_sessions.c.status!='cancelled')).first()
                    if conflict:raise APIError('SCHEDULE_CONFLICT','目标日已有训练安排，请选择其他日期',409)
                    values.update(scheduled_date=target,rescheduled_from=session['scheduled_date'])
                elif kind=='skip':
                    if session['status']!='planned':raise APIError('INVALID_STATE','当前安排不能跳过',409)
                    reason=data.get('reason')
                    if reason is not None:
                        from .training_plans import text
                        reason=text(reason,'reason',200,0)
                    values['status']='skipped'
                elif kind=='restore':
                    if session['status']!='skipped' or workout and workout['status']!='ready':raise APIError('INVALID_STATE','仅可恢复未开始的跳过安排',409)
                    values['status']='planned'
                if workout and workout['status']=='ready' and kind=='reschedule':
                    target_snapshot=json.loads(workout['target_snapshot_json']);target_snapshot['date']=target
                    c.execute(update(s.training_workouts).where(s.training_workouts.c.id==workout['id']).values(target_snapshot_json=json.dumps(target_snapshot,ensure_ascii=False),state_version=workout['state_version']+1))
                c.execute(update(s.training_plan_sessions).where(s.training_plan_sessions.c.id==sid).values(**values))
                c.execute(update(s.training_plans).where(s.training_plans.c.id==pid).values(state_version=plan['state_version']+1,updated_at_ms=self.plans.clock()))
                self.plans._event(c,uid,pid,key,kind,plan['state_version']+1,sid,session['scheduled_date'],target,reason)
                return {'id':sid,'version':version+1}
            idempotent(c,uid,key,data,'training.schedule.'+kind,write,sid)
        result=self.plans.get(uid,auth,pid);session=next(x for x in result['sessions'] if x['id']==sid)
        weeks=sorted({(date(x)-dt.timedelta(days=date(x).weekday())).isoformat() for x in (session['scheduled_date'],session.get('rescheduled_from') or session['scheduled_date'])})
        return {'session':session,'affected_weeks':weeks} if kind=='reschedule' else session

def register_progress(app):
    service=TrainingProgress(app);app.extensions['training_progress']=service
    @app.get('/api/v1/training/today')
    def training_today():
        if set(request.args)-{'date'}:invalid('query')
        return jsonify(data=service.today(current_user.user_id,current_user.id,request.args.get('date')))
    @app.get('/api/v1/training/progress')
    def training_progress():
        if set(request.args)-{'week_start','plan_id'}:invalid('query')
        return jsonify(data=service.progress(current_user.user_id,current_user.id,request.args.get('week_start'),request.args.get('plan_id')))
    @app.get('/api/v1/training/plans/<pid>/week')
    def training_week(pid):
        if set(request.args)-{'week_start'}:invalid('query')
        return jsonify(data=service.week(current_user.user_id,current_user.id,pid,request.args.get('week_start')))
    for kind in ('reschedule','skip','restore'):
        def handler(pid,sid,command=kind):
            fields={'expected_version'}|({'target_date','plan_expected_version'} if command=='reschedule' else {'reason'} if command=='skip' else set())
            return jsonify(data=service.schedule(current_user.user_id,current_user.id,pid,sid,command,body(fields),request.headers.get('Idempotency-Key')))
        app.add_url_rule('/api/v1/training/plans/<pid>/sessions/<sid>/'+kind,'training_schedule_'+kind,handler,methods=['POST'])
