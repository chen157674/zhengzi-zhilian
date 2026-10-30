"""复测只比较已保存的一项提示；复用新训练记录，不存第二套姿态结论。"""
import re
from flask import jsonify
from flask_login import current_user
from .auth import APIError
from .database import ConflictError
from .records import TERMINAL,uuid_id,integer
from .training_insights import record_evidence,record_statistics

VERSION='single-item-retest-1'
MINIMUM_CYCLES=3
MINIMUM_OBSERVED_MS=2000
MINIMUM_COVERAGE=.8


def evaluate(origin,target,evidence):
    result={'policy_version':VERSION,'state':'not_comparable','message':'两段观察条件不一致，暂不能比较。',
        'source_record_id':origin['session_id'],'target_record_id':target['session_id'],
        'code':evidence['code'],'side':evidence.get('side','none'),'rule_version':evidence['rule_version'],
        'title':evidence['title'],'suggestion':evidence.get('suggestion'),
        'evidence_level':'reference','independent_specific_error_accuracy':None,
        'no_issue_is_not_overall_correct':True,'improvement_percentage':None,
        'minimum_cycles':MINIMUM_CYCLES,'minimum_observed_ms':MINIMUM_OBSERVED_MS,'minimum_coverage':MINIMUM_COVERAGE,
        'observation':None}
    if target['action_id']!=origin['action_id'] or target.get('source')!='camera' or target.get('included_in_statistics') is False:return result
    if target['status'] not in TERMINAL:return {**result,'state':'collecting','message':'复测采集中；主动结束后再查看这一项的结果。'}
    view=evidence.get('evidence',{}).get('view')
    items=[v for v in record_statistics(target) if v['code']==evidence['code'] and v['side']==result['side']]
    if not items:return {**result,'state':'insufficient_evidence','message':'没有保存足够的同项观察，不能确认这项调整。'}
    unit='cycles' if target.get('counting_available') and target.get('unit')!='seconds' else 'ms'
    source_unit='cycles' if origin.get('counting_available') and origin.get('unit')!='seconds' else 'ms'
    if unit!=source_unit:return result
    if view not in {'front','side','oblique'} or any(v['view']!=view or v['rule_version']!=evidence['rule_version'] or v['unit']!=unit for v in items):return result
    # 相同键只应有一个统计项，不能从多个机位/单位中挑一个好看的结果。
    if len(items)!=1:return result
    item=items[0];denominator=item['evaluated']+item['unknown']
    if unit=='ms':denominator=max(denominator,target.get('active_ms',0))
    coverage=min(1.,item['evaluated']/denominator) if denominator else 0.
    observation={k:item[k] for k in ('unit','issue','evaluated','unknown','view')}
    observation['coverage']=coverage;result['observation']=observation
    minimum=MINIMUM_CYCLES if unit=='cycles' else MINIMUM_OBSERVED_MS
    if item['evaluated']<minimum or coverage<MINIMUM_COVERAGE:
        return {**result,'state':'insufficient_evidence','message':'同项有效观察还不足，无法确认调整效果；未知区间不算正常。'}
    if item['issue']:
        return {**result,'state':'reobserved','message':'本次复测仍观察到这一项参考提示。'}
    return {**result,'state':'not_reobserved','message':'本次可评估范围未再次观察到这一项偏差；不代表整体动作正确。'}


class Retests:
    def __init__(self,sessions):self.sessions=sessions;self.db=sessions.db

    def options(self,uid,sid,auth=None):
        if auth:
            with self.db.transaction(False) as c:self.sessions._actor(c,uid,auth)
        data=self.sessions.snapshot(uid,uuid_id(sid),True)
        ready=data['status'] in TERMINAL and data['source']!='demo' and data.get('included_in_statistics') is not False
        candidates=[]
        if ready:
            for item in record_evidence(data):
                if item['status']!='issue' or item.get('evidence',{}).get('view') not in {'front','side','oblique'}:continue
                candidates.append({'origin':{'session_id':data['session_id'],'expected_version':data['state_version'],'evidence_id':item['id']},
                    'code':item['code'],'side':item.get('side','none'),'rule_version':item['rule_version'],
                    'view':item['evidence']['view'],'title':item['message'] or item['title'],'suggestion':item.get('suggestion'),
                    'action_id':data['action_id'],'action_name':data['action_name'],'evidence_level':'reference'})
        return {'record_id':data['session_id'],'state_version':data['state_version'],'options':candidates,
            'message':'选择一项提示，主动开启相机采集新的片段。' if candidates else '这条记录还没有可复测的已保存提示；需已结束且机位明确的个人有效记录。'}

    def resolve(self,uid,auth,origin):
        if not isinstance(origin,dict) or set(origin)!={'session_id','expected_version','evidence_id'}:raise APIError('VALIDATION_FAILED','复测来源字段不完整或含未知字段',422)
        sid=uuid_id(origin['session_id']);version=integer(origin['expected_version'],'expected_version')
        evidence_id=origin['evidence_id']
        if not isinstance(evidence_id,str) or not re.fullmatch(r'e:[0-9a-f]{24}',evidence_id):raise APIError('VALIDATION_FAILED','复测提示标识无效',422)
        available=self.options(uid,sid,auth)
        if available['state_version']!=version:raise ConflictError('原提示已更新，请重新选择复测项目')
        selected=next((v for v in available['options'] if v['origin']['evidence_id']==evidence_id),None)
        if selected is None:raise APIError('RETEST_SOURCE_UNAVAILABLE','原提示不可用于复测，请重新读取记录',409)
        return selected

    def prepare(self,uid,auth,origin):return self.resolve(uid,auth,origin)['origin']

    def summary(self,uid,target):
        ref=target.get('config',{}).get('retest_origin')
        if not ref:return None
        missing={'policy_version':VERSION,'state':'source_unavailable','message':'原提示已删除、更新或退出有效统计，复测比较不可用。',
            'target_record_id':target['session_id'],'source_record_id':ref.get('session_id'),
            'evidence_level':'reference','independent_specific_error_accuracy':None,'improvement_percentage':None,'no_issue_is_not_overall_correct':True}
        try:
            origin=self.sessions.snapshot(uid,uuid_id(ref.get('session_id')),True)
        except APIError:return missing
        if origin['state_version']!=ref.get('expected_version') or origin['status'] not in TERMINAL or origin['source']=='demo' or origin.get('included_in_statistics') is False:return missing
        selected=next((v for v in record_evidence(origin) if v['id']==ref.get('evidence_id') and v['status']=='issue'),None)
        return evaluate(origin,target,selected) if selected else missing


def register_retests(app):
    service=Retests(app.extensions['session_store']);app.extensions['retests']=service

    @app.get('/api/v1/records/<session_id>/retest-options')
    def retest_options(session_id):return jsonify(data=service.options(current_user.user_id,uuid_id(session_id),current_user.id))
