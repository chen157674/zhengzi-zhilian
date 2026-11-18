"""成长看板：实际分析与计划执行分开计数，饮食使用已保存记录。"""
import datetime as dt
from collections import Counter
from flask import jsonify,request
from flask_login import current_user
from sqlalchemy import select
from . import db_schema as s
from .auth import APIError
from .training_plans import ZONE

class GrowthDashboard:
    def __init__(self,app):self.app=app;self.db=app.extensions['database']
    def get(self,uid,auth,week_start,days):
        if days not in {'7','30'}:raise APIError('VALIDATION_FAILED','请选择本周或近30天',422)
        progress=self.app.extensions['training_progress']
        start_week=progress.monday(week_start);end=start_week+dt.timedelta(days=6)
        today=self.app.extensions['diet_journal'].today()
        if days=='30' and start_week<=today<=end:end=today
        start=start_week if days=='7' else end-dt.timedelta(days=29)
        daily={(start+dt.timedelta(days=i)).isoformat():{'date':(start+dt.timedelta(days=i)).isoformat(),'analysis_count':0,'workout_count':0,'meal_count':0} for i in range((end-start).days+1)}
        records=[];page=1
        store=self.app.extensions['session_store'];actions=Counter();record_links={}
        while True:
            data=store.list_records(uid,page=page,page_size=100,date_from=start.isoformat(),date_to=end.isoformat())
            for item in data['records']:
                row=store.snapshot(uid,item['session_id'],True)
                if row['status'] not in {'completed','ended_early'} or row.get('included_in_statistics') is False:continue
                if self.app.extensions['persistence'].snapshot(uid,row['session_id'])['save_state']!='clean':continue
                records.append(row)
                day=dt.datetime.fromtimestamp((row.get('started_at_ms') or row['created_at_ms'])/1000,ZONE).date().isoformat()
                if day in daily:daily[day]['analysis_count']+=1
                actions[row['action_name']]+=1;record_links.setdefault(row['action_name'],row['action_id'])
            if page*100>=data['total']:break
            page+=1
        left=int(dt.datetime.combine(start,dt.time(),ZONE).timestamp()*1000)
        right=int(dt.datetime.combine(end+dt.timedelta(days=1),dt.time(),ZONE).timestamp()*1000)
        with self.db.transaction(False) as c:
            store._actor(c,uid,auth)
            workouts=c.execute(select(s.training_workouts.c.actual_started_at_ms,s.training_workouts.c.status).where(s.training_workouts.c.user_id==uid,s.training_workouts.c.actual_started_at_ms>=left,s.training_workouts.c.actual_started_at_ms<right,s.training_workouts.c.status!='voided')).all()
            task_ids=c.execute(select(s.coach_saved_retests.c.id).where(s.coach_saved_retests.c.user_id==uid,s.coach_saved_retests.c.created_at_ms>=left,s.coach_saved_retests.c.created_at_ms<right)).scalars().all()
        for stamp,state in workouts:
            day=dt.datetime.fromtimestamp(stamp/1000,ZONE).date().isoformat()
            if day in daily:daily[day]['workout_count']+=1
        journal=self.app.extensions['diet_journal'].list(uid,auth,start.isoformat(),end.isoformat())['records']
        groups={key:set() for key in ('staple','protein','vegetable','fruit','drink')}
        for meal in journal:
            daily[meal['consumed_date']]['meal_count']+=1
            for food in meal['foods']:
                if food['group'] in groups:groups[food['group']].add(meal['consumed_date'])
        current=self.app.extensions['training_plans'].current(uid,auth)['plan']
        calendar=progress.week(uid,auth,current['id'],start_week.isoformat()) if current else None
        from .training_insights import observation_groups
        retests={'completed':0,'awaiting':0,'not_comparable':0}
        for identifier in task_ids:
            try:task=self.app.extensions['coach_retests'].get(uid,auth,identifier)
            except APIError:continue
            state=(task.get('comparison') or {}).get('state')
            retests['completed' if state in {'reobserved','not_reobserved'} else 'not_comparable' if state else 'awaiting']+=1
        # 只读入口不创建计划、执行或模型任务。
        return {'date_from':start.isoformat(),'date_to':end.isoformat(),'period_days':int(days),'timezone':'Asia/Shanghai',
                'daily':list(daily.values()),'analysis_count':len(records),'execution_count':len(workouts),
                'training_days':sum(x['analysis_count']>0 or x['workout_count']>0 for x in daily.values()),
                'diet_days':len({x['consumed_date'] for x in journal}),'diet_records':len(journal),
                'food_days':{k:len(v) for k,v in groups.items()},'recent_meals':journal[:4],
                'actions':[{'name':name,'count':count,'action_id':record_links[name]} for name,count in actions.most_common(8)],
                'trend_groups':observation_groups(records),'retests':retests,
                'calendar':calendar,'plan_title':current['title'] if current else None,
                'memories':self.app.extensions['diet_journal'].memories(uid,auth)['memories'][:8]}

def register_growth_dashboard(app):
    service=GrowthDashboard(app);app.extensions['growth_dashboard']=service
    @app.get('/api/v1/training/growth')
    def growth_dashboard():
        if set(request.args)-{'week_start','days'}:raise APIError('INVALID_REQUEST','看板查询字段不正确',400)
        return jsonify(data=service.get(current_user.user_id,current_user.id,request.args.get('week_start'),request.args.get('days','7')))
