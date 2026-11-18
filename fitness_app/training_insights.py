"""本人有效训练证据与可比统计；记录比例、完整周期和时间窗口分开。"""
import hashlib,json,math
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from flask import jsonify,request
from flask_login import current_user
from sqlalchemy import select,func,case,union_all,true,literal
from . import db_schema as s
from .auth import APIError,body
from .records import TERMINAL,effective_records,uuid_id,integer
from .database import ConflictError,json_text
from .training_overview import interval_totals

ZONE=timezone(timedelta(hours=8))
OBSERVATION_FIELDS=frozenset({'value','threshold','feature_name','view','view_basis','parameter_source','measurement_side',
    'offset_direction','time_base','exercise_basis','hip_image_degrees','knee_image_degrees','trunk_change_degrees',
    'range_by_side','raw_range_by_side','extreme_windows','minimum_window_ms','measurement_basis',
    'minimum_hip_degrees','minimum_knee_degrees','longest_joint_extension_ms','minimum_stage_ms','limited_joints',
    'scope','affected_sides','arm_deviation','observed_duration_ms','value_min','value_max'})


def ai_observation_evidence(raw):
    """保留已保存的部位方向和观测范围，嵌套字段不能夹带任意额外内容。"""
    result={k:v for k,v in raw.items() if k in OBSERVATION_FIELDS}
    if 'affected_sides' in result:
        sides=result['affected_sides'];result['affected_sides']=[s for s in ('left','right') if isinstance(sides,list) and s in sides]
    if 'arm_deviation' in result:
        arms=result.pop('arm_deviation');clean={}
        if isinstance(arms,dict):
            for side in result.get('affected_sides',[]):
                value=arms.get(side);ratio=value.get('ratio') if isinstance(value,dict) else None
                if type(ratio) in (int,float) and math.isfinite(ratio) and 0<abs(ratio)<=1:
                    direction='outward' if ratio>0 else 'inward'
                    if value.get('direction')==direction:clean[side]={'ratio':ratio,'direction':direction}
        result['arm_deviation']=clean
    if 'scope' in result and (not isinstance(result['scope'],str) or len(result['scope'])>500):result.pop('scope')
    if 'observed_duration_ms' in result and (type(result['observed_duration_ms']) is not int or result['observed_duration_ms']<0):result.pop('observed_duration_ms')
    for key in ('value_min','value_max'):
        if key in result and (type(result[key]) not in (int,float) or not math.isfinite(result[key])):result.pop(key)
    return result


def filters(uid,data=None,as_of_ms=None):
    data=data or {}
    if not isinstance(data,dict) or set(data)-{'action_id','date_from','date_to'}:raise APIError('INVALID_REQUEST','训练范围字段无效',400)
    conditions=[s.analysis_sessions.c.user_id==uid,s.analysis_sessions.c.source!='demo',effective_records(),
                s.analysis_sessions.c.status.in_(TERMINAL),s.analysis_sessions.c.started_at_ms.is_not(None)]
    action=data.get('action_id')
    if action:
        from .catalogue import get_action
        if not isinstance(action,str) or not get_action(action):raise APIError('VALIDATION_FAILED','动作不存在',422)
        conditions.append(s.analysis_sessions.c.action_id==action)
    dates={}
    for field in ('date_from','date_to'):
        if data.get(field) is not None:
            try:dates[field]=datetime.strptime(data[field],'%Y-%m-%d').replace(tzinfo=ZONE)
            except (TypeError,ValueError):raise APIError('VALIDATION_FAILED','日期格式应为YYYY-MM-DD',422) from None
    if len(dates)==2 and dates['date_from']>dates['date_to']:raise APIError('VALIDATION_FAILED','请按先后顺序选择起止日期',422)
    if 'date_from' in dates:conditions.append(s.analysis_sessions.c.started_at_ms>=int(dates['date_from'].timestamp()*1000))
    if 'date_to' in dates:conditions.append(s.analysis_sessions.c.started_at_ms<int((dates['date_to']+timedelta(days=1)).timestamp()*1000))
    if as_of_ms is not None:
        conditions.extend([s.analysis_sessions.c.created_at_ms<=as_of_ms,s.analysis_sessions.c.ended_at_ms<=as_of_ms])
    return conditions


def _view(item):return item.get('evidence',{}).get('view') or item.get('view') or 'unknown'


def history_header_query(conditions):
    return select(s.analysis_sessions.c.id,s.analysis_sessions.c.action_id,s.analysis_sessions.c.state_version,
        s.analysis_sessions.c.started_at_ms,s.analysis_sessions.c.ended_at_ms,s.training_notes.c.version.label('note_version'),s.training_notes.c.data_json.label('note_json')).outerjoin(
        s.training_notes,s.training_notes.c.session_id==s.analysis_sessions.c.id).where(*conditions).order_by(s.analysis_sessions.c.ended_at_ms.desc(),s.analysis_sessions.c.id)


def record_statistics(data):
    cycles=defaultdict(dict);windows=defaultdict(list)
    for event in data.get('events',[]):
        for item in event.get('assessments',[]):
            key=(item['code'],event['side'],item.get('rule_version','legacy-unknown'),_view(item))
            cycles[key][(event['event_seq'],event['side'])]=item.get('status','unassessable')
    for item in data.get('posture_observations',[]):
        key=(item['code'],item.get('side','none'),item.get('rule_version','legacy-unknown'),_view(item))
        if item.get('end_ms',0)>item.get('start_ms',0):windows[key].append(item)
    for item in data.get('intervals',[]):
        if item.get('item_code'):
            key=(item['item_code'],'none','legacy-interval:'+item['algorithm_version'],'unknown')
            windows[key].append({'start_ms':item['start_ms'],'end_ms':item['end_ms'],'status':'issue' if item['kind']=='issue' else 'unassessable'})
    result=[]
    for key,values in cycles.items():
        states=list(values.values());evaluated=sum(v in {'issue','not_observed'} for v in states);issue=states.count('issue')
        result.append(dict(zip(('code','side','rule_version','view'),key))|{'unit':'cycles','issue':issue,'evaluated':evaluated,
            'unknown':len(states)-evaluated,'ratio':issue/evaluated if evaluated else None,'small_sample':evaluated<10})
    for key,items in windows.items():
        totals=interval_totals(items);n=totals['evaluated']
        result.append(dict(zip(('code','side','rule_version','view'),key))|{'unit':'ms',**totals,'ratio':totals['issue']/n if n else None,
            'small_sample':n<3000})
    return result


def record_evidence(data):
    rules={r['code']:r for r in data['supported_items']};selected={}
    for item in data.get('posture',{}).get('findings',[]):
        if item['code'] not in rules:continue
        key=(item['code'],item.get('side','none'),item.get('rule_version','legacy-unknown'),_view(item),item['status'])
        selected[key]=item
    values=[]
    for key,item in selected.items():
        rule=rules[item['code']]
        value={k:item.get(k) for k in ('code','status','side','rule_version','start_ms','end_ms','message','suggestion','body_parts','reason','kind','evidence_level')}
        value['evidence']=ai_observation_evidence(item.get('evidence',{}))
        value.update(record_id=data['session_id'],action_id=data['action_id'],title=rule.get('title',item['code']),
                     parameter_source=rule.get('parameter_source'),validation_status=rule.get('validation_status','not_independently_validated'))
        value['id']='e:'+hashlib.sha256(json_text(value).encode()).hexdigest()[:24];values.append(value)
    for item in data.get('confirmed_demo_findings',[]):
        value={**item,'record_id':data['session_id'],'action_id':data['action_id'],
            'title':item['message'],'kind':'user_confirmed_demo_annotation',
            'validation_status':'user_confirmed_not_model_detection'}
        value['id']='e:'+hashlib.sha256(json_text(value).encode()).hexdigest()[:24];values.append(value)
    represented={v['code'] for v in values}
    for code,rule in rules.items():
        if code not in represented:
            value={'record_id':data['session_id'],'action_id':data['action_id'],'code':code,'status':'unassessable',
                   'title':rule.get('title',code),'body_parts':rule.get('body_parts',[]),'rule_version':rule.get('rule_version','legacy-unknown'),
                   'message':'该记录没有保存足以评估此项的结果','evidence_level':'reference','kind':'coverage_summary',
                   'validation_status':'not_independently_validated','evidence':{},'suggestion':None}
            value['id']='e:'+hashlib.sha256(json_text(value).encode()).hexdigest()[:24];values.append(value)
    return values


def compact_record(data,note=None):
    config=data.get('config',{});start=config.get('source_start_ms');end=config.get('source_end_ms')
    source_range={'start_ms':start,'end_ms':end,'time_base':'source_ms','interval':'half_open'} if type(start) is int and type(end) is int and 0<=start<end else None
    exercise_basis=config.get('exercise_basis')
    if exercise_basis not in {'user_selected','automatically_recognized'}:exercise_basis='not_recorded'
    return {k:data.get(k) for k in ('session_id','action_id','action_name','status','source','state_version','created_at_ms',
        'started_at_ms','ended_at_ms','active_ms','capability_version','posture_policy_version','included_in_statistics','superseded_by','mode')}|{
        'target':config.get('target'),
        'completion':{'counting_available':data['counting_available'],'unit':data['unit'],
                      'completed_sets':data['completed_sets'],'evaluable_cycles':data['evaluable_count'],'unassessable_cycles':data['unassessable_count'],
                      'reference_count':data['total_count'] if data['counting_available'] and data['unit']!='seconds' else None,
                      'reference_hold_ms':data['hold_ms'] if data['counting_available'] and data['unit']=='seconds' else None,
                      'count_accuracy_status':data['count_accuracy_status']},
        'statistics':record_statistics(data),'limitations':data.get('posture',{}).get('limitations',[]),
        'assessment_state':data.get('posture',{}).get('assessment_state','evidence_insufficient'),
        'training_note':note,'source_range':source_range,'exercise_basis':exercise_basis,'no_issue_is_not_overall_correct':True}


def observation_groups(records,statistics=None):
    """按同动作/规则/侧别/机位/单位分组，保留未知分母与实际记录身份。"""
    groups={}
    for record in records:
        for stat in (statistics or record_statistics)(record):
            rule=next((item for item in record.get('supported_items',[]) if item['code']==stat['code']),{})
            policy=json.dumps(record.get('input_policy'),sort_keys=True,ensure_ascii=False,separators=(',',':'))
            key=(record['action_id'],stat['code'],stat['side'],stat['rule_version'],stat['view'],stat['unit'],record.get('capability_version'),policy)
            group=groups.setdefault(key,{'action_id':record['action_id'],'action_name':record['action_name'],'code':stat['code'],
                'title':rule.get('title',stat['code']),**{field:stat[field] for field in ('side','rule_version','view','unit')},
                'records':[],'totals':{'issue':0,'evaluated':0,'unknown':0},
                'capability_version':record.get('capability_version'),
                'comparable_view':stat['view'] not in {'unknown','auto'}})
            group['records'].append({'record_id':record['session_id'],'record_version':record['state_version'],
                'started_at_ms':record.get('started_at_ms') or record['created_at_ms'],
                **{field:stat[field] for field in ('issue','evaluated','unknown')}})
            for field in ('issue','evaluated','unknown'):group['totals'][field]+=stat[field]
    for group in groups.values():
        total=group['totals'];total['ratio']=total['issue']/total['evaluated'] if total['evaluated'] else None
    return list(groups.values())


class TrainingInsights:
    def __init__(self,sessions,training_info):self.sessions=sessions;self.db=sessions.db;self.training_info=training_info;self.clock=training_info.clock

    def history(self,uid,auth,scope=None,as_of_ms=None,detail_limit=200):
        integer(detail_limit,'detail_limit',0,200);conditions=filters(uid,scope,as_of_ms)
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth)
            selected=select(s.analysis_sessions.c.id,s.analysis_sessions.c.action_id,s.analysis_sessions.c.state_version,
                s.analysis_sessions.c.started_at_ms,s.analysis_sessions.c.ended_at_ms,s.analysis_sessions.c.active_ms,
                s.action_capabilities.c.definition_json).join(s.action_capabilities,
                (s.analysis_sessions.c.action_id==s.action_capabilities.c.action_id)&(s.analysis_sessions.c.capability_version==s.action_capabilities.c.capability_version)).where(*conditions).subquery('history')
            issues=func.json_each(selected.c.definition_json,'$.issues').table_valued('key','value').alias('rule')
            base=select(selected.c.id.label('sid'),selected.c.action_id,func.json_extract(issues.c.value,'$.code').label('code'),
                func.coalesce(func.json_extract(issues.c.value,'$.rule_version'),'legacy-unknown').label('rule_version'),literal('unassessable').label('status')).select_from(selected.join(issues,true()))
            pose=select(selected.c.id.label('sid'),selected.c.action_id,s.posture_observations.c.code,s.posture_observations.c.rule_version,
                        func.json_extract(s.posture_observations.c.data_json,'$.status').label('status')).select_from(selected.join(s.posture_observations,s.posture_observations.c.session_id==selected.c.id))
            assessments=func.json_each(s.action_events.c.assessment_json).table_valued('key','value').alias('assessment')
            event=select(selected.c.id.label('sid'),selected.c.action_id,func.json_extract(assessments.c.value,'$.code').label('code'),
                func.coalesce(func.json_extract(assessments.c.value,'$.rule_version'),'legacy-unknown').label('rule_version'),
                func.json_extract(assessments.c.value,'$.status').label('status')).select_from(selected.join(s.action_events,s.action_events.c.session_id==selected.c.id).join(assessments,true()))
            interval=select(selected.c.id.label('sid'),selected.c.action_id,s.observation_intervals.c.item_code.label('code'),
                (literal('legacy-interval:')+s.observation_intervals.c.algorithm_version).label('rule_version'),
                case((s.observation_intervals.c.kind=='issue','issue'),else_='unassessable').label('status')).select_from(
                    selected.join(s.observation_intervals,s.observation_intervals.c.session_id==selected.c.id)).where(s.observation_intervals.c.item_code.is_not(None))
            all_items=union_all(base,pose,event,interval).subquery('items')
            grouped=select(all_items.c.sid,all_items.c.action_id,all_items.c.code,all_items.c.rule_version,
                func.max(case((all_items.c.status=='issue',1),else_=0)).label('has_issue'),
                func.max(case((all_items.c.status.in_(('issue','not_observed')),1),else_=0)).label('assessable')).where(all_items.c.code.is_not(None)).group_by(
                    all_items.c.sid,all_items.c.action_id,all_items.c.code,all_items.c.rule_version)
            historical_rules={}
            for raw_rule in c.execute(select(issues.c.value).select_from(selected.join(issues,true()))).scalars():
                rule=json.loads(raw_rule)
                historical_rules[(rule.get('code'),rule.get('rule_version','legacy-unknown'))]=rule
            cohorts={}
            for row in c.execute(grouped).mappings():
                key=(row['action_id'],row['code'],row['rule_version'])
                target=cohorts.setdefault(key,{'action_id':key[0],'code':key[1],'rule_version':key[2],
                    'records_with_issue':0,'assessed_records':0,'unassessable_records':0})
                target['records_with_issue']+=row['has_issue'];target['assessed_records']+=row['assessable'];target['unassessable_records']+=not row['assessable']
            actions=c.execute(select(selected.c.action_id,func.count().label('records'),func.sum(selected.c.active_ms).label('active_ms')).group_by(selected.c.action_id)).mappings().all()
            actions=[dict(r) for r in actions]
            amounts=select(s.session_sets.c.session_id,func.sum(s.session_sets.c.total_count).label('reps')).group_by(s.session_sets.c.session_id).subquery('amounts')
            available=func.coalesce(func.json_extract(selected.c.definition_json,'$.counting_available'),0)==1
            unit=func.json_extract(selected.c.definition_json,'$.unit')
            completion=c.execute(select(selected.c.action_id,
                func.sum(case((available&unit.in_(('reps','per_side_reps')),1),else_=0)).label('rep_records'),
                func.sum(case((available&unit.in_(('reps','per_side_reps')),func.coalesce(amounts.c.reps,0)),else_=0)).label('reference_reps'),
                func.sum(case((~available,1),else_=0)).label('count_unavailable_records'))
                .select_from(selected.outerjoin(amounts,amounts.c.session_id==selected.c.id)).group_by(selected.c.action_id)).mappings()
            completions={row['action_id']:dict(row) for row in completion}
            for row in actions:
                extra=completions[row['action_id']];row.update(extra)
                if not extra['rep_records']:row['reference_reps']=None
                row['amounts_are_reference']=True
            header_rows=c.execute(history_header_query(conditions)).mappings()
            fingerprint=hashlib.sha256();headers=[];total=0
            for raw in header_rows:
                row=dict(raw);fingerprint.update(json_text({k:v for k,v in row.items() if k!='note_json'}).encode());total+=1
                if len(headers)<detail_limit:
                    raw_note=row.pop('note_json');row['user_provided_note']=json.loads(raw_note) if raw_note else None;headers.append(row)
            ranked=[]
            for item in cohorts.values():
                rule=historical_rules.get((item['code'],item['rule_version']),{})
                item.update(title=rule.get('title',item['code']),body_parts=rule.get('body_parts',[]),suggestion=rule.get('suggestion'),
                    evidence_level=rule.get('evidence_level','legacy'),parameter_source=rule.get('parameter_source'),
                    rule_description_available=bool(rule))
                item['ratio']=item['records_with_issue']/item['assessed_records'] if item['assessed_records'] else None
                item['ratio_unit']='records_with_observed_issue_over_assessed_records';item['small_sample']=item['assessed_records']<10
                ranked.append(item)
            ranked.sort(key=lambda x:(-x['records_with_issue'],-x['assessed_records'],x['action_id'],x['code']))
            return {'scope':scope or {},'as_of_ms':as_of_ms,'records_total':total,'action_totals':actions,
                    'rule_cohorts':ranked,'recent_details':headers,'details_returned':len(headers),'details_limited':len(headers)<total,
                    'fingerprint':fingerprint.hexdigest(),'excluded_demo_and_superseded':True,'scope_includes_all_matched_aggregates':True,
                    'ratio_label':'出现该提示的可评估记录比例；不是人体犯错概率，双侧合并按记录去重',
                    'no_evidence_is_not_normal':True,'independent_specific_error_accuracy':None}

    def comparison(self,uid,auth,ids,versions=None,*,include_notes=True):
        if not isinstance(ids,list) or len(ids)!=2 or len(set(map(str,ids)))!=2:raise APIError('VALIDATION_FAILED','请选择两条不同的本人记录',422)
        ids=[uuid_id(v) for v in ids];snapshots=[];facts=[]
        if versions is not None:
            if not isinstance(versions,dict):raise APIError('VALIDATION_FAILED','记录版本无效',422)
            versions={uuid_id(k):integer(v,'expected_version') for k,v in versions.items()}
            if set(versions)!=set(ids):raise APIError('VALIDATION_FAILED','请选择记录对应的完整版本',422)
        for sid in ids:
            with self.db.transaction(False) as c:self.sessions._actor(c,uid,auth)
            data=self.sessions.snapshot(uid,sid,True)
            if versions is not None and (not isinstance(versions,dict) or versions.get(sid)!=data['state_version']):raise ConflictError('记录已变化，请重新选择比较')
            if data['source']=='demo' or not data['included_in_statistics'] or data['status'] not in TERMINAL:raise APIError('COMPARISON_UNAVAILABLE','请选择本人已结束的当前训练记录进行对比',409)
            snapshots.append(data);facts.append(compact_record(data,self.training_info.get_note(uid,auth,sid) if include_notes else None))
        comparable=(snapshots[0]['action_id']==snapshots[1]['action_id'] and snapshots[0]['capability_version']==snapshots[1]['capability_version'] and snapshots[0].get('input_policy')==snapshots[1].get('input_policy'));pairs=[]
        lookup={(r['code'],r['side'],r['rule_version'],r['view'],r['unit']):r for r in facts[1]['statistics']}
        if comparable:
            for previous in facts[0]['statistics']:
                key=tuple(previous[k] for k in ('code','side','rule_version','view','unit'));current=lookup.get(key)
                if current and key[3] not in {'unknown','auto'} and previous['evaluated']>0 and current['evaluated']>0:
                    pairs.append({'code':key[0],'title':next((v['title'] for v in snapshots[0]['supported_items'] if v['code']==key[0]),key[0]),'side':key[1],'rule_version':key[2],'view':key[3],'unit':key[4],
                        'before':previous,'after':current,'ratio_change':current['ratio']-previous['ratio'],
                        'interpretation':'仅比较已保存可评估结果，不能据此证明调整的因果效果或整体动作正确'})
        return {'records':facts,'comparable':bool(pairs),'comparisons':pairs,
                'reason':None if pairs else '动作、能力/规则版本、测量方式、侧别、机位/单位或可评估证据不满足比较条件',
                'evidence_level':'reference','independent_specific_error_accuracy':None,
                'evidence':[value for snapshot in snapshots for value in record_evidence(snapshot)]}


def register_training_insights(app):
    store=TrainingInsights(app.extensions['session_store'],app.extensions['training_info']);app.extensions['training_insights']=store

    @app.get('/api/v1/account/training-insights')
    def training_insights_get():
        return jsonify(data=store.history(current_user.user_id,current_user.id,dict(request.args)))

    @app.post('/api/v1/records/comparison')
    def training_comparison():
        data=body({'record_ids','expected_versions'})
        return jsonify(data=store.comparison(current_user.user_id,current_user.id,data.get('record_ids'),data.get('expected_versions')))
