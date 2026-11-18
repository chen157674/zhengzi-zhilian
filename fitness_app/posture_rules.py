"""姿态参考规则与建议。基于运动生物力学几何模型，提供姿态偏差分析与纠正建议。"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path

PARAMETER_SOURCE = '生物力学姿态参考规则 v1；用于图像动作姿态纠偏分析'
SOURCES = {
    'plank': 'https://www.acefitness.org/resources/everyone/exercise-library/32/front-plank/',
    'push_up': 'https://www.nasm.org/resource-center/exercise-library/push-up',
    'row': 'https://www.catalystathletics.com/exercise/171/Bent-Row/',
}
# 来源支持动作原则；下面的图像比例、角度容差和确认时间均由本工程定义。
RULES = {
    'squat.range': ('bodyweight_squat', '深蹲下蹲幅度偏小', ['髋', '膝'], ['side'], .15,
                    '在可控制、无不适的范围内尝试更完整地下蹲；若训练计划规定浅蹲，以计划为准。'),
    'push_up.range': ('standard_push_up', '俯卧撑下降幅度偏小', ['肩', '肘'], ['side'], .25,
                      '保持身体整体下降和推起；可改用较轻松的变式练习可控制的完整幅度。'),
    'push_up.hip_high': ('standard_push_up','俯卧撑过程中髋部偏高',['髋','躯干'],['side'],.08,
                         '让髋部回到肩踝连线附近，让身体整体下降和推起；难以维持时先降低动作难度。'),
    'push_up.hip_low': ('standard_push_up','俯卧撑过程中髋部下沉',['髋','躯干'],['side'],.06,
                        '把髋部轻轻带回肩踝连线附近，避免身体中段先下沉；难以维持时先降低动作难度。'),
    'reverse_lunge.range': ('reverse_lunge', '后撤腿屈膝幅度偏小', ['髋', '后撤侧膝'], ['oblique'], 120.,
                           '先站稳，再在舒适范围内增加下降幅度，起身后收回站立；不要用膝盖撞地。'),
    'glute_bridge.range': ('bodyweight_glute_bridge', '臀桥顶端髋部偏离肩膝连线', ['髋'], ['side'], 160.,
                          '保持双脚位置稳定，将髋部调整到肩膝连线附近；髋部已偏高时不要继续抬高或后仰。'),
    'sit_up.range': ('bent_knee_sit_up', '躯干抬起幅度偏小', ['躯干'], ['side'], 60.,
                    '核对所选动作是仰卧起坐而非卷腹；缓慢完成可控制的抬起和返回，不要牵拉头颈。'),
    'curl.range': ('standing_dumbbell_curl', '肘部屈伸幅度偏小', ['肘'], ['side', 'oblique'], 70.,
                   '尽量固定上臂，缓慢完成可控制的屈肘和伸回；避免用身体摆动带动哑铃。'),
    'curl.trunk_sway': ('standing_dumbbell_curl', '弯举过程中躯干摆动较大', ['肩', '髋'], ['side', 'oblique'], 18.,
                       '减小躯干前后借力，控制哑铃回落；必要时降低负重。主动借力的专项变式不适用此项。'),
    'curl.upper_arm_swing': ('standing_dumbbell_curl', '弯举过程中上臂摆动较大', ['肩', '肘'], ['side', 'oblique'], 25.,
                            '尝试保持上臂位置稳定，缓慢屈肘和伸回；避免用肩部大幅摆动带动哑铃。'),
    'plank.hip_high': ('forearm_plank', '髋部位置偏高', ['髋', '躯干'], ['side'], .08,
                      '让髋部稍向下调整，使肩、髋、脚踝的侧面连线更平直，保持自然呼吸。'),
    'plank.hip_low': ('forearm_plank', '髋部位置偏低', ['髋', '躯干'], ['side'], .06,
                     '将髋部轻轻抬回肩踝连线附近；难以维持时缩短本组时间，再重新建立姿势。'),
    'jumping_jack.arm_range': ('jumping_jack', '手臂展开幅度偏小', ['双肩', '双臂'], ['front'], 140.,
                              '在舒适范围内让双臂充分向上展开，再与双腿一起收回；避免刻意甩臂。'),
    'jumping_jack.leg_range': ('jumping_jack', '双腿展开幅度偏小', ['双腿'], ['front'], .55,
                              '保持可控制的节奏，让双脚清楚分开再并回；先保证平稳落地。'),
    'high_knees.height': ('high_knees', '抬膝高度低于参考目标', ['抬起侧髋', '抬起侧膝'], ['front', 'oblique'], -.10,
                         '保持躯干稳定，尝试让抬起侧的大腿更接近水平；可先放慢节奏。'),
    'overhead_press.elbow': ('barbell_overhead_press', '起始位肘腕横向对齐偏差较大', ['肘', '腕'], ['front'], .35,
                            '在起始位核对握距，让前臂从正面看更接近竖直；不要为追求对齐强扭手腕。'),
    'barbell_row.trunk': ('barbell_bent_over_row', '划船过程中躯干角度变化较大', ['躯干', '髋'], ['side', 'oblique'], 15.,
                         '若采用稳定躯干的划船方式，尝试减小前后摆动并控制回落；有意改变躯干角度的变式不适用此提示。'),
    'deadlift.extension': ('conventional_deadlift', '顶端髋膝伸展未达到参考带', ['髋', '膝'], ['side'], 160.,
                          '在站起顶端自然站直，再控制回落；不要为了伸展而后仰或强行顶膝。'),
}
LIMITATIONS = {
    'barbell_overhead_press':['只观察起始位肘腕图像关系；不判断全程杠铃轨迹、腰部代偿或适合本人的握距',
                             '上肢可见而腿脚裁切时不验证完整站姿变式，不提供次数'],
    'barbell_bent_over_row':['肩髋图像方向不是腰椎曲度；只适用于稳定躯干的划船变式'],
    'conventional_deadlift':['只观察完整周期顶端髋膝伸展；不能据此识别脊柱局部弯曲或来源PB错误标签'],
}
from .gym_catalogue import RULES as GYM_RULES, ACTION_IDS as GYM_ACTIONS, LIMITATIONS as GYM_LIMITATIONS
from .gym_catalogue import CANDIDATE_RULES
from .pretrained_policy import RULES as PRETRAINED_RULES,LIMITATIONS as PRETRAINED_LIMITATIONS,enabled as pretrained_enabled,body_enabled,active_rules
from .movement_guidance_policy import RULES as GUIDANCE_RULES,LIMITATIONS as GUIDANCE_LIMITATIONS,enabled as guidance_enabled
RULES.update(GYM_RULES)
LIMITATIONS.update({action:GYM_LIMITATIONS for action in GYM_ACTIONS})
REASONS = {
    'scene_reference_unavailable': '本项缺少可靠的画面参考方向，暂不判断侧倾',
    'scene_reference_dependency_missing': '本项参考方向暂不可用',
    'awaiting_motion': '等待连续动作后判断这一项，静止画面不作动作结论',
    'bilateral_projection_disagreement': '请稳定机位并让双侧肩髋持续入镜',
    'unsupported_view': '当前机位不适合判断这一项',
    'view_mismatch': '声明的机位与可见姿态方向明显不符',
    'view_unknown': '请调整拍摄角度并选择机位',
    'required_parts_or_identity_missing': '关键部位没有连续可见',
    'cycle_evidence_missing': '请完整完成一次动作并保持取景',
    'cycle_evidence_gap': '周期中有较长的观测中断',
    'extreme_duration_insufficient': '关键阶段缺少足够的连续帧',
    'extreme_not_bracketed_by_cycle': '没有观察到完整的离开、极值和返回',
    'awaiting_complete_cycle': '正在等待可观察的完整动作周期',
    'posture_duration_unconfirmed': '正在等待姿态偏差的连续观测确认',
    'rack_phase_unconfirmed': '当前不是可判断肘腕对齐的起始位',
    'forearm_support_unconfirmed': '请调整为前臂支撑姿势',
}


@lru_cache(maxsize=1)
def policy_version():
    folder = Path(__file__).parent
    names = ('posture_rules.py', 'posture_engine.py', 'quality_candidates.py', 'movement_quality.py',
             'barbell_quality.py', 'glute_bridge.py', 'sit_up.py', 'high_knees.py', 'overhead_press.py','frame_pipeline.py',
             'gym_catalogue.py','gym_quality.py','gym_motion.py','scene_reference.py')
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode())
        digest.update((folder/name).read_bytes())
    if body_enabled():
        for name in ('pretrained_policy.py','pretrained_quality.py','pretrained_input.py','rtmpose_input.py','pose_torch_backend.py','exercise_feedback.py','body_lean.py'):
            digest.update(name.encode());digest.update((folder/name).read_bytes())
        from .pretrained_resources import registry
        document=registry()
        for name in (('rtmpose-body','rtmpose-hand','rtmdet-hand') if pretrained_enabled() else ('rtmpose-body',)):
            model=document['models'][name];item=next(row for row in model['files'] if row['path']==model['onnx'])
            digest.update(item['sha256'].encode())
        for item in document['repos']['exercise-pose-feedback']['assets'] if pretrained_enabled() else []:
            if item['path'] in {'models/benchpress/benchpress.pkl','models/deadlift/deadlift.pkl','models/squat/squat.pkl'}:
                digest.update(item['sha256'].encode())
    if guidance_enabled():
        for name in ('movement_guidance_policy.py','movement_guidance.py','rtmpose_input.py','pose_torch_backend.py','pretrained_input.py','body_lean.py'):
            digest.update(name.encode());digest.update((folder/name).read_bytes())
        from .pretrained_resources import registry
        for name in ('rtmpose-body','rtmpose-hand','rtmdet-hand'):
            model=registry()['models'][name];item=next(row for row in model['files'] if row['path']==model['onnx'])
            digest.update(item['sha256'].encode())
    return 'posture-1-' + digest.hexdigest()[:12]+('-pretrained' if pretrained_enabled() else '-body-lean' if body_enabled() else '')+('-guidance' if guidance_enabled() else '')


EXPERIMENTAL_CODES=set(CANDIDATE_RULES)


def rules_for(action,include_candidates=False):
    version = policy_version()+('-candidate' if include_candidates else '')
    definitions={**RULES,**CANDIDATE_RULES} if include_candidates else RULES
    if body_enabled():definitions={**definitions,**active_rules()}
    if guidance_enabled():definitions={**definitions,**GUIDANCE_RULES}
    return [{'code': code, 'status': 'reference', 'title': title, 'body_parts': parts,
             'views': views, 'threshold': threshold, 'suggestion': advice,
             'rule_version': version, 'evidence_level': 'reference',
             'validation_status': 'not_independently_validated', 'parameter_source': PARAMETER_SOURCE,
             'limitations': LIMITATIONS.get(action,[])+(PRETRAINED_LIMITATIONS if code in PRETRAINED_RULES else GUIDANCE_LIMITATIONS if code in GUIDANCE_RULES else [])}
            for code, (owner, title, parts, views, threshold, advice) in definitions.items()
            if owner == action and (include_candidates or code not in EXPERIMENTAL_CODES)]


def describe(code, status, reason=None):
    rule = RULES[code] if code in RULES else CANDIDATE_RULES[code] if code in CANDIDATE_RULES else PRETRAINED_RULES[code] if code in PRETRAINED_RULES else GUIDANCE_RULES[code]
    if status == 'issue':
        return rule[1], rule[5]
    if status == 'not_observed':
        return '本次未观察到此项姿态偏差', ''
    advice='请改用稳定的严格侧面机位，使肩和髋连续可见，再完成一次划船。' if reason=='bilateral_projection_disagreement' else '请按推荐机位拍摄完整动作，使相关部位连续入镜。'
    return REASONS.get(reason, '该项暂时没有足够的姿态证据'), advice


def report_findings(data):
    """只根据记录自己的能力快照解释历史；不套用当前规则重新判历史正常／错误。"""
    supported = {x['code']: x for x in data.get('supported_items', [])}
    findings = []
    for event in data.get('events', []):
        for item in event.get('assessments', []):
            findings.append({**item, 'event_seq': event['event_seq'], 'side': event['side'], 'kind': 'cycle'})
    findings.extend({**item,'kind':'observation'} for item in data.get('posture_observations',[]))
    for item in data.get('intervals', []):
        if item.get('item_code'):
            rule = supported.get(item['item_code'], {})
            status = 'issue' if item['kind'] == 'issue' else 'unassessable'
            findings.append({'code': item['item_code'], 'status': status, 'start_ms': item['start_ms'],
                             'end_ms': item['end_ms'], 'side': 'none', 'kind': 'interval',
                             'message': rule.get('title') if status == 'issue' else item.get('reason'),
                             'suggestion': rule.get('suggestion', ''), 'body_parts': rule.get('body_parts', []),
                             'evidence_level': rule.get('evidence_level', 'legacy'),
                             'rule_version': item['algorithm_version'], 'reason': item.get('reason')})
    findings.sort(key=lambda f:(f['start_ms'],f['end_ms'],f['code']))
    assessed=[f for f in findings if f['status'] in {'issue','not_observed'}]
    item_summary=[]
    for code,rule in supported.items():
        observations=[f for f in findings if f['code']==code]
        issues=sum(f['status']=='issue' for f in observations)
        normal=sum(f['status']=='not_observed' for f in observations)
        item_summary.append({'code':code,'title':rule.get('title',code),'issue_observations':issues,
                             'assessed_observations':issues+normal,'unassessable_observations':sum(f['status']=='unassessable' for f in observations),
                             'status':'issue' if issues else 'not_observed' if normal else 'unassessable',
                             'reason':None if issues+normal else '没有可评估的完整周期／阶段证据，不能判断该项是否存在问题'})
    return {'schema_version': 1, 'findings': findings,
            'issue_count': sum(f['status'] == 'issue' for f in findings),
            'assessment_state':'issue_observed' if any(f['status']=='issue' for f in findings) else 'no_issue_observed_in_assessed_items' if assessed else 'evidence_insufficient',
            'item_summary':item_summary,
            'limitations':list(dict.fromkeys(text for rule in supported.values() for text in rule.get('limitations',[]))),
            'validation_status': 'not_independently_validated' if any(x.get('status') == 'reference' for x in supported.values()) else 'legacy',
            'scope': '可见姿态参考；不判断肌肉发力或实际关节载荷',
            'no_issue_is_not_overall_correct': True}
