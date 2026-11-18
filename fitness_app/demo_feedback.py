"""把同一时刻的真实姿态判项整理成演示反馈，不把缺少证据当作正确。"""
from .coaching import coaching_observation
from .skeleton import skeleton_snapshot

FEEDBACK_SCHEMA_VERSION=1
STATES={'clear','issue','unassessable','observing'}
WAITING={'awaiting_motion','awaiting_complete_cycle','posture_duration_unconfirmed','cycle_evidence_missing'}
VIEW_NAMES={'front':'正面','side':'侧面','oblique':'斜侧面'}


def check_label(rule):
    code=rule['code']
    for suffix,label in (('arm_asymmetry','双侧手臂高度'),('trunk_sway','躯干稳定性'),('upper_arm_swing','上臂稳定性')):
        if code.endswith(suffix):return label
    return rule['title']


def build_demo_feedback(frame,output,rules,coaching):
    observation=coaching_observation(output)
    posture=observation.get('posture',{})
    quality=frame.get('framing',{})
    valid_target=quality.get('person_count')==1 and quality.get('usable') and frame.get('identity_accepted') is not False
    items=posture.get('items',[])
    checks=[]
    for rule in rules:
        found=[item for item in items if item.get('code')==rule['code']]
        for item in found or [{}]:
            status=item.get('status','unassessable') if valid_target else 'unassessable'
            if status not in {'issue','not_observed','unassessable'}:status='unassessable'
            reason=item.get('reason') or 'awaiting_complete_cycle'
            if not valid_target:
                reason='target_ambiguous' if quality.get('person_count',0)>1 else 'required_parts_or_identity_missing'
            if status=='not_observed':detail='未发现偏差'
            elif status=='issue':detail=item.get('message') or rule['title']
            elif reason=='unsupported_view':detail='需要'+('或'.join(VIEW_NAMES[v] for v in rule.get('views',[]) if v in VIEW_NAMES))+'机位'
            elif reason=='target_ambiguous':detail='请保持单人完整入镜'
            elif reason in WAITING:detail='等待连续动作证据'
            else:detail=item.get('message') or '请保持关键部位持续入镜'
            side=item.get('side','none')
            label=check_label(rule)+('（左侧）' if side=='left' else '（右侧）' if side=='right' else '')
            checks.append({'code':rule['code'],'label':label,'status':status,'detail':detail,
                           'reason':reason if status=='unassessable' else None,'side':side})
    assessed=sum(item['status']!='unassessable' for item in checks)
    unknown=sum(item['status']=='unassessable' for item in checks)
    issues={item['code'] for item in checks if item['status']=='issue'}
    primary=coaching.get('primary_cue') or {}
    confirmed=valid_target and coaching.get('state')=='needs_adjustment' and primary.get('code') in issues
    result={'schema_version':FEEDBACK_SCHEMA_VERSION,'checks':checks,'assessed_count':assessed,'unknown_count':unknown,
            'rule_version':posture.get('rule_version'),'highlight':None,'overall_correctness':None,
            'scope_note':f'已评估{assessed}项'+(f'；另有{unknown}项受机位或可见性限制。' if unknown else '；仅代表已检查项目。')}
    if confirmed:
        result.update(state='issue',title=primary.get('message') or '发现需要调整的地方',
                      description='检测到可见关节的姿态偏差，问题部位已标出。',
                      instruction=primary.get('suggestion') or '请先核对问题部位，再在可控制范围内调整。',
                      highlight=skeleton_snapshot(frame,coaching,time_base='source_ms')['highlight'])
    elif issues:
        result.update(state='observing',title='正在确认姿态变化',description='等待连续证据确认后显示具体问题。',instruction='保持相关部位连续入镜。')
    elif assessed:
        result.update(state='clear',title='当前检查项未发现错误',description='已评估的项目正常，继续保持平稳、可控制的动作。',instruction='')
    elif checks and valid_target and any(item['reason'] in WAITING for item in checks):
        result.update(state='observing',title='正在观察动作',description='需要一小段连续动作后，才能判断姿态。',instruction='')
    else:
        result.update(state='unassessable',title='',description='请调整机位，让关键部位持续入镜。',instruction='请按下方检查项的机位要求，让相关部位连续入镜。')
    return result
