"""参考动作识别：独立于姿态纠错；未知、静止与不完整窗口不硬猜。"""
from collections import deque
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from .paths import ROOT
from .frame_pipeline import usable_point,framing,REQUIRED
from .upstream import calculate_angle

CORE_ACTIONS=('bodyweight_squat','standard_push_up','standing_dumbbell_curl','conventional_deadlift')
ACTIONS=CORE_ACTIONS+('reverse_lunge','forearm_plank','jumping_jack','barbell_bent_over_row','barbell_overhead_press')
from .gym_catalogue import ACTION_IDS as GYM_ACTIONS
# 原十二动作的冻结基线；新增动作使用独立视觉资源，不改变旧训练标签空间。
TARGET_ACTIONS=tuple(action for action in REQUIRED if action not in GYM_ACTIONS)
CONFIG={'schema':'exercise-window-visible-features-3','window_ms':2000,'step_ms':1000,'coverage':.7,'minimum_frames':8,
        'maximum_gap_ms':500,'motion_degrees':12.,'minimum_score':.8,'minimum_margin':.2,
        'trees':160,'max_depth':14,'min_samples_leaf':2,'seed':20261018,
        'minimum_feature_columns':4,'feature_columns':8,'weights':'equal_class_then_equal_clip',
        'missing':'training_median_with_explicit_visibility_flags',
        'motion_columns':[0,1,2,4,7],'static_support_pose':True}
MODEL=ROOT/'data/models/action-recognition.joblib'
MANIFEST=ROOT/'data/models/action-recognition.json'


def body_frame(result,stamp,width,height,seq):
    # 只复用共同身体可见性检查，不运行深蹲计数器或预先指定动作结果。
    return {'captured_ms':stamp,'frame_seq':seq,'width':width,'height':height,
            'world_landmarks':result['world_poses'][0] if len(result.get('world_poses',[]))==1 and len(result.get('poses',[]))==1 else [],
            'framing':framing('bodyweight_squat',result)}


def side_values(frame,side):
    q=frame.get('framing',{});p=q.get('landmarks',[]);s=0 if side=='left' else 1
    if q.get('person_count')!=1 or q.get('code')=='TARGET_AMBIGUOUS' or frame.get('identity_accepted') is False or len(p)!=33:return None
    w,h=frame['width'],frame['height']
    if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in (w,h)):return None
    def point(i):
        value=p[i+s]
        return np.array([value['x']*w,value['y']*h]) if usable_point(value) and value.get('valid') is not False else None
    shoulder,elbow,wrist,hip,knee,ankle=[point(i) for i in (11,13,15,23,25,27)]
    if shoulder is None or hip is None:return None
    scale=float(np.linalg.norm(shoulder-hip))
    if scale<1e-6:return None
    def angle(a,b,c):
        if a is None or b is None or c is None:return float('nan')
        try:return calculate_angle(a,b,c)
        except ValueError:return float('nan')
    return [angle(shoulder,elbow,wrist),angle(shoulder,hip,knee),angle(hip,knee,ankle),
            angle(shoulder,hip,hip+np.array([0.,-1.])),angle(elbow,shoulder,hip),
            float((shoulder[1]-wrist[1])/scale) if wrist is not None else float('nan'),
            float((ankle[1]-hip[1])/scale) if ankle is not None else float('nan'),
            angle(elbow,wrist,wrist+np.array([0.,-1.]) if wrist is not None else None)]


def prepare_frame(frame):
    return {**frame,'_recognition_values':{side:side_values(frame,side) for side in ('left','right')}}


def window_features(rows):
    if not rows:return None,'warming_up'
    times=[r['captured_ms'] for r in rows]
    if any(b<=a for a,b in zip(times,times[1:])):raise ValueError('动作窗口时间必须递增')
    if times[-1]-times[0]<1800:return None,'warming_up'
    prepared=[r if '_recognition_values' in r else prepare_frame(r) for r in rows]
    candidates=[];had_gap=False
    for side in ('left','right'):
        values=np.asarray([r['_recognition_values'][side] if r['_recognition_values'][side] is not None else [np.nan]*8 for r in prepared])
        statistics=np.full((8,5),np.nan);visible=np.zeros(8,dtype=bool);ranges=np.zeros(8)
        for col in range(8):
            valid=np.isfinite(values[:,col]);stamps=np.asarray(times)[valid]
            if valid.sum()<CONFIG['minimum_frames'] or valid.mean()<CONFIG['coverage']:continue
            if max(stamps[0]-times[0],times[-1]-stamps[-1],np.diff(stamps).max())>CONFIG['maximum_gap_ms']:
                had_gap=True;continue
            v=values[valid,col];quantiles=np.percentile(v,[10,50,90])
            statistics[col]=[v.mean(),v.std(),*quantiles];visible[col]=True;ranges[col]=quantiles[-1]-quantiles[0]
        if visible[3] and visible.sum()>=CONFIG['minimum_feature_columns']:
            candidates.append((int(visible.sum()),statistics,visible,ranges))
    if not candidates:return None,'pose_gap' if had_gap else 'insufficient_pose'
    def support_pose(candidate):
        _,statistics,visible,_=candidate
        return visible[3] and visible[4] and visible[7] and statistics[3,3]>60 and 45<=statistics[4,3]<=135 and 60<=statistics[7,3]<=120
    moving=[candidate for candidate in candidates if np.max(candidate[3][CONFIG['motion_columns']])>=CONFIG['motion_degrees'] or support_pose(candidate)]
    if not moving:return None,'stationary'
    # 分侧动作可能只有另一侧在运动，不能因左侧可见就把右侧弯举判成静止。
    _,statistics,visible,ranges=max(moving,key=lambda c:c[0])
    feature=np.concatenate((statistics.ravel(),visible.astype(float)))
    return feature,None


def decision(model,feature):
    scores=model.predict_proba(np.asarray([feature],float))[0];order=np.argsort(scores)[::-1]
    score=float(scores[order[0]]);margin=score-float(scores[order[1]])
    action=str(model.classes_[order[0]]) if score>=CONFIG['minimum_score'] and margin>=CONFIG['minimum_margin'] else None
    return {'candidate_action_id':action,'score':score,'margin':margin,'reason':None if action else 'uncertain_action'}


class ActionRecognizer:
    def __init__(self):
        from .sessions import IdentityContinuity
        self.identity=IdentityContinuity()
        self.rows=deque(maxlen=240);self.model=None;self.version=None;self.reason='model_unavailable'
        self.last_seq=None;self.last_stamp=None;self.last_evaluated=-10000
        self.pending=None;self.pending_since=None;self.confirmed=None;self.latest={}
        if MODEL.is_file() and MANIFEST.is_file():
            manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
            if manifest.get('config')!=CONFIG or hashlib.sha256(MODEL.read_bytes()).hexdigest()!=manifest.get('sha256'):
                self.reason='model_identity_mismatch';return
            if not manifest.get('development_gate_passed'):self.reason='model_validation_pending';return
            import joblib
            self.model=joblib.load(MODEL);self.version=manifest['version'];self.reason=None

    def reset(self):
        self.identity.reset()
        self.rows.clear();self.pending=None;self.pending_since=None;self.confirmed=None;self.latest={};self.last_evaluated=-10000

    def process(self,frame):
        stamp,seq=frame['captured_ms'],frame['frame_seq']
        if self.last_seq is not None and (seq<=self.last_seq or stamp<=self.last_stamp):
            # 旧包不能带着上一次已确认动作继续分析，也不能破坏当前流内部状态。
            return {**self.snapshot('stale_frame'),'state':'searching','action_id':None,
                    'candidate_action_id':None,'score':None,'message':'等待新的画面'}
        if self.last_stamp is not None and stamp-self.last_stamp>500:self.reset()
        self.last_seq,self.last_stamp=seq,stamp
        if frame['framing'].get('person_count')!=1 or frame['framing'].get('code')=='TARGET_AMBIGUOUS':
            self.reset();return self.snapshot('target_unavailable')
        if frame.get('identity_accepted') is False or not self.identity.accept(frame):
            self.reset();return self.snapshot('target_changed')
        self.rows.append(prepare_frame(frame))
        while self.rows and stamp-self.rows[0]['captured_ms']>CONFIG['window_ms']:self.rows.popleft()
        if self.model is None:return self.snapshot(self.reason)
        if stamp-self.last_evaluated<250:return self.snapshot()
        self.last_evaluated=stamp;feature,reason=window_features(list(self.rows))
        self.latest=decision(self.model,feature) if feature is not None else {'candidate_action_id':None,'reason':reason,'score':None,'margin':None}
        candidate=self.latest['candidate_action_id']
        if candidate!=self.pending:
            self.pending=candidate;self.pending_since=stamp;self.confirmed=None
        if candidate is None:self.confirmed=None
        elif stamp-self.pending_since>=750:self.confirmed=candidate
        return self.snapshot()

    def snapshot(self,reason=None):
        state='recognized' if self.confirmed else 'confirming' if self.pending else 'searching'
        message='已识别动作' if self.confirmed else '正在确认动作，请保持自然运动' if self.pending else '正在观察动作'
        if self.model is None:message='自动识别暂不可用，可以使用手动选择动作'
        return {'mode':'auto','state':state,'available':self.model is not None,'message':message,
                'action_id':self.confirmed,'candidate_action_id':self.pending,'reason':reason or self.latest.get('reason') or self.reason,
                'score':self.latest.get('score'),'score_is_calibrated_probability':False,'model_version':self.version,
                'supported_actions':list(self.model.classes_) if self.model is not None else [],
                'target_actions':list(TARGET_ACTIONS),
                'untrained_actions':[action for action in TARGET_ACTIONS if action not in ACTIONS],
                'candidate_actions':list(ACTIONS),'evidence_level':'reference','manual_fallback_available':True}
