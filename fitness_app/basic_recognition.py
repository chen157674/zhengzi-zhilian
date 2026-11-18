"""基础自动识别发布策略：真实图像分类＋运动证据，保留未通过完整验收的事实。"""
from collections import deque
import json
import hashlib
import logging
import numpy as np
from .paths import ROOT
from .pose import checked_model_hash
from .automatic_recognition import CORE_ACTIONS,ACTIONS,TARGET_ACTIONS,prepare_frame,window_features,decision
from .visual_recognition import DIRECTORY,preprocess,normalize
from .sessions import IdentityContinuity
from .motion_recognition import motion_candidate,VERSION as MOTION_VERSION
from .gym_recognition import resources as gym_resources,trained_resources,candidate as gym_candidate,implementation_version,CONFIG as GYM_CONFIG
from .gym_catalogue import ACTION_IDS as GYM_ACTIONS
from .temporal_gym import TemporalGym

POLICY=ROOT/'data/models/automatic-basic.json'
logger=logging.getLogger(__name__)


def checked(path,digest):
    stat=path.stat()
    return checked_model_hash(str(path),stat.st_mtime_ns,stat.st_size,digest)


def resources():
    try:
        policy=json.loads(POLICY.read_text(encoding='utf-8'))
        visual=json.loads((ROOT/'data/models/visual-action-classifier.json').read_text(encoding='utf-8'))
        pose=json.loads((ROOT/'data/models/action-recognition.json').read_text(encoding='utf-8'))
        backbone=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
        if policy.get('mode')!='basic_reference' or not policy.get('enabled'):return None,'basic_mode_disabled'
        if policy.get('motion_version')!=MOTION_VERSION:return None,'motion_policy_mismatch'
        if (visual['sha256']!=policy['visual_sha256'] or pose['sha256']!=policy['pose_sha256'] or
            backbone['revision']!=policy['backbone_revision'] or set(visual['actions'])!=set(ACTIONS) or
            set(pose['actions'])!=set(ACTIONS)):return None,'model_identity_mismatch'
        for path,digest in ((ROOT/'data/models/visual-action-classifier.joblib',visual['sha256']),
                            (ROOT/'data/models/action-recognition.joblib',pose['sha256'])):
            if not checked(path,digest):return None,'model_identity_mismatch'
        for name in ('onnx/vision_model_quantized.onnx','preprocessor_config.json'):
            if not checked(DIRECTORY/name,backbone['files'][name]['sha256']):return None,'model_identity_mismatch'
        return {**policy,'version':policy['version']+'-'+visual['version']},None
    except (OSError,ValueError,KeyError,TypeError):return None,'model_unavailable'


class BasicRecognizer:
    def __init__(self):
        self.policy,self.reason=resources();self.available=self.policy is not None
        self.version=self.policy['version'] if self.policy else None
        self.gym,self.gym_reason=gym_resources()
        self.gym_head,self.gym_head_reason=trained_resources()
        self.temporal=TemporalGym()
        if self.version and self.gym:self.version+='-'+self.gym['version']
        if self.version and self.gym_head:self.version+='-'+self.gym_head['version']
        if self.version and self.temporal.enabled:self.version+='-'+self.temporal.resource['version']
        if self.version and self.gym:
            self.version='basic-extended-1-'+hashlib.sha256((self.version+implementation_version()).encode()).hexdigest()[:24]
        self.model=None;self.pose_model=None;self.encoder=None;self.preprocessor=None
        self.rows=deque(maxlen=240);self.images=deque(maxlen=5);self.identity=IdentityContinuity()
        self.last_seq=None;self.last_stamp=None;self.last_evaluated=-10000;self.last_image_stamp=-10000
        self.pending=None;self.pending_since=None;self.confirmed=None;self.latest={}

    def _load(self):
        if self.encoder is not None:return
        import joblib
        import onnxruntime as ort
        self.model=joblib.load(ROOT/'data/models/visual-action-classifier.joblib')
        self.pose_model=joblib.load(ROOT/'data/models/action-recognition.joblib')
        if set(self.model.classes_)!=set(ACTIONS) or set(self.pose_model.classes_)!=set(ACTIONS):raise ValueError('模型类别不一致')
        self.preprocessor=json.loads((DIRECTORY/'preprocessor_config.json').read_text(encoding='utf-8'))
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        self.encoder=ort.InferenceSession(str(DIRECTORY/'onnx/vision_model_quantized.onnx'),options,providers=['CPUExecutionProvider'])

    def extract(self,rgb,stamp):
        """只在预览工作线程调用；不持管理锁，不保留RGB，每500ms至多提取一次。"""
        if not self.available or stamp-self.last_image_stamp<500:return None
        try:
            self._load()
            feature=normalize(self.encoder.run(['image_embeds'],{'pixel_values':preprocess(rgb,self.preprocessor)[None]})[0])[0]
            if feature.shape!=(512,) or not np.isfinite(feature).all():raise ValueError('图像特征无效')
            self.last_image_stamp=stamp;return feature
        except (OSError,ValueError,RuntimeError,ImportError) as error:
            logger.warning('自动图像识别不可用 type=%s',type(error).__name__)
            self.available=False;self.reason='visual_model_unavailable'
            self.encoder=self.model=self.pose_model=None
            return None

    def reset(self):
        self.rows.clear();self.images.clear();self.identity.reset()
        self.pending=None;self.pending_since=None;self.confirmed=None;self.latest={};self.last_evaluated=-10000

    def process(self,frame,feature=None):
        stamp,seq=frame['captured_ms'],frame['frame_seq']
        if self.last_seq is not None and (seq<=self.last_seq or stamp<=self.last_stamp):
            return {**self.snapshot('stale_frame'),'state':'searching','action_id':None,'candidate_action_id':None,'score':None}
        if self.last_stamp is not None and stamp-self.last_stamp>500:self.reset()
        self.last_seq,self.last_stamp=seq,stamp
        if frame['framing'].get('person_count')!=1 or frame['framing'].get('code')=='TARGET_AMBIGUOUS':
            self.reset();return self.snapshot('target_unavailable')
        if frame.get('identity_accepted') is False or not self.identity.accept(frame):
            self.reset();return self.snapshot('target_changed')
        self.rows.append(prepare_frame(frame))
        while self.rows and stamp-self.rows[0]['captured_ms']>4000:self.rows.popleft()
        if feature is not None:
            feature=np.asarray(feature,dtype=np.float32)
            if feature.shape!=(512,) or not np.isfinite(feature).all():
                self.reset();return self.snapshot('invalid_visual_feature')
            self.images.append((stamp,feature))
        if not self.available:return self.snapshot(self.reason)
        if stamp-self.last_evaluated<250:return self.snapshot()
        self.last_evaluated=stamp
        motion=motion_candidate(list(self.rows));motion_action=motion['action_id']
        pose_feature,reason=window_features([r for r in self.rows if stamp-r['captured_ms']<=2000])
        guess={'candidate_action_id':None,'reason':reason,'score':None};raw_visual_action=None
        if pose_feature is not None:
            if self.model is None or len(self.images)<3 or self.images[-1][0]-self.images[0][0]<1000:
                guess['reason']='warming_up_visual'
            elif stamp-self.images[-1][0]>750:guess['reason']='waiting_for_visual'
            else:
                guess=decision(self.model,normalize(np.mean([v for _,v in self.images],axis=0)))
                visual_action=guess['candidate_action_id'];raw_visual_action=visual_action;pose_guess=decision(self.pose_model,pose_feature)
                pose_action=pose_guess['candidate_action_id']
                if visual_action and pose_action and pose_action!=visual_action:
                    guess.update(candidate_action_id=None,reason='models_disagree')
        visual_action=guess['candidate_action_id']
        if motion_action:
            # 九类模型没有后三类的标签空间，不能用它们的错误映射否决新增运动模式。
            if motion_action not in ACTIONS or raw_visual_action is None or raw_visual_action==motion_action:
                guess={'candidate_action_id':motion_action,'reason':None,'score':guess.get('score') if visual_action==motion_action else None,
                       'decision_basis':'temporal_pose_rules' if visual_action!=motion_action else 'visual_and_motion',
                       'motion_evidence':motion.get('evidence'),'motion_version':MOTION_VERSION}
            else:guess.update(candidate_action_id=None,reason='visual_motion_conflict')
        elif visual_action:guess['decision_basis']='trained_visual_with_motion_gate'
        if guess['candidate_action_id'] is None:guess['motion_reason']=motion['reason']
        expanded=gym_candidate(self.gym,self.images,list(self.rows),stamp,self.gym_head)
        if expanded is not None:guess=expanded
        if guess['candidate_action_id'] is None and self.temporal.enabled:
            try:fused=self.temporal.refine(list(self.rows),self.images,self.gym_head,self.gym)
            except (OSError,ValueError,RuntimeError,ImportError) as error:
                logger.warning('补充时序模型不可用 type=%s',type(error).__name__)
                self.temporal.enabled=False;self.temporal.reason='temporal_model_failure';fused=None
            if fused is not None:guess=fused
        self.latest=guess;candidate=guess['candidate_action_id']
        if candidate!=self.pending:self.pending=candidate;self.pending_since=stamp;self.confirmed=None
        if candidate is None:self.confirmed=None
        elif stamp-self.pending_since>=(GYM_CONFIG['confirm_ms'] if candidate in GYM_ACTIONS else 750):self.confirmed=candidate
        return self.snapshot()

    def snapshot(self,reason=None):
        supported=list(TARGET_ACTIONS)+(list(GYM_ACTIONS) if self.gym else [])
        trained=list(ACTIONS)+(self.gym_head['new_actions'] if self.gym_head else [])
        state='recognized' if self.confirmed else 'confirming' if self.pending else 'searching'
        message='已识别动作' if self.confirmed else '正在确认动作，请保持自然运动' if self.pending else '正在观察动作'
        current_reason=reason or self.latest.get('reason') or self.reason
        if not self.confirmed and not self.pending:
            message={'uncertain_gym_variant':'动作变式尚不确定，可手动指定动作',
                     'gym_motion_unconfirmed':'正在确认连续动作，请保持自然运动',
                     'gym_visual_unstable':'动作画面尚不稳定，请保持器械与身体入镜',
                     'background_or_unsupported_variant':'请选择训练动作，或保持清楚取景继续识别'}.get(current_reason,message)
        if not self.available:message='自动识别暂不可用，可使用手动备用'
        return {'mode':'auto','release_mode':'basic_reference','state':state,'available':self.available,'message':message,
                'action_id':self.confirmed,'candidate_action_id':self.pending,'reason':reason or self.latest.get('reason') or self.reason,
                'score':self.latest.get('score'),'score_is_calibrated_probability':False,'model_version':self.version,
                'supported_actions':supported if self.available else [],'basic_actions':list(TARGET_ACTIONS),
                'limited_actions':list(GYM_ACTIONS), 'trained_actions':trained,'base_trained_actions':list(ACTIONS),'rule_actions':list(TARGET_ACTIONS),
                'zero_shot_actions':list(GYM_ACTIONS) if self.gym else [],'gym_resource_reason':self.gym_reason,
                'development_trained_actions':self.gym_head['new_actions'] if self.gym_head else [],
                'gym_trained_head_reason':self.gym_head_reason,
                'temporal_reference_available':self.temporal.resource is not None,'temporal_reference_enabled':self.temporal.enabled,
                'target_actions':list(TARGET_ACTIONS)+list(GYM_ACTIONS),'untrained_actions':[a for a in list(TARGET_ACTIONS)+list(GYM_ACTIONS) if a not in trained],
                'candidate_actions':supported,'evidence_level':'reference','manual_fallback_available':True,
                'visual_similarity':self.latest.get('visual_similarity'),'visual_margin':self.latest.get('visual_margin'),
                'decision_basis':self.latest.get('decision_basis'),'motion_evidence':self.latest.get('motion_evidence'),
                'motion_version':MOTION_VERSION,'full_validation_passed':False,'full_twelve_action_support':self.available}
