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
        if not research:
            from .pull_press_review import release_policy
            extra=release_policy(info)
            if extra:info={**info,'pull_press_review':extra,'pull_press_base_version':info['version'],
                'version':info['version']+'-'+extra['version']+'-'+extra.get('execution_version','late1')}
            from .overhead_action_review import release_policy as fast_policy
            fast=fast_policy(info)
            if fast:info={**info,'overhead_fast_review':fast,'overhead_fast_base_version':info['version'],
                'version':info['version']+'-'+fast['version']+'-'+fast.get('execution_version','cache1')}
        return info,None
    except MotionPolicyError:return None,'motion_evidence_policy_unavailable'
    except (OSError,ValueError,KeyError,TypeError):return None,'semantic_model_unavailable'


class SemanticPredictor:
    """每实例串行使用GPU，公开方法只返回类别和证据摘要，不产生姿态纠错结论。"""
    def __init__(self,research=False):
        self.info,self.reason=resources(research)
        if self.info is None:raise RuntimeError(self.reason)
        self.lock=threading.Lock();self.head=None;self.video=None;self.world=WorldFeatures();self.refiner=None
        self.idle_timer=None;self.last_use=0.;self.idle_generation=0;self.pull_press_reviewer=None;self.overhead_reviewer=None;self.fast_primed=False;self.fast_warmup_forwards=0

    def _arm_idle(self):
        self.last_use=time.monotonic()
        if self.idle_timer:self.idle_timer.cancel()
        self.idle_generation+=1;generation=self.idle_generation
        self.idle_timer=threading.Timer(60,lambda:self._unload_idle(generation));self.idle_timer.daemon=True;self.idle_timer.start()

    def _unload_idle(self,generation=None):
        with self.lock:
            if generation is not None and generation!=self.idle_generation:return
            if time.monotonic()-self.last_use<59:return
            if self.refiner:self.refiner.model=self.refiner.code_ids=None;self.refiner=None
            self.pull_press_reviewer=None;self.overhead_reviewer=None;self.fast_primed=False
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
            try:
                self._load_head()
                if self.info['block']!='geometry':
                    self._video().load()
                    if self.info.get('overhead_fast_review') and not self.fast_primed:
                        self._fast_model().review([np.zeros((252,448,3),np.uint8) for _ in range(8)],np.arange(8)*400)
                        self.fast_primed=True;self.fast_warmup_forwards+=1
            finally:
                # 首次加载可能超过空闲时限；空闲从工作结束计算，不能刚载入就卸载。
                if self.info['block']!='geometry':self._arm_idle()

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
            try:
                facts,box=observations(arrays,timestamps[0],timestamps[-1]);detail=None
                if arrays.get('inference_mode')=='realtime' and self.info.get('overhead_fast_review'):
                    fast=self._fast_decision(images,timestamps,arrays,facts)
                    if fast is not None:return fast
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
                # 明确过顶冲突直接用完整跨组复核；已确认时不再重复跑不含下拉的胸部组。
                if self.info.get('pull_press_review',{}).get('execution_version')=='early1':
                    cross=self._cross_review(primary,images,timestamps,facts)
                    if cross.get('cross_review'):return self._with_motion(cross,arrays,timestamps)
                if self.info.get('refinement_enabled'):
                    from .video_refinement import VideoFamilyRefiner,choose_family,combine
                    family=choose_family(primary,prediction)
                    if family in self.info.get('refinement_families',[]) and (primary['action_id'] is not None or not self.info.get('refinement_require_primary',True)):
                        if self.refiner is None:self.refiner=VideoFamilyRefiner(self.video)
                        review=self.refiner.review(images,timestamps,family,facts,detail)
                        return self._with_motion(self._cross_review(combine(primary,review,family),images,timestamps,facts),arrays,timestamps)
                return self._with_motion(self._cross_review(primary,images,timestamps,facts),arrays,timestamps)
            finally:
                if self.info['block']!='geometry':self._arm_idle()

    def _fast_model(self):
        from .overhead_action_review import OverheadActionReviewer
        if self.overhead_reviewer is None:self.overhead_reviewer=OverheadActionReviewer(self._video(),low_latency=True)
        return self.overhead_reviewer

    def _fast_decision(self,images,timestamps,arrays,facts):
        from .overhead_action_review import eligible,accepted_pulldown,version
        hint=arrays.get('recognition_hint')
        if hint not in (None,'lat_pulldown','machine_chest_press','reverse_pec_deck_fly') or not eligible(facts):return None
        policy=self.info.get('motion_evidence') or {}
        motion=motion_evaluate(arrays,int(timestamps[0]),int(timestamps[-1]),'lat_pulldown',policy.get('config'))
        if motion['state']!='moving':return None
        review=self._fast_model().review(images,timestamps)
        if not accepted_pulldown(review):return None
        result={'action_id':'lat_pulldown','candidate_action_id':'lat_pulldown','top_action_id':'lat_pulldown',
            'score':review['score'],'margin':review['margin'],'reason':None,
            'decision_basis':'local_single_visual_overhead_review','score_is_calibrated_probability':False,
            'overhead_fast_review':{'version':version(),'scope':'action_confirmation_only_not_posture_error',
                'score':review['score'],'margin':review['margin'],'source_start_ms':int(timestamps[0]),
                'source_end_ms':int(timestamps[-1]),'visual_forward_passes':1}}
        return self._with_motion(result,arrays,timestamps)

    def _cross_review(self,result,images,timestamps,facts):
        if not self.info.get('pull_press_review'):return result
        from .pull_press_review import route,PullPressReviewer,apply_review
        if not route(result,facts):return result
        if self.pull_press_reviewer is None:self.pull_press_reviewer=PullPressReviewer(self._video())
        review=self.pull_press_reviewer.review(images,timestamps,facts)
        return apply_review(result,review,facts)

    def _with_motion(self,result,arrays,timestamps):
        policy=self.info.get('motion_evidence')
        if not policy:return result
        evidence=motion_evaluate(arrays,int(timestamps[0]),int(timestamps[-1]),result.get('action_id'),policy['config'])
        result=motion_apply(result,evidence)
        result.update(model_version=self.info['version'],base_model_version=self.info.get('base_version'),motion_evidence_applied=True)
        return result
