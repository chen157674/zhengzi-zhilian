"""健身房动作零样本视觉候选与时序门控；不冒充已训练或已校准的类别。"""
from functools import lru_cache
import hashlib,json
from pathlib import Path
import numpy as np
from .paths import ROOT
from .visual_recognition import PROMPTS as BASE_PROMPTS,normalize
from .gym_catalogue import BY_ID,ACTION_IDS
from .gym_motion import measurements,motion_gate

DIRECTORY=ROOT/'data/models'
MODEL=DIRECTORY/'gym-text-features.npz'
MANIFEST=DIRECTORY/'gym-recognition.json'
PROMPTS={**BASE_PROMPTS,**{a:x['prompt'] for a,x in BY_ID.items()},
         'unknown_band_raise':'a person raising resistance bands sideways',
         'unknown_arnold_press':'a person rotating dumbbells in an Arnold shoulder press',
         'unknown_cable_setup':'a person adjusting a cable exercise machine without doing an exercise'}
CONFIG={'schema':'gym-clip-motion-1','minimum_cosine':.24,'minimum_margin':.012,
        'minimum_images':3,'minimum_image_span_ms':1000,'maximum_image_age_ms':750,
        'image_window_ms':2000,'confirm_ms':1000,'prompt_templates':['A photo of {}.','A video frame of {}.','An exercise demonstration of {}.']}


def signature():
    return hashlib.sha256(json.dumps({'prompts':PROMPTS,'config':CONFIG},sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=1)
def implementation_version():
    folder=Path(__file__).parent
    return hashlib.sha256(b''.join((folder/name).read_bytes() for name in ('gym_recognition.py','gym_motion.py','basic_recognition.py','sessions.py','temporal_gym.py'))).hexdigest()[:16]


@lru_cache(maxsize=2)
def _load(modified,size,manifest_modified):
    info=json.loads(MANIFEST.read_text(encoding='utf-8'))
    if info['signature']!=signature() or hashlib.sha256(MODEL.read_bytes()).hexdigest()!=info['sha256']:raise ValueError('新增动作资源身份不符')
    backbone=json.loads((DIRECTORY/'clip-vit-base-patch32/manifest.json').read_text(encoding='utf-8'))
    if info['backbone_revision']!=backbone['revision']:raise ValueError('视觉骨干身份不符')
    with np.load(MODEL,allow_pickle=False) as values:
        features=values['features'].copy();labels=values['labels'].tolist()
    if labels!=list(PROMPTS) or features.shape!=(len(labels),512) or not np.isfinite(features).all():raise ValueError('新增动作资源格式不符')
    return {'labels':labels,'features':features,'version':info['version']}


def resources():
    try:
        stat=MODEL.stat();return _load(stat.st_mtime_ns,stat.st_size,MANIFEST.stat().st_mtime_ns),None
    except (OSError,ValueError,KeyError,TypeError):return None,'gym_model_unavailable'


@lru_cache(maxsize=2)
def _head(modified,size,manifest_modified):
    path=DIRECTORY/'gym-action-classifier.joblib'
    info=json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    if hashlib.sha256(path.read_bytes()).hexdigest()!=info['sha256']:raise ValueError('新增分类头摘要不同')
    if not set(info['new_actions'])<=set(ACTION_IDS):raise ValueError('分类头动作不一致')
    return info


@lru_cache(maxsize=2)
def load_head(digest):
    import joblib
    path=DIRECTORY/'gym-action-classifier.joblib'
    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('分类头加载时摘要改变')
    return joblib.load(path)


def trained_resources():
    try:
        path=DIRECTORY/'gym-action-classifier.joblib';stat=path.stat()
        return _head(stat.st_mtime_ns,stat.st_size,path.with_suffix('.json').stat().st_mtime_ns),None
    except (OSError,ValueError,KeyError,TypeError,ImportError):return None,'gym_trained_head_unavailable'


def motion_evidence(action,frames):
    # 飞鸟的上臂展开可由肩、肘、髋观察；缺腕不伪造腕点，也不开放腕部纠错。
    require_wrist=BY_ID[action]['family'] not in {'fly','reverse_fly','bent_fly'}
    return measurements(frames,require_wrist=require_wrist)


def candidate(resource,images,frames,stamp,head=None):
    """None表示未命中新类别；拒判字典表示疑似新类别，禁止回落为旧类别。"""
    if resource is None:return None
    recent=[(t,v) for t,v in images if 0<=stamp-t<=CONFIG['image_window_ms']]
    if len(recent)<CONFIG['minimum_images'] or recent[-1][0]-recent[0][0]<CONFIG['minimum_image_span_ms']:return None
    if stamp-recent[-1][0]>CONFIG['maximum_image_age_ms']:return {'candidate_action_id':None,'reason':'waiting_for_visual','score':None}
    head_rejection=None
    if head:
        model=load_head(head['sha256'])
        if set(model.classes_)!=set(head['actions']):raise ValueError('分类头标签不一致')
        scores=model.predict_proba(normalize(np.mean([v for _,v in recent],axis=0))[None])[0]
        order=np.argsort(scores)[::-1];top=str(model.classes_[order[0]]);value=float(scores[order[0]]);margin=value-float(scores[order[1]])
        if top in head['new_actions']:
            result={'candidate_action_id':None,'reason':'uncertain_gym_variant','score':value,
                    'decision_basis':'development_visual_classifier_and_temporal_pose','classification_method':'supervised_development',
                    'candidate_variant':top,'visual_margin':margin}
            if value<head['config']['minimum_score'] or margin<head['config']['minimum_margin']:
                # 闭集分类器不认识其余新增标签，低置信结果不能阻断开放类别的证据。
                head_rejection=result
            else:
                if not motion_gate(top,motion_evidence(top,frames)):result['reason']='gym_motion_unconfirmed';return result
                votes=model.predict(np.asarray([v for _,v in recent])).tolist()
                if votes.count(top)<max(2,len(votes)-1):result['reason']='gym_visual_unstable';return result
                result.update(candidate_action_id=top,reason=None,motion_evidence={'family':BY_ID[top]['family']})
                return result
    scores=resource['features']@normalize(np.mean([v for _,v in recent],axis=0));order=np.argsort(scores)[::-1]
    top=resource['labels'][int(order[0])];value=float(scores[order[0]]);margin=value-float(scores[order[1]])
    if top not in ACTION_IDS:
        if top.startswith('unknown_') and value>=CONFIG['minimum_cosine'] and margin>=CONFIG['minimum_margin']:
            return {'candidate_action_id':None,'reason':'background_or_unsupported_variant','score':None}
        return head_rejection
    result={'candidate_action_id':None,'reason':'uncertain_gym_variant','score':None,
            'visual_similarity':value,'visual_margin':margin,'decision_basis':'zero_shot_visual_and_temporal_pose',
            'classification_method':'zero_shot','candidate_variant':top}
    if value<CONFIG['minimum_cosine'] or margin<CONFIG['minimum_margin']:return result
    values=motion_evidence(top,frames)
    if not motion_gate(top,values):result['reason']='gym_motion_unconfirmed';return result
    # 连续图像必须至少两帧同意，不能由一个偶然的画面平均值决定变式。
    votes=[resource['labels'][int(np.argmax(resource['features']@v))] for _,v in recent]
    if votes.count(top)<max(2,len(votes)-1):result['reason']='gym_visual_unstable';return result
    result.update(candidate_action_id=top,reason=None,motion_evidence={'family':BY_ID[top]['family'],
                  'start_ms':values['start_ms'],'end_ms':values['end_ms']})
    return result
