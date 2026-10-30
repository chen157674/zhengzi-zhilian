"""离线分类头适配与受限绳索/拉类候选边界；无生产注册和默认启用。"""
import math
import numpy as np
from .video_refinement import FAMILIES
from .action_expert import MINIMUM_SCORE,MINIMUM_MARGIN

UPPER=set(FAMILIES['cable']+FAMILIES['pull']+('dumbbell_front_raise','dumbbell_lateral_raise','reverse_pec_deck_fly','bent_over_dumbbell_reverse_fly'))


def route_needed(base):
    action=base.get('action_id')
    if (base.get('motion_evidence') or {}).get('veto') is True:return False
    if action in UPPER:return True
    if action is not None:return False
    return base.get('top_action_id') in UPPER and type(base.get('score')) in (int,float) and math.isfinite(base['score']) and base['score']<.7


def apply_adapted(base,proposed,score,margin,motion,version):
    result=dict(base)
    if not route_needed(base) or proposed not in UPPER or proposed==base.get('action_id'):return result
    if not all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in (score,margin)) or score<MINIMUM_SCORE or margin<MINIMUM_MARGIN:return result
    if motion.get('state')!='moving' or motion.get('veto') is not False:return result
    result.update(action_id=proposed,candidate_action_id=proposed,top_action_id=proposed,score=float(score),margin=float(margin),reason=None,
        model_version=version,decision_basis='experimental_adapted_domain_head',runtime_enabled=False,score_is_calibrated_probability=False,
        adaptation_review={'base_action_id':base.get('action_id'),'base_model_version':base.get('model_version'),'scope':'related upper actions; low-margin rescue requires positive motion'},motion_evidence=motion)
    return result


def adapt_linear(features,mask,weights,teacher,initial_weight,initial_bias,steps=200):
    """固定CPU步骤；只在训练真值允许且教师高置信时蒸馏，避免固化已知错类。"""
    import torch
    from scripts.partial_action_training import partial_loss
    torch.set_num_threads(4);torch.manual_seed(20261024)
    x=torch.as_tensor(features,dtype=torch.float32);allowed=torch.as_tensor(mask,dtype=torch.bool);w=torch.as_tensor(weights,dtype=torch.float32)
    probability=torch.as_tensor(teacher,dtype=torch.float32);original_w=torch.as_tensor(initial_weight,dtype=torch.float32);original_b=torch.as_tensor(initial_bias,dtype=torch.float32)
    if x.ndim!=2 or probability.shape!=mask.shape or initial_weight.shape!=(mask.shape[1],x.shape[1]) or initial_bias.shape!=(mask.shape[1],):raise ValueError('分类头适配维度无效')
    if not np.isfinite(features).all() or not np.isfinite(teacher).all() or np.any(teacher<0) or not np.allclose(teacher.sum(1),1,atol=1e-5):raise ValueError('分类头适配数值无效')
    top=probability.argmax(1);protected=(probability.max(1).values>=.8)&allowed[torch.arange(len(x)),top]
    weight=torch.nn.Parameter(original_w.clone());bias=torch.nn.Parameter(original_b.clone());optimizer=torch.optim.AdamW([weight,bias],lr=.001,weight_decay=0.)
    history=[]
    for step in range(steps):
        optimizer.zero_grad();logits=x@weight.T+bias;classification=partial_loss(logits,allowed,w)
        if protected.any():
            reference=probability[protected];log_probability=torch.log_softmax(logits[protected],1)
            divergence=(reference*(torch.log(reference.clamp_min(1e-8))-log_probability)).sum(1);distillation=(divergence*w[protected]).sum()/w[protected].sum()
        else:distillation=classification*0
        anchor=.001*((weight-original_w).square().sum()+(bias-original_b).square().sum())/2
        loss=classification+.2*distillation+anchor
        if not torch.isfinite(loss):raise ValueError('分类头适配损失非有限')
        loss.backward();torch.nn.utils.clip_grad_norm_([weight,bias],1.);optimizer.step()
        if step==0 or (step+1)%50==0:history.append({'step':step+1,'loss':float(loss.detach()),'classification':float(classification.detach()),'distillation':float(distillation.detach()),'anchor':float(anchor.detach())})
    return {'coef':weight.detach().numpy().copy(),'intercept':bias.detach().numpy().copy(),'history':history,'protected_teacher_samples':int(protected.sum()),'steps':steps}
