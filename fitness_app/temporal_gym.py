"""MIT SkelGym时序模型的受控适配；只能与画面证据共同确定器械变式。"""
from functools import lru_cache
import hashlib,json
import numpy as np
from .paths import ROOT
from .pose import checked_model_hash

DIRECTORY=ROOT/'data/models/skelgym'
JOINTS=(0,11,12,13,14,15,16,23,24,25,26,27,28)
EXPECTED_LABELS=('barbell biceps curl','bench press','chest fly machine','deadlift','decline bench press','hammer curl',
                 'hip thrust','incline bench press','lat pulldown','lateral raise','leg extension','leg raises','plank',
                 'pull Up','push-up','romanian deadlift','russian twist','shoulder press','squat','t bar row','tricep Pushdown','tricep dips')
GROUPS={
    'bench press':('barbell_bench_press','dumbbell_bench_press'),
    'incline bench press':('incline_dumbbell_press',),
    'chest fly machine':('pec_deck_fly',),
    'hammer curl':('hammer_curl',),
    'lat pulldown':('lat_pulldown',),
    'lateral raise':('dumbbell_lateral_raise',),
    'pull Up':('pull_up','assisted_pull_up'),
    'shoulder press':('barbell_overhead_press','dumbbell_shoulder_press'),
    'tricep Pushdown':('cable_triceps_pushdown',),
}
CONFIG={'schema':'skelgym-visual-agreement-1','frames':32,'minimum_observations':16,'minimum_span_ms':1000,'maximum_span_ms':4000,
        'maximum_gap_ms':500,'minimum_core_visibility':.8,'minimum_pose_score':.8,'minimum_pose_margin':.2,
        'minimum_joint_score':.8,'minimum_joint_margin':.2,'minimum_visual_support':.5}


@lru_cache(maxsize=2)
def _resource(modified,size):
    manifest=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'));info=manifest['onnx']
    path=ROOT/info['path'];path.resolve().relative_to(DIRECTORY.resolve());stat=path.stat()
    if info['input_shape']!=[1,32,39] or tuple(info['labels'])!=EXPECTED_LABELS or not checked_model_hash(str(path),stat.st_mtime_ns,stat.st_size,info['sha256']):raise ValueError('时序模型契约或摘要不符')
    return {**info,'absolute_path':str(path),'version':'skelgym-world-'+info['sha256'][:12],
            'runtime_enabled':manifest.get('runtime_enabled',False)}


def resources():
    try:
        stat=(DIRECTORY/'manifest.json').stat();return _resource(stat.st_mtime_ns,stat.st_size),None
    except (OSError,ValueError,KeyError,TypeError):return None,'temporal_model_unavailable'


def sequence(frames):
    if len(frames)<CONFIG['minimum_observations']:return None,'temporal_warming_up'
    frames=frames[-32:];times=np.array([f['captured_ms'] for f in frames]);gaps=np.diff(times)
    if np.any(gaps<=0) or gaps.max()>CONFIG['maximum_gap_ms']:return None,'temporal_gap'
    if not CONFIG['minimum_span_ms']<=times[-1]-times[0]<=CONFIG['maximum_span_ms']:return None,'temporal_duration'
    rows=[];core=[]
    for f in frames:
        quality=f.get('framing',{});points=f.get('world_landmarks',[]);visible=quality.get('landmarks',[])
        if quality.get('person_count')!=1 or quality.get('code')=='TARGET_AMBIGUOUS' or f.get('identity_accepted') is False or len(points)!=33 or len(visible)!=33:return None,'temporal_target_unavailable'
        try:row=np.asarray([[points[i][key] for key in ('x','y','z')] for i in JOINTS],dtype=np.float32)
        except (KeyError,TypeError,ValueError):return None,'temporal_world_missing'
        if not np.isfinite(row).all():return None,'temporal_world_missing'
        rows.append(row.reshape(39));core.append([visible[i].get('valid',False) for i in (11,12,23,24)])
    if np.mean(core)<CONFIG['minimum_core_visibility']:return None,'temporal_core_missing'
    values=np.asarray(rows,np.float32)
    if len(values)<32:
        # 与上游短窗口的align_corners=False一致，只重采样分类特征，不生成可显示或纠错的关节。
        positions=(np.arange(32)+.5)*len(values)/32-.5
        values=np.stack([np.interp(positions,np.arange(len(values)),values[:,i]) for i in range(39)],axis=1).astype(np.float32)
    return values[None],None


class TemporalGym:
    def __init__(self):
        self.resource,self.reason=resources();self.session=None
        self.enabled=bool(self.resource and self.resource['runtime_enabled'])
    def predict(self,frames):
        if self.resource is None:return {'available':False,'reason':self.reason}
        values,reason=sequence(frames)
        if values is None:return {'available':True,'reason':reason}
        if self.session is None:
            import onnxruntime as ort
            options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
            self.session=ort.InferenceSession(self.resource['absolute_path'],options,providers=['CPUExecutionProvider'])
        scores=self.session.run(['probabilities'],{'world_sequence':values})[0][0]
        if scores.shape!=(22,) or not np.isfinite(scores).all():raise ValueError('时序模型输出无效')
        order=np.argsort(scores)[::-1]
        return {'available':True,'reason':None,'label':self.resource['labels'][int(order[0])],
                'score':float(scores[order[0]]),'margin':float(scores[order[0]]-scores[order[1]]),
                'model_version':self.resource['version'],'score_is_calibrated_probability':False}

    def refine(self,frames,images,head,text_resource):
        from .gym_recognition import load_head,motion_evidence,CONFIG as VISUAL_CONFIG
        from .gym_motion import span
        from .visual_recognition import normalize
        stamp=frames[-1]['captured_ms'] if frames else 0
        recent=[(t,v) for t,v in images if 0<=stamp-t<=2000]
        if len(recent)<3 or recent[-1][0]-recent[0][0]<1000 or stamp-recent[-1][0]>750:return None
        temporal=self.predict(frames)
        if temporal.get('reason') or temporal.get('score',0)<CONFIG['minimum_pose_score'] or temporal.get('margin',0)<CONFIG['minimum_pose_margin']:return None
        mean=normalize(np.mean([v for _,v in recent],axis=0));fused=None
        allowed=GROUPS.get(temporal.get('label'),())
        if not allowed:return None
        if head:
            classifier=load_head(head['sha256']);scores=classifier.predict_proba(mean[None])[0]
            visual=dict(zip(map(str,classifier.classes_),map(float,scores)));top=max(visual,key=visual.get)
            if top not in allowed and visual[top]>=head['config']['minimum_score']:return None
            fused=agreement(temporal,visual)
            if fused:
                votes=classifier.predict(np.asarray([v for _,v in recent])).tolist()
                if votes.count(fused['candidate_action_id'])<max(2,len(votes)-1):fused=None
        if fused is None and text_resource:
            scores=text_resource['features']@mean;order=np.argsort(scores)[::-1];top=text_resource['labels'][int(order[0])]
            if top in allowed and scores[order[0]]>=VISUAL_CONFIG['minimum_cosine'] and scores[order[0]]-scores[order[1]]>=VISUAL_CONFIG['minimum_margin']:
                votes=[text_resource['labels'][int(np.argmax(text_resource['features']@v))] for _,v in recent]
                if votes.count(top)>=max(2,len(votes)-1):
                    fused={'candidate_action_id':top,'score':None,'reason':None,'decision_basis':'zero_shot_visual_and_temporal_agreement',
                           'temporal_label':temporal['label'],'temporal_score':temporal['score'],'joint_score_is_probability':False}
        if fused:
            evidence=motion_evidence(fused['candidate_action_id'],frames)
            if not evidence or not any(span(r,'elbow')>=12 or span(r,'arm')>=12 for r in evidence['sides'].values()):return None
            fused['motion_evidence']={'basis':'observed_visible_joint_motion_and_learned_world_sequence','temporal_version':temporal['model_version']}
        return fused


def agreement(temporal,visual_scores):
    """不把未知器械硬映射为上游已有类；不归一化低分以制造高置信。"""
    if temporal.get('reason') or temporal.get('score',0)<CONFIG['minimum_pose_score'] or temporal.get('margin',0)<CONFIG['minimum_pose_margin']:return None
    allowed=GROUPS.get(temporal.get('label'),())
    # 一个画面强烈支持别的动作时，不让闭集姿态标签覆盖它。
    if not visual_scores:return None
    ordered=sorted(visual_scores,key=visual_scores.get,reverse=True)
    if ordered[0] not in allowed or visual_scores[ordered[0]]<CONFIG['minimum_visual_support']:return None
    action=ordered[0];score=float(np.sqrt(temporal['score']*visual_scores[action]))
    runner=float(np.sqrt(temporal['score']*max((v for a,v in visual_scores.items() if a!=action),default=0)))
    if score<CONFIG['minimum_joint_score'] or score-runner<CONFIG['minimum_joint_margin']:return None
    return {'candidate_action_id':action,'score':score,'reason':None,'decision_basis':'trained_visual_and_temporal_agreement',
            'temporal_label':temporal['label'],'temporal_score':temporal['score'],'joint_score_is_probability':False}
