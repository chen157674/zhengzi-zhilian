"""视频语义融合分类器；训练和运行共享特征，未验收的权重默认拒绝启用。"""
import hashlib
import json
import threading
import time
import numpy as np
from .paths import ROOT
from .semantic_features import feature_blocks, geometry_block, VERSION
from .video_vlm import VideoActionVLM, CONFIG, prompt, LABELS
from .recognition_observations import observations
from .world_features import WorldFeatures
from .pose import checked_model_hash
from .motion_evidence import read_policy,MotionPolicyError,evaluate as motion_evaluate,apply as motion_apply

MANIFEST=ROOT/'data/models/semantic-action-classifier.json'
MODEL=MANIFEST.with_suffix('.joblib')


def resources(research=False):
    try:
        info=json.loads(MANIFEST.read_text(encoding='utf-8'))
        if info['features_version']!=VERSION:raise ValueError('融合特征版本改变')
        if hashlib.sha256((ROOT/'fitness_app/semantic_features.py').read_bytes()).hexdigest()!=info['features_code_sha256']:
            raise ValueError('融合特征实现改变')
        if hashlib.sha256((ROOT/'fitness_app/world_features.py').read_bytes()).hexdigest()!=info['world_features_code_sha256']:
            raise ValueError('骨架特征实现改变')
        world=json.loads((ROOT/'data/models/skelgym/world-features.json').read_text(encoding='utf-8'))
        if world['sha256']!=info['world_encoder_sha256']:raise ValueError('世界坐标编码器改变')
        policy=info['encoder_policy']
        if hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()!=info['encoder_policy_sha256']:
            raise ValueError('视频编码策略身份错误')
        if policy['video_config']!=CONFIG or policy['prompt']!=prompt():raise ValueError('视频输入契约改变')
        for name in ('fitness_app/video_vlm.py','fitness_app/recognition_observations.py'):
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=policy['code'][name]:raise ValueError('视频观测实现改变')
        if len(info['classes'])!=len(LABELS) or set(info['classes'])!={a or 'unknown' for a in LABELS}:raise ValueError('融合类别不完整')
        if info['block'] not in {'semantic','visual','geometry','full'}:raise ValueError('融合输入类型不符')
        if not 0<info['minimum_score']<=1 or not 0<=info['minimum_margin']<=1:raise ValueError('融合阈值无效')
        read_policy(info)
        if info.get('refinement_enabled'):
            from .video_refinement import policy_version,FAMILIES
            if info['refinement_policy_version']!=policy_version():raise ValueError('分组复核策略改变')
            if not set(info['refinement_families'])<=set(FAMILIES):raise ValueError('未知复核分组')
            for filename,key in (('video_refinement.py','refinement_code_sha256'),('compact_video.py','refinement_compact_code_sha256')):
                if hashlib.sha256((ROOT/'fitness_app'/filename).read_bytes()).hexdigest()!=info[key]:raise ValueError('分组复核实现改变')
        if not research and not (info['runtime_enabled'] and (info['development_gate_passed'] or info.get('basic_development_gate_passed')) and info.get('release_gate_passed')):
            return None,'semantic_model_not_released'
        stat=MODEL.stat()
        if not checked_model_hash(str(MODEL),stat.st_mtime_ns,stat.st_size,info['sha256']):raise ValueError('融合权重不完整')
        world_path=ROOT/world['path'];stat=world_path.stat()
        if not checked_model_hash(str(world_path),stat.st_mtime_ns,stat.st_size,world['sha256']):raise ValueError('骨架权重不完整')
        return info,None
    except MotionPolicyError:return None,'motion_evidence_policy_unavailable'
    except (OSError,ValueError,KeyError,TypeError):return None,'semantic_model_unavailable'


class SemanticPredictor:
    """每实例串行使用GPU，公开方法只返回类别和证据摘要，不产生姿态纠错结论。"""
    def __init__(self,research=False):
        self.info,self.reason=resources(research)
        if self.info is None:raise RuntimeError(self.reason)
        self.lock=threading.Lock();self.head=None;self.video=None;self.world=WorldFeatures();self.refiner=None
        self.idle_timer=None;self.last_use=0.

    def _arm_idle(self):
        self.last_use=time.monotonic()
        if self.idle_timer:self.idle_timer.cancel()
        self.idle_timer=threading.Timer(60,self._unload_idle);self.idle_timer.daemon=True;self.idle_timer.start()

    def _unload_idle(self):
        with self.lock:
            if time.monotonic()-self.last_use<59:return
            if self.refiner:self.refiner.model=self.refiner.code_ids=None;self.refiner=None
            had_video=self.video is not None and self.video.model is not None
            self.video=None;self.idle_timer=None
            if had_video:
                import gc,torch
                gc.collect();torch.cuda.empty_cache()

    def _video(self):
        if self.video is None:
            self.video=VideoActionVLM('4b',quantized=True)
            if self.video.info['revision']!=self.info['encoder_policy']['base_revision']:raise ValueError('视频编码基座版本改变')
        return self.video

    def warm(self):
        with self.lock:
            self._load_head()
            if self.info['block']!='geometry':self._arm_idle();self._video().load()

    def _load_head(self):
        if self.head is not None:return
        import joblib
        if hashlib.sha256(MODEL.read_bytes()).hexdigest()!=self.info['sha256']:raise ValueError('融合模型摘要改变')
        head=joblib.load(MODEL)
        if list(head.classes_)!=self.info['classes']:raise ValueError('融合模型标签顺序改变')
        self.head=head

    def from_features(self,embedding,prediction,world=None,visibility=0.):
        self._load_head()
        blocks=feature_blocks(embedding,prediction,world,visibility)
        return self._decision(blocks[self.info['block']],world is not None)

    def _decision(self,feature,world_available):
        probabilities=self.head.predict_proba(feature[None])[0]
        if not np.isfinite(probabilities).all():raise ValueError('融合分类分数无效')
        order=np.argsort(probabilities)[::-1];top=str(self.head.classes_[order[0]])
        score=float(probabilities[order[0]]);margin=score-float(probabilities[order[1]])
        accepted=top!='unknown' and score>=self.info['minimum_score'] and margin>=self.info['minimum_margin']
        return {'action_id':top if accepted else None,'candidate_action_id':top if accepted else None,
                'top_action_id':None if top=='unknown' else top,'score':score,'margin':margin,
                'reason':None if accepted else 'video_background' if top=='unknown' else 'video_uncertain',
                'model_version':self.info.get('base_version',self.info['version']),'decision_basis':'trained_video_semantic_pose_fusion',
                'motion_evidence_applied':False,
                'candidates':[(str(self.head.classes_[i]),float(probabilities[i])) for i in order[:4]],
                'score_is_calibrated_probability':False,'world_features_available':world_available,
                'release_gate_passed':bool(self.info.get('release_gate_passed'))}

    def predict(self,images,timestamps,arrays):
        with self.lock:
            if self.info['block']!='geometry':self._arm_idle()
            facts,box=observations(arrays,timestamps[0],timestamps[-1]);detail=None
            world,visibility=self.world.extract(arrays,timestamps[0],timestamps[-1]) if 'world' in arrays else (None,0.)
            if self.info['block']=='geometry':
                self._load_head()
                return self._with_motion(self._decision(geometry_block(facts,world,visibility),world is not None),arrays,timestamps)
            if box:
                image=images[len(images)//2];h,w=image.shape[:2];left,top,right,bottom=box
                detail=np.ascontiguousarray(image[round(top*h):round(bottom*h),round(left*w):round(right*w)])
            self._video()
            if self.video.model is not None:
                import torch
                torch.cuda.empty_cache()
            prediction=self.video.predict(images,timestamps,facts,detail,return_embedding=True)
            primary=self.from_features(prediction.pop('_embedding'),prediction,world,visibility)
            if self.info.get('refinement_enabled'):
                from .video_refinement import VideoFamilyRefiner,choose_family,combine
                family=choose_family(primary,prediction)
                if family in self.info.get('refinement_families',[]) and (primary['action_id'] is not None or not self.info.get('refinement_require_primary',True)):
                    if self.refiner is None:self.refiner=VideoFamilyRefiner(self.video)
                    review=self.refiner.review(images,timestamps,family,facts,detail)
                    return self._with_motion(combine(primary,review,family),arrays,timestamps)
            return self._with_motion(primary,arrays,timestamps)

    def _with_motion(self,result,arrays,timestamps):
        policy=self.info.get('motion_evidence')
        if not policy:return result
        evidence=motion_evaluate(arrays,int(timestamps[0]),int(timestamps[-1]),result.get('action_id'),policy['config'])
        result=motion_apply(result,evidence)
        result.update(model_version=self.info['version'],base_model_version=self.info.get('base_version'),motion_evidence_applied=True)
        return result
