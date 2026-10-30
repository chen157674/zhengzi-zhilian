"""十二动作的连续姿态分类补充；判断运动模式，不评判质量或肌肉用力。"""
import math
import numpy as np
from .frame_pipeline import usable_point
from .upstream import calculate_angle

VERSION='twelve-motion-patterns-1'


def motion_candidate(frames):
    if len(frames)<8 or frames[-1]['captured_ms']-frames[0]['captured_ms']<1200:
        return {'action_id':None,'reason':'warming_up_motion','matches':[]}
    times=np.asarray([f['captured_ms'] for f in frames])
    if np.any(np.diff(times)<=0) or np.max(np.diff(times))>500:
        return {'action_id':None,'reason':'motion_gap','matches':[]}
    if len(frames)>64:
        indices=np.linspace(0,len(frames)-1,64).astype(int)
        frames=[frames[i] for i in indices];times=times[indices]
    xy=np.full((len(frames),33,2),np.nan)
    for row,frame in enumerate(frames):
        q=frame.get('framing',{});points=q.get('landmarks',[])
        if q.get('person_count')!=1 or q.get('code')=='TARGET_AMBIGUOUS' or frame.get('identity_accepted') is False or len(points)!=33:continue
        for i,p in enumerate(points):
            if isinstance(p,dict) and p.get('valid') is not False and usable_point(p):xy[row,i]=[p['x']*frame['width'],p['y']*frame['height']]
    def series(indices,fn):
        result=np.full(len(frames),np.nan)
        for row in range(len(frames)):
            if np.isfinite(xy[row,list(indices)]).all():
                try:result[row]=fn(xy[row])
                except (ValueError,ZeroDivisionError):pass
        return result
    def usable(v):
        good=np.isfinite(v);stamps=times[good]
        return good.sum()>=8 and good.mean()>=.7 and max(stamps[0]-times[0],times[-1]-stamps[-1],np.diff(stamps).max())<=500
    quantiles={}
    def q(v,p=50):
        key=id(v)
        if key not in quantiles:quantiles[key]=(v,np.percentile(v[np.isfinite(v)],[10,50,90]) if usable(v) else [np.nan]*3)
        return float(quantiles[key][1][{10:0,50:1,90:2}[p]])
    def span(v):return q(v,90)-q(v,10)
    def bent_and_returned(v):
        if not usable(v):return False
        values=v[np.isfinite(v)]
        # 一次屈肘后重新伸展，排除从站立下降到静态前臂支撑的单向进入过程。
        return any(max(values[:i])-values[i]>18 and max(values[i+1:])-values[i]>18 for i in range(1,len(values)-1))
    def angle(a,b,c):return series((a,b,c),lambda p:calculate_angle(p[a],p[b],p[c]))
    scales=[series((11+s,23+s),lambda p,s=s:float(np.linalg.norm(p[11+s]-p[23+s]))) for s in (0,1)]
    scale=max((q(v) for v in scales if usable(v)),default=0)
    if not math.isfinite(scale) or scale<=1e-6:return {'action_id':None,'reason':'insufficient_motion_pose','matches':[]}
    matches=set();evidence={}
    def match(action,measurements):matches.add(action);evidence[action]=measurements
    # 同一侧的真实关节链，不把左右肢体拼成一条完整链。
    for s in (0,1):
        shoulder,elbow,wrist,hip,knee,ankle=(i+s for i in (11,13,15,23,25,27))
        elevation=series((shoulder,hip),lambda p:math.degrees(math.atan2(p[hip,1]-p[shoulder,1],abs(p[hip,0]-p[shoulder,0]))))
        knee_angle=angle(hip,knee,ankle);hip_angle=angle(shoulder,hip,knee);elbow_angle=angle(shoulder,elbow,wrist)
        arm_angle=angle(elbow,shoulder,hip)
        knee_up=series((hip,knee),lambda p:(p[hip,1]-p[knee,1])/scale)
        hip_ground=series((hip,ankle),lambda p:(p[ankle,1]-p[hip,1])/scale)
        shoulder_ground=series((shoulder,ankle),lambda p:(p[ankle,1]-p[shoulder,1])/scale)
        wrist_up=series((shoulder,wrist),lambda p:(p[shoulder,1]-p[wrist,1])/scale)
        wrist_below_hip=series((hip,wrist),lambda p:(p[wrist,1]-p[hip,1])/scale)
        hand_ground=series((wrist,ankle),lambda p:abs(p[wrist,1]-p[ankle,1])/scale)
        side='left' if s==0 else 'right'
        current=elevation[-max(4,int(len(frames)*.2)):]
        current_elevation=float(np.nanmedian(current)) if np.isfinite(current).any() else float('nan')
        bent_supine=(35<q(knee_angle)<155 and q(knee_up,90)>.25 and q(hip_ground,10)<.5 and q(elevation,10)<25)
        if bent_supine and current_elevation<65:
            if q(elevation,10)<-12 and q(elevation,90)<25 and span(hip_ground)>.15 and span(shoulder_ground)<.2:
                match('bodyweight_glute_bridge',{'side':side,'hip_lift_range':span(hip_ground),'shoulder_lift_range':span(shoulder_ground)})
            if q(elevation,90)>35 and span(elevation)>30 and span(hip_ground)<.22:
                match('bent_knee_sit_up',{'side':side,'trunk_rise_degrees':span(elevation),'hip_lift_range':span(hip_ground)})
        prone=(q(knee_angle)>145 and q(hip_angle)>140 and -10<q(elevation)<40 and q(knee_up)<0 and q(wrist_up)<-.25 and q(hand_ground)<.55)
        if prone and current_elevation<45:
            if span(elbow_angle)>20 and bent_and_returned(elbow_angle):
                match('standard_push_up',{'side':side,'elbow_motion_degrees':span(elbow_angle)})
            elif 50<q(elbow_angle)<130 and span(elevation)<12 and span(hip_ground)<.15:
                match('forearm_plank',{'side':side,'elbow_degrees':q(elbow_angle),'trunk_motion_degrees':span(elevation)})
        upright=q(elevation)>55 and current_elevation>50
        legs_quiet=not usable(knee_angle) or (span(knee_angle)<20 and span(hip_ground)<.2)
        if upright and legs_quiet and span(elbow_angle)>25 and q(arm_angle)<40 and span(arm_angle)<25 and q(wrist_up,90)<.5:
            match('standing_dumbbell_curl',{'side':side,'elbow_motion_degrees':span(elbow_angle),'upper_arm_degrees':q(arm_angle)})
        if upright and q(wrist_up,90)>.5 and q(arm_angle,90)>110 and q(elbow_angle,10)<150 and span(arm_angle)>35:
            match('barbell_overhead_press',{'side':side,'wrist_above_shoulder':q(wrist_up,90),'upper_arm_motion':span(arm_angle)})
        if 10<q(elevation)<65 and current_elevation<70 and q(hip_ground)>.6 and q(hand_ground)>.55 and span(elbow_angle)>20 and (span(elevation)<45 or span(elbow_angle)>1.2*span(elevation)):
            match('barbell_bent_over_row',{'side':side,'trunk_elevation':q(elevation),'elbow_motion_degrees':span(elbow_angle)})
        if q(elevation,10)<55 and q(elevation,90)>65 and span(elevation)>25 and span(hip_angle)>25 and q(elbow_angle,10)>145 and q(wrist_below_hip)>.15:
            if span(knee_angle)<45 or span(hip_angle)>1.4*span(knee_angle):
                match('conventional_deadlift',{'side':side,'hip_motion_degrees':span(hip_angle),'knee_motion_degrees':span(knee_angle)})
    # 双侧关系用于区分深蹲、后撤、原地交替抬腿与双脚开合。
    left_knee=angle(23,25,27);right_knee=angle(24,26,28)
    feet_width=series((27,28),lambda p:abs(p[27,0]-p[28,0])/scale)
    hip_y=series((23,24,27,28),lambda p:((p[27,1]+p[28,1])-(p[23,1]+p[24,1]))/(2*scale))
    trunk=series((11,12,23,24),lambda p:math.degrees(math.atan2((p[23,1]+p[24,1])-(p[11,1]+p[12,1]),abs((p[23,0]+p[24,0])-(p[11,0]+p[12,0])))))
    knees=[series((23+s,25+s),lambda p,s=s:(p[23+s,1]-p[25+s,1])/scale) for s in (0,1)]
    foot_rise=[series((23+s,27+s),lambda p,s=s:(p[23+s,1]-p[27+s,1])/scale) for s in (0,1)]
    retreat=[]
    for s in (0,1):
        def backward(p,s=s):
            facing=p[31+s,0]-p[29+s,0]
            if abs(facing)<.04*scale:return float('nan')
            return math.copysign(1,facing)*(p[27+s,0]-p[23+s,0])/scale
        retreat.append(series((23+s,27+s,29+s,31+s),backward))
    if q(trunk)>55:
        if all(span(v)>.25 and q(v,90)>-.45 for v in knees) and all(span(v)>.15 for v in foot_rise) and span(hip_y)<.6:
            difference=knees[0]-knees[1]
            travel=series((23,24),lambda p:(p[23,0]+p[24,0])/(2*scale))
            if q(difference,10)<-.15 and q(difference,90)>.15 and span(travel)<.35 and span(feet_width)<.45:
                match('high_knees',{'alternating_knee_separation':span(difference),'horizontal_travel':span(travel)})
        upper=[angle(13+s,11+s,23+s) for s in (0,1)]
        elbows=[angle(11+s,13+s,15+s) for s in (0,1)]
        if all(span(v)>55 and q(v,90)>100 for v in upper) and all(q(v,90)>140 for v in elbows) and span(feet_width)>.45:
            match('jumping_jack',{'leg_opening_range':span(feet_width),'arm_opening_degrees':min(span(v) for v in upper)})
        if span(feet_width)>.45 and span(hip_y)>.15 and min(q(left_knee,10),q(right_knee,10))<145 and any(q(v,10)<-.3 and span(v)>.4 for v in retreat):
            match('reverse_lunge',{'foot_separation_range':span(feet_width),'body_lowering_range':span(hip_y)})
        if min(span(left_knee),span(right_knee))>25 and q(abs(left_knee-right_knee))<20 and span(feet_width)<.3 and min(q(left_knee,10),q(right_knee,10))<145:
            match('bodyweight_squat',{'knee_motion_degrees':min(span(left_knee),span(right_knee))})
    if 'conventional_deadlift' in matches:matches.discard('bodyweight_squat')
    if 'jumping_jack' in matches:matches.discard('barbell_overhead_press');matches.discard('reverse_lunge')
    if len(matches)!=1:return {'action_id':None,'reason':'ambiguous_motion' if matches else 'unmatched_motion','matches':sorted(matches)}
    action=next(iter(matches))
    return {'action_id':action,'reason':None,'matches':[action],'evidence':evidence[action],'rule_version':VERSION}
