"""分类后的运动证据保护：低幅度/准备不当作已在运动，缺失不当作静止，平板例外。"""
import hashlib,json,math
from pathlib import Path
import numpy as np
from .paths import project_path

VERSION='visible-motion-evidence-1'
DEFAULT={'angle_degrees':8.,'displacement_torso':.12,'minimum_frames':8,'minimum_span_ms':1200,
         'maximum_gap_ms':500,'minimum_visibility':.7,'minimum_coverage':.7,'minimum_signal_families':2}
LOWER={'bodyweight_squat','reverse_lunge','bodyweight_glute_bridge','bent_knee_sit_up','high_knees','conventional_deadlift'}
MIXED={'standard_push_up','jumping_jack'}


class MotionPolicyError(ValueError):pass


def read_policy(info):
    """发布策略须匹配代码、配置、基础权重和已记录评估；不读任意外部路径。"""
    policy=info.get('motion_evidence')
    if policy is None:
        if info.get('base_version'):raise MotionPolicyError('运动保护版本缺少策略')
        return None
    try:
        config=policy['config']
        if not isinstance(config,dict) or set(config)!=set(DEFAULT):raise ValueError('config')
        for key in ('angle_degrees','displacement_torso','minimum_visibility','minimum_coverage'):
            if type(config[key]) not in (int,float) or not math.isfinite(config[key]) or config[key]<=0:raise ValueError(key)
        if config['angle_degrees']>45 or config['displacement_torso']>2 or config['minimum_visibility']>1 or config['minimum_coverage']>1:raise ValueError('range')
        for key in ('minimum_frames','minimum_span_ms','maximum_gap_ms','minimum_signal_families'):
            if type(config[key]) is not int or config[key]<1:raise ValueError(key)
        if config['minimum_frames']<8 or config['minimum_span_ms']<800 or config['maximum_gap_ms']>1000 or config['minimum_signal_families']>8:raise ValueError('temporal')
        signature=policy_signature(config)
        if policy['version']!=VERSION or policy['signature']!=signature or policy['base_weight_sha256']!=info['sha256']:raise ValueError('identity')
        if info['version']!=info['base_version']+'-motion-'+signature[:10]:raise ValueError('model version')
        path=project_path(policy['evaluation_path']);raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=policy['evaluation_sha256']:raise ValueError('evaluation hash')
        result=json.loads(raw)
        if result['base_model_version']!=info['base_version'] or result['policy_signature']!=signature or result['selected']['config']!=config or not result['safe_nonregression_candidate'] or result['unreviewed_correct_vetoes']:
            raise ValueError('evaluation')
        return policy
    except (KeyError,ValueError,TypeError,OSError) as error:raise MotionPolicyError('运动保护策略/评估身份不一致') from error


def policy_signature(config=None):
    value={'version':VERSION,'config':config or DEFAULT,'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def evaluate(arrays,start_ms,end_ms,action,config=None):
    """仅返回观察保护状态，不是动作分类器/运动正确性/肌肉发力判断。"""
    cfg={**DEFAULT,**(config or {})}
    base={'version':VERSION,'state':'insufficient','reason':'motion_evidence_missing','veto':False,
          'scope':'visible_kinematic_change_not_exercise_correctness','signals':{},'frames':0}
    if action is None:return {**base,'reason':'no_accepted_action'}
    if action=='forearm_plank':return {**base,'state':'static_exempt','reason':'static_supported_exercise'}
    required={'timestamps','points','sizes','people'}
    if not isinstance(arrays,dict) or not required.issubset(arrays):return base
    try:
        t=np.asarray(arrays['timestamps']);p=np.asarray(arrays['points'],np.float64);sizes=np.asarray(arrays['sizes'],np.float64);people=np.asarray(arrays['people'])
        n=len(t)
        if p.shape!=(n,33,5) or sizes.shape!=(n,2) or people.shape!=(n,) or t.ndim!=1:return {**base,'reason':'motion_array_shape'}
        selected=np.flatnonzero((t>=start_ms)&(t<=end_ms));t=t[selected];p=p[selected];sizes=sizes[selected];people=people[selected]
        base['frames']=len(t)
        if len(t)<cfg['minimum_frames']:return {**base,'reason':'motion_frames_insufficient'}
        if not np.isfinite(t).all() or not np.all(np.diff(t)>0) or np.max(np.diff(t))>cfg['maximum_gap_ms']:return {**base,'reason':'motion_source_gap'}
        if t[-1]-t[0]<cfg['minimum_span_ms']:return {**base,'reason':'motion_span_insufficient'}
        if not np.all(people==1):return {**base,'reason':'motion_target_ambiguous'}
        if not np.isfinite(sizes).all() or np.min(sizes)<=0:return {**base,'reason':'motion_dimensions_invalid'}
        valid=np.isfinite(p[:,:,(0,1,3,4)]).all(-1)&(p[:,:,3]>=cfg['minimum_visibility'])&(p[:,:,4]>=cfg['minimum_visibility'])
        valid&=(p[:,:,0]>=0)&(p[:,:,0]<=1)&(p[:,:,1]>=0)&(p[:,:,1]<=1)
        xy=p[:,:,:2]*sizes[:,None,:]
        signals={};needed=max(cfg['minimum_frames'],math.ceil(len(t)*cfg['minimum_coverage']));bridge_hold=False
        def add(name,values,unit):
            values=np.asarray(values);values=values[np.isfinite(values)]
            if len(values)<needed:return
            extent=float(np.percentile(values,90)-np.percentile(values,10))
            previous=signals.get(name)
            if previous is None or extent>previous['range']:signals[name]={'range':extent,'unit':unit,'frames':len(values)}
        def angle(a,b,c):
            good=valid[:,a]&valid[:,b]&valid[:,c];u=xy[:,a]-xy[:,b];v=xy[:,c]-xy[:,b]
            norm=np.linalg.norm(u,axis=1)*np.linalg.norm(v,axis=1);good&=norm>1e-6
            result=np.full(len(t),np.nan);result[good]=np.degrees(np.arccos(np.clip(np.sum(u[good]*v[good],axis=1)/norm[good],-1,1)))
            return result
        upper=action not in LOWER or action in MIXED;lower=action in LOWER or action in MIXED
        for side in (0,1):
            sh,el,wr,hip,knee,ankle=(i+side for i in (11,13,15,23,25,27))
            scale=np.linalg.norm(xy[:,sh]-xy[:,hip],axis=1);torso=valid[:,sh]&valid[:,hip]&(scale>1)
            if upper:
                add('elbow',angle(sh,el,wr),'image_degrees');add('upper_arm',angle(el,sh,hip),'image_degrees')
                for axis in (0,1):
                    good=torso&valid[:,wr];values=np.full(len(t),np.nan);values[good]=(xy[good,wr,axis]-xy[good,sh,axis])/scale[good]
                    add('wrist_'+str(axis),values,'torso_ratio')
            if lower:
                hip_angles=angle(sh,hip,knee);knee_angles=angle(hip,knee,ankle)
                add('hip',hip_angles,'image_degrees');add('knee',knee_angles,'image_degrees')
                if action=='bodyweight_glute_bridge':
                    line=xy[:,knee]-xy[:,sh];length=np.linalg.norm(line,axis=1);delta=xy[:,hip]-xy[:,sh]
                    good=torso&valid[:,knee]&valid[:,ankle]&(length>1)
                    distance=np.full(len(t),np.nan)
                    distance[good]=np.abs(line[good,0]*delta[good,1]-line[good,1]*delta[good,0])/length[good]/scale[good]
                    held=good&(hip_angles>=145)&(knee_angles>=40)&(knee_angles<=145)&(distance<=.15)
                    bridge_hold=bridge_hold or np.count_nonzero(held)>=needed
                values=np.full(len(t),np.nan);vector=xy[:,sh]-xy[:,hip]
                values[torso]=np.degrees(np.arctan2(vector[torso,0],-vector[torso,1]));add('trunk',values,'image_degrees')
                for axis in (0,1):
                    good=torso&valid[:,ankle];values=np.full(len(t),np.nan);values[good]=(xy[good,ankle,axis]-xy[good,hip,axis])/scale[good]
                    add('ankle_'+str(axis),values,'torso_ratio')
        base['signals']=signals
        if bridge_hold:return {**base,'state':'hold_supported','reason':'visible_bridge_top_hold','veto':False,
                               'pose_hold_is_not_technique_validation':True}
        moving=[name for name,metric in signals.items() if metric['range']>=cfg['angle_degrees' if metric['unit']=='image_degrees' else 'displacement_torso']]
        if moving:return {**base,'state':'moving','reason':'visible_motion_supported','moving_signals':moving}
        families={name.split('_')[0] if name.startswith(('wrist_','ankle_')) else name for name in signals}
        if len(families)<cfg['minimum_signal_families']:return {**base,'reason':'motion_parts_insufficient'}
        return {**base,'state':'unconfirmed','reason':'motion_unconfirmed','veto':True,'moving_signals':[]}
    except (TypeError,ValueError,IndexError,OverflowError):return {**base,'reason':'motion_arrays_invalid'}


def apply(decision,evidence):
    result={**decision,'motion_evidence':evidence}
    if decision.get('action_id') and evidence['veto']:
        result.update(action_id=None,candidate_action_id=None,reason='motion_unconfirmed',
                      decision_basis=decision.get('decision_basis','trained_classifier')+'_motion_not_confirmed')
    return result
