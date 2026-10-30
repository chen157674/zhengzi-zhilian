"""动作类别复核的同侧二维运动特征；不判断动作正误、发力或真实关节角。"""
import hashlib,json,math
import numpy as np

VERSION='action-motion-consistency-1'
FAMILIES={'standing_dumbbell_curl':'elbow_flexion','hammer_curl':'elbow_flexion','dumbbell_front_raise':'arm_elevation'}
FEATURES=('elbow_fraction',)


def training_family(row):
    actions=row.get('label_scope',{}).get('allowed_actions') or []
    values={FAMILIES.get(a) for a in actions}
    return next(iter(values)) if actions and len(values)==1 and None not in values else None


def extract(arrays,start_ms,end_ms,config):
    base={'state':'insufficient','reason':'motion_geometry_missing','sides':[],'scope':'image-motion category consistency only; not technique or muscle validation'}
    try:
        t=np.asarray(arrays['timestamps']);p=np.asarray(arrays['points'],np.float64);sizes=np.asarray(arrays['sizes'],np.float64);people=np.asarray(arrays['people'])
        if t.ndim!=1 or p.shape!=(len(t),33,5) or sizes.shape!=(len(t),2) or people.shape!=(len(t),):return base|{'reason':'geometry_shape'}
        selected=(t>=start_ms)&(t<=end_ms);t=t[selected];p=p[selected];sizes=sizes[selected];people=people[selected];n=len(t)
        if n<config['minimum_frames'] or not np.isfinite(t).all() or np.any(np.diff(t)<=0) or np.max(np.diff(t))>config['maximum_gap_ms'] or t[-1]-t[0]<config['minimum_span_ms']:return base|{'reason':'geometry_time'}
        if np.any(people!=1):return base|{'reason':'geometry_target_ambiguous'}
        if not np.isfinite(sizes).all() or np.any(sizes<=0):return base|{'reason':'geometry_dimensions'}
        valid=np.isfinite(p[:,:,(0,1,3,4)]).all(-1)&(p[:,:,3]>=config['minimum_visibility'])&(p[:,:,4]>=config['minimum_visibility'])
        valid&=(p[:,:,0]>=0)&(p[:,:,0]<=1)&(p[:,:,1]>=0)&(p[:,:,1]<=1)
        xy=p[:,:,:2]*sizes[:,None,:];needed=max(config['minimum_frames'],math.ceil(n*config['minimum_coverage']));sides=[]
        for side in (0,1):
            sh,el,wr,hip=[i+side for i in (11,13,15,23)];common=valid[:,sh]&valid[:,el]&valid[:,wr]&valid[:,hip]
            def angle(a,b,c):
                u=xy[:,a]-xy[:,b];v=xy[:,c]-xy[:,b];norm=np.linalg.norm(u,axis=1)*np.linalg.norm(v,axis=1)
                good=common&(norm>1e-6);values=np.full(n,np.nan);values[good]=np.degrees(np.arccos(np.clip(np.sum(u[good]*v[good],axis=1)/norm[good],-1,1)))
                return values
            elbow=angle(sh,el,wr);arm=angle(el,sh,hip);same=np.isfinite(elbow)&np.isfinite(arm)
            if same.sum()<needed:continue
            er=float(np.percentile(elbow[same],90)-np.percentile(elbow[same],10));ar=float(np.percentile(arm[same],90)-np.percentile(arm[same],10))
            if max(er,ar)<2*config['angle_degrees']:continue
            sides.append({'side':'left' if side==0 else 'right','frames':int(same.sum()),'elbow_range':er,'upper_arm_range':ar,'unit':'image_degrees','elbow_fraction':er/(er+ar)})
        return base|{'state':'assessable','reason':'same_side_motion_available','sides':sides} if sides else base|{'reason':'same_side_visible_excursion_insufficient'}
    except (KeyError,TypeError,ValueError,IndexError,OverflowError):return base|{'reason':'geometry_invalid'}


def policy_signature(policy):
    return hashlib.sha256(json.dumps({k:v for k,v in policy.items() if k!='signature'},sort_keys=True).encode()).hexdigest()


def support_bounds(records):
    """训练支持域的保守边界；重复同一观测不会改变边界，重叠时拒绝拟合。"""
    values={family:[] for family in ('arm_elevation','elbow_flexion')}
    for record in records:
        if record['geometry']['state']=='assessable':values[record['family']].extend(side['elbow_fraction'] for side in record['geometry']['sides'])
    if not all(values.values()):return None
    low=max(values['arm_elevation']);high=min(values['elbow_flexion'])
    return {'arm_elevation_max':low,'elbow_flexion_min':high} if low<high else None


def family_prediction(features,policy):
    if features.get('state')!='assessable' or not features.get('sides'):return None
    bounds=policy.get('bounds')
    if bounds is None:return None
    low,high=bounds['arm_elevation_max'],bounds['elbow_flexion_min']
    if not all(type(v) in (int,float) and math.isfinite(v) for v in (low,high)) or not 0<=low<high<=1:raise ValueError('类别运动支持边界无效')
    decisions=[]
    for side in features['sides']:
        value=side.get('elbow_fraction')
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:return None
        if value<=low:decisions.append('arm_elevation')
        elif value>=high:decisions.append('elbow_flexion')
        else:return None
    return {'family':decisions[0],'fractions':[s['elbow_fraction'] for s in features['sides']],'bounds':bounds} if len(set(decisions))==1 else None


def apply_consistency(base,candidate,features,policy,*,research=False):
    if not research and not policy.get('runtime_enabled'):return dict(candidate)
    if policy.get('version')!=VERSION or policy.get('signature')!=policy_signature(policy):raise ValueError('类别运动策略摘要不一致')
    old_family=FAMILIES.get(base.get('action_id'));new_family=FAMILIES.get(candidate.get('action_id'))
    if not old_family or not new_family or old_family==new_family:return dict(candidate)
    trace={'version':VERSION,'policy_signature':policy['signature'],'scope':'exercise category only; no grip leaf or posture-error inference'}
    if not policy.get('calibration_accepted'):return dict(candidate,classification_consistency=trace|{'state':'unaccepted_policy'})
    prediction=family_prediction(features,policy)
    if prediction is None:return dict(candidate,classification_consistency=trace|{'state':'abstained','reason':features.get('reason')})
    trace.update(observed_family=prediction['family'],observed_fractions=prediction['fractions'],training_support_bounds=prediction['bounds'],is_error_or_grip_judgment=False)
    if prediction['family']==old_family and prediction['family']!=new_family:
        return dict(base,model_version=base.get('model_version','unknown')+'-consistency-'+policy['signature'][:10],runtime_enabled=False,
            classification_consistency=trace|{'state':'proposal_vetoed','proposed_action_id':candidate['action_id'],'features':features})
    return dict(candidate,classification_consistency=trace|{'state':'proposal_supported'})
