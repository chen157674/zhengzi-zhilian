"""本人已保存姿态统计：按动作、判项、侧别与规则版本隔离，不用观察条数代替周期。"""
import json
from datetime import datetime,timedelta,timezone
from flask import request,jsonify
from flask_login import current_user
from sqlalchemy import select
from . import db_schema as s
from .auth import APIError
from .catalogue import get_action
from .posture_rules import rules_for
from .records import TERMINAL,effective_records

ZONE=timezone(timedelta(hours=8))

def interval_totals(observations):
    """并集分段；同段优先已观察偏差，未知仅占没有可评估证据的部分。"""
    edges={}
    for item in observations:
        start,end=item['start_ms'],item['end_ms']
        if end<=start:continue
        state=item['status']
        edges.setdefault(start,[]).append((state,1))
        edges.setdefault(end,[]).append((state,-1))
    counts={'issue':0,'not_observed':0,'unassessable':0}
    total={'issue':0,'evaluated':0,'unknown':0}
    previous=None
    for stamp,changes in sorted(edges.items()):
        if previous is not None:
            span=stamp-previous
            if counts['issue']:total['issue']+=span;total['evaluated']+=span
            elif counts['not_observed']:total['evaluated']+=span
            elif counts['unassessable']:total['unknown']+=span
        for status,delta in changes:counts[status]+=delta
        previous=stamp
    return total

def aggregate_records(records,events,observations,code,side,version=None):
    cohorts={}
    for row in records:
        sid=row['id'];definition=json.loads(row['definition_json'])
        rule=next((r for r in definition.get('issues',[]) if r['code']==code),None)
        if not rule:continue
        rule_version=rule.get('rule_version') or 'legacy-unknown'
        cohort=cohorts.setdefault(rule_version,[])
        unit='ms' if row['action_id']=='forearm_plank' else 'cycles'
        summary={'session_id':sid,'started_at_ms':row['started_at_ms'],'status':row['status'],'issue':0,'evaluated':0,'unknown':0,'unit':unit}
        if unit=='cycles':
            seen=set()
            for event in events.get(sid,[]):
                if event['side']!=side:continue
                key=(event['event_seq'],event['side'])
                if key in seen:continue
                seen.add(key)
                item=next((x for x in json.loads(event['assessment_json']) if x['code']==code),None)
                state=item.get('status') if item and item.get('rule_version',rule_version)==rule_version else 'unassessable'
                if state in ('issue','not_observed'):summary['evaluated']+=1;summary['issue']+=int(state=='issue')
                else:summary['unknown']+=1
        else:
            items=[json.loads(r['data_json']) for r in observations.get(sid,[]) if r['code']==code and r['side']==side and r['rule_version']==rule_version]
            summary.update(interval_totals(items))
        summary['ratio']=summary['issue']/summary['evaluated'] if summary['evaluated'] else None
        summary['has_issue']=summary['issue']>0 or any(json.loads(x['data_json']).get('status')=='issue' for x in observations.get(sid,[]) if x['code']==code and x['side']==side and x['rule_version']==rule_version)
        summary['small_sample']=unit=='cycles' and summary['evaluated']<10
        cohort.append(summary)
    versions=sorted(cohorts,key=lambda v:max(x['started_at_ms'] for x in cohorts[v]),reverse=True)
    selected=version or (versions[0] if versions else None)
    if version and version not in cohorts:raise APIError('VALIDATION_FAILED','此筛选范围没有该规则版本',422)
    rows=sorted(cohorts.get(selected,[]),key=lambda x:x['started_at_ms'])
    totals={name:sum(x[name] for x in rows) for name in ('issue','evaluated','unknown')}
    return {**totals,'ratio':totals['issue']/totals['evaluated'] if totals['evaluated'] else None,
            'rule_version':selected,'rule_versions':versions,'trend':rows,'records':list(reversed(rows)),
            'excluded_version_records':sum(len(cohorts[v]) for v in versions if v!=selected),
            'record_count':len(rows),'issue_record_count':sum(x['has_issue'] for x in rows),
            'unmeasured_records':sum(x['evaluated']+x['unknown']==0 for x in rows)}

def register_training_overview(app,db):
    @app.get('/api/v1/account/training-overview')
    def training_overview():
        allowed={'action_id','code','side','date_from','date_to','rule_version'}
        if set(request.args)-allowed:raise APIError('INVALID_REQUEST','概览筛选字段不合法')
        action=get_action(request.args.get('action_id','bodyweight_squat'))
        if not action:raise APIError('VALIDATION_FAILED','动作不存在',422)
        rules=rules_for(action['action_id'])
        code=request.args.get('code') or rules[0]['code']
        if code not in {x['code'] for x in rules}:raise APIError('VALIDATION_FAILED','检查项不属于当前动作',422)
        sided=action['unit']=='per_side_reps' or action['action_id']=='high_knees'
        side=request.args.get('side','left' if sided else 'none')
        if side not in ({'left','right'} if sided else {'none','left','right'}):raise APIError('VALIDATION_FAILED','侧别无效',422)
        today=datetime.fromtimestamp((app.config.get('CLOCK') or s.now_ms)()/1000,ZONE).date()
        try:
            start=datetime.strptime(request.args.get('date_from',str(today-timedelta(days=6))),'%Y-%m-%d').replace(tzinfo=ZONE)
            end=datetime.strptime(request.args.get('date_to',str(today)),'%Y-%m-%d').replace(tzinfo=ZONE)+timedelta(days=1)
        except (ValueError,OverflowError) as error:raise APIError('VALIDATION_FAILED','日期格式无效',422) from error
        if not 0<(end-start).days<=366:raise APIError('VALIDATION_FAILED','请选择一年以内的有效日期范围',422)
        with db.transaction(False) as c:
            rows=c.execute(select(s.analysis_sessions,s.action_capabilities.c.definition_json).join(s.action_capabilities,
                (s.analysis_sessions.c.action_id==s.action_capabilities.c.action_id)&(s.analysis_sessions.c.capability_version==s.action_capabilities.c.capability_version))
                .where(s.analysis_sessions.c.user_id==current_user.user_id,s.analysis_sessions.c.action_id==action['action_id'],
                       s.analysis_sessions.c.source!='demo',s.analysis_sessions.c.status.in_(TERMINAL),effective_records(),
                       s.analysis_sessions.c.started_at_ms>=int(start.timestamp()*1000),s.analysis_sessions.c.started_at_ms<int(end.timestamp()*1000))).mappings().all()
            # 子查询避免记录较多时超过SQLite变量上限；所有明细仍受本人条件限制。
            ids=select(s.analysis_sessions.c.id).where(s.analysis_sessions.c.user_id==current_user.user_id,
                s.analysis_sessions.c.action_id==action['action_id'],s.analysis_sessions.c.source!='demo',
                s.analysis_sessions.c.status.in_(TERMINAL),effective_records(),s.analysis_sessions.c.started_at_ms>=int(start.timestamp()*1000),
                s.analysis_sessions.c.started_at_ms<int(end.timestamp()*1000))
            events={};observations={}
            for row in c.execute(select(s.action_events).where(s.action_events.c.session_id.in_(ids))).mappings():events.setdefault(row['session_id'],[]).append(row)
            for row in c.execute(select(s.posture_observations).where(s.posture_observations.c.session_id.in_(ids))).mappings():observations.setdefault(row['session_id'],[]).append(row)
        data=aggregate_records(rows,events,observations,code,side,request.args.get('rule_version'))
        data.update(action_id=action['action_id'],code=code,side=side,unit='ms' if action['unit']=='seconds' else 'cycles',
                    scope='本人已保存记录中的同动作、同判项、同侧别、同规则版本；排除演示。中断记录仅包含已保存部分。',
                    date_from=str(start.date()),date_to=str((end-timedelta(days=1)).date()),rules=rules)
        return jsonify(data=data)
