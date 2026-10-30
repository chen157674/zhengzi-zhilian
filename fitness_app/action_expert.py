"""离线动作子类复核边界；候选未注册到生产推理，不扩大未知输入的接纳范围。"""
import math
from .video_refinement import FAMILIES

CHEST=tuple(FAMILIES['chest'])
OTHER='other_action'
MINIMUM_SCORE=.8
MINIMUM_MARGIN=.25


def labels_for(row):
    """只映射监督集合，不把动作名、来源或分区加入数值特征。"""
    allowed=row['label_scope']['allowed_actions']
    if not isinstance(allowed,list) or not allowed or any(not isinstance(v,str) for v in allowed):raise ValueError('动作监督集合无效')
    return sorted({v if v in CHEST else OTHER for v in allowed})


def refine_chest(base,action,score,margin,version):
    result=dict(base)
    if base.get('action_id') not in CHEST or action not in CHEST or base['action_id']==action:return result
    if (base.get('motion_evidence') or {}).get('veto') is True:return result
    if not all(type(v) in (float,int) and math.isfinite(v) and 0<=v<=1 for v in (score,margin)):return result
    if score<MINIMUM_SCORE or margin<MINIMUM_MARGIN:return result
    result.update(action_id=action,candidate_action_id=action,top_action_id=action,score=float(score),margin=float(margin),reason=None,
        decision_basis='experimental_chest_subclass_expert',model_version=version,score_is_calibrated_probability=False,runtime_enabled=False,
        expert_review={'base_action_id':base['action_id'],'base_model_version':base.get('model_version'),'base_decision_basis':base.get('decision_basis'),
            'scope':'recognized_chest_actions_only; no unknown rescue or posture judgment'})
    return result


def select_temporal_training(rows,negative_limit=4):
    """全取胸部正例；每个其他原类别取来源多样的固定负例，不读验证结果。"""
    if type(negative_limit) is not int or negative_limit<1:raise ValueError('负例上限无效')
    selected=set();buckets={}
    for i,row in enumerate(rows):
        if row['partition']!='train':continue
        positive=any(a in CHEST for a in labels_for(row))
        mandatory=row.get('training_origin')=='bounded_partial_view_training'
        if positive or mandatory:selected.add(i)
        if not positive:buckets.setdefault(row['action_id'],[]).append(i)
    for action,candidates in sorted(buckets.items()):
        picked=[i for i in candidates if i in selected];remaining=[i for i in candidates if i not in selected]
        while len(picked)<negative_limit and remaining:
            groups={rows[i]['group'] for i in picked};sources={rows[i]['source_sha256'] for i in picked}
            eligible=[i for i in remaining if rows[i]['source_sha256'] not in sources]
            if not eligible:break
            index=min(eligible,key=lambda i:(rows[i]['group'] in groups,rows[i]['source_sha256'],rows[i]['id']))
            picked.append(index);selected.add(index);remaining.remove(index)
    return sorted(selected)
