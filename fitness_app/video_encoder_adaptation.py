"""离线视频编码器末层适配；固定范围与运动门槛，不注册线上模型。"""
import hashlib,math
from .head_adaptation import UPPER
from .video_refinement import FAMILIES

SCOPE=UPPER|set(FAMILIES['curl'])
PREFIXES=('encoder.vision_model.encoder.layers.11.','encoder.vision_model.post_layernorm.','encoder.mit.','encoder.visual_projection.','head.')


def configure(net):
    selected={}
    for name,value in net.named_parameters():
        enabled=name.startswith(PREFIXES);value.requires_grad_(enabled)
        if enabled:selected[name]=value
    if not all(any(n.startswith(prefix) for n in selected) for prefix in PREFIXES):raise ValueError('视觉末层/时序/投影/分类头结构不同，不能静默只训练头')
    return selected


def parameter_sha(net,names):
    wanted=set(names);h=hashlib.sha256()
    for name,value in net.named_parameters():
        if name in wanted:
            array=value.detach().cpu().numpy();h.update(name.encode());h.update(str(array.shape).encode());h.update(str(array.dtype).encode());h.update(array.tobytes())
    return h.hexdigest()


def loss_terms(logits,allowed,weights,teacher):
    import torch
    if logits.ndim!=2 or allowed.shape!=logits.shape or teacher.shape!=logits.shape or weights.shape!=(len(logits),):raise ValueError('视频适配损失维度无效')
    if allowed.dtype!=torch.bool or not allowed.any(1).all() or not all(torch.isfinite(v).all() for v in (logits,teacher,weights)) or not (weights>0).all():raise ValueError('视频适配标签或数值无效')
    if (teacher<0).any() or not torch.allclose(teacher.sum(1),torch.ones(len(teacher),device=teacher.device),atol=1e-5,rtol=0):raise ValueError('原教师分布无效')
    ce=(torch.logsumexp(logits,1)-torch.logsumexp(logits.masked_fill(~allowed,-torch.inf),1))*weights
    top=teacher.argmax(1);protected=(teacher.max(1).values>=.8)&allowed[torch.arange(len(logits),device=logits.device),top]
    divergence=(teacher*(teacher.clamp_min(1e-8).log()-logits.log_softmax(1))).sum(1)*weights*protected
    return ce,divergence,protected


def route_needed(base):
    if (base.get('motion_evidence') or {}).get('veto') is True:return False
    if base.get('action_id') in SCOPE:return True
    score=base.get('score')
    return base.get('action_id') is None and base.get('top_action_id') in SCOPE and type(score) in (int,float) and math.isfinite(score) and score<.7


def apply_prediction(base,proposed,score,margin,motion,version):
    if not route_needed(base) or proposed not in SCOPE or proposed==base.get('action_id'):return dict(base)
    if not all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in (score,margin)) or score<.8 or margin<.25:return dict(base)
    if motion.get('state')!='moving' or motion.get('veto') is not False:return dict(base)
    return dict(base,action_id=proposed,candidate_action_id=proposed,top_action_id=proposed,score=score,margin=margin,reason=None,model_version=version,
        decision_basis='experimental_video_encoder_adapter',runtime_enabled=False,score_is_calibrated_probability=False,motion_evidence=motion,
        adaptation_review={'base_action_id':base.get('action_id'),'base_model_version':base.get('model_version'),'scope':'upper and curl specialist; low score rescue requires positive motion'})
