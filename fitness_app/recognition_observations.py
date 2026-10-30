"""从真实可见姿态提取动作类别线索；不给语言模型传入目标标签或纠错结论。"""
import math
import numpy as np
from .frame_pipeline import usable_point
from .gym_motion import measurements,quantile,span
from .sessions import IdentityContinuity
from .upstream import calculate_angle

VERSION='visible-motion-observations-2-image-depth'


def observations(arrays,start_ms,end_ms):
    selected=np.flatnonzero((arrays['timestamps']>=start_ms)&(arrays['timestamps']<=end_ms));frames=[];tracker=IdentityContinuity()
    if len(selected)<8:return {},None
    for j,index in enumerate(selected):
        points=[dict(zip(('x','y','z','visibility','presence'),map(float,p))) for p in arrays['points'][index]]
        for p in points:p['valid']=usable_point(p) and min(p['visibility'],p['presence'])>=.7
        width,height=map(int,arrays['sizes'][index]);people=int(arrays['people'][index])
        frame={'captured_ms':int(arrays['timestamps'][index]),'frame_seq':j,'width':width,'height':height,
               'framing':{'person_count':people,'landmarks':points,'code':'VISIBLE' if people==1 else 'TARGET_AMBIGUOUS'}}
        if people!=1 or not tracker.accept(frame):return {},None
        frames.append(frame)
    values=measurements(frames,require_wrist=False)
    result={}
    for key,fn in [('trunk_tilt_from_vertical_deg',lambda r:90-abs(quantile(r,'trunk'))),
                   ('elbow_median_deg',lambda r:quantile(r,'elbow')),('elbow_range_deg',lambda r:span(r,'elbow')),
                   ('upper_arm_to_trunk_median_deg',lambda r:quantile(r,'arm')),('upper_arm_range_deg',lambda r:span(r,'arm')),
                   ('wrist_above_shoulder_min_torso',lambda r:quantile(r,'wrist_up',10)),
                   ('wrist_above_shoulder_max_torso',lambda r:quantile(r,'wrist_up',90))]:
        numbers=[fn(rows) for rows in values['sides'].values()] if values else [];numbers=[v for v in numbers if math.isfinite(v)]
        if numbers:result[key]=round(float(np.mean(numbers)),2)
    # 分类可以观察到肩肘腕而髋被裁掉；缺髋不应抹去可见的肘屈伸。
    for side in (0,1):
        elbows=[];vertical=[];heights=[]
        for f in frames:
            p=f['framing']['landmarks'];size=np.array((f['width'],f['height']))
            if not all(p[i+side]['valid'] for i in (11,13)):continue
            shoulder=np.array((p[11+side]['x'],p[11+side]['y']))*size;elbow=np.array((p[13+side]['x'],p[13+side]['y']))*size
            length=np.linalg.norm(elbow-shoulder)
            if length>1:vertical.append(math.degrees(math.acos(float(np.clip((elbow-shoulder)[1]/length,-1,1)))))
            if not p[15+side]['valid']:continue
            wrist=np.array((p[15+side]['x'],p[15+side]['y']))*size
            try:elbows.append(calculate_angle(shoulder,elbow,wrist))
            except ValueError:continue
            if all(p[i]['valid'] for i in (11,12)):
                scale=np.linalg.norm((np.array((p[11]['x'],p[11]['y']))-np.array((p[12]['x'],p[12]['y'])))*size)*1.5
                if scale>1:heights.append(float((shoulder[1]-wrist[1])/scale))
        if len(elbows)>=8:
            result.setdefault('elbow_median_deg',round(float(np.median(elbows)),2))
            result.setdefault('elbow_range_deg',round(float(np.percentile(elbows,90)-np.percentile(elbows,10)),2))
        if len(vertical)>=8:result.setdefault('upper_arm_from_image_down_deg',round(float(np.median(vertical)),2))
        if len(heights)>=8 and 'wrist_above_shoulder_max_torso' not in result:
            result['wrist_height_min_estimated_torso']=round(float(np.percentile(heights,10)),2)
            result['wrist_height_max_estimated_torso']=round(float(np.percentile(heights,90)),2)
    separation=[];hips=[];torso_scales=[];wrist_xyz=[]
    for index in selected:
        p=arrays['points'][index];size=arrays['sizes'][index];valid=np.isfinite(p).all(1)&(p[:,3]>=.7)&(p[:,4]>=.7)&(p[:,0]>=0)&(p[:,0]<=1)&(p[:,1]>=0)&(p[:,1]<=1)
        xy=p[:,:2]*size
        scales=[np.linalg.norm(xy[s]-xy[h]) for s,h in ((11,23),(12,24)) if valid[s] and valid[h]]
        if not scales:continue
        scale=float(np.mean(scales))
        if scale<1e-5:continue
        torso_scales.append(scale)
        if valid[15] and valid[16]:separation.append(float(np.linalg.norm(xy[15]-xy[16])/scale))
        if valid[23] and valid[24]:hips.append(float((xy[23,1]+xy[24,1])/2))
        if all(valid[i] for i in (11,12,15,16,23,24)):
            # 所有旧/新数据一致使用图像landmark估计深度，避免有无world缓存成为类别捷径。
            world=p[:,:3]*np.array((size[0],size[1],size[0]))
            if not np.isfinite(world[[11,12,15,16,23,24]]).all():continue
            shoulder=(world[11]+world[12])/2;hip=(world[23]+world[24])/2;up=shoulder-hip;length=np.linalg.norm(up)
            if length<1:continue
            up/=length;lateral=world[12]-world[11];lateral-=np.dot(lateral,up)*up;norm=np.linalg.norm(lateral)
            if norm<1:continue
            lateral/=norm;depth=np.cross(lateral,up);basis=np.stack((lateral,up,depth),axis=1)
            wrist_xyz.append(np.asarray([(world[i]-shoulder)@basis/length for i in (15,16)]))
    def extent(v):return float(np.percentile(v,90)-np.percentile(v,10))
    if len(separation)>=8:result['hand_separation_range_torso']=round(extent(separation),2)
    if len(hips)>=8:result['hip_vertical_range_torso']=round(extent(hips)/float(np.median(torso_scales)),2)
    if len(wrist_xyz)>=8:
        v=np.asarray(wrist_xyz)
        result['estimated_body_wrist_lateral_range_torso']=round(float(np.mean([extent(v[:,side,0]) for side in range(2)])),2)
        result['estimated_body_wrist_depth_range_torso']=round(float(np.mean([extent(v[:,side,2]) for side in range(2)])),2)
    # 中部真实可见头/肩/肘/腕/髋的局部，只作补充细节，仍保留原完整视频。
    coordinates=[(p[i]['x'],p[i]['y']) for f in frames for p in [f['framing']['landmarks']]
                 for i in (0,11,12,13,14,15,16,23,24) if p[i]['valid']]
    box=None
    unique={i for f in frames for p in [f['framing']['landmarks']] for i in (0,11,12,13,14,15,16,23,24) if p[i]['valid']}
    if len(unique)>=4:
        xs=[p[0] for p in coordinates];ys=[p[1] for p in coordinates];dx=max(xs)-min(xs);dy=max(ys)-min(ys)
        if dx>.03 and dy>.03:box=[max(0,min(xs)-dx*.3),max(0,min(ys)-dy*.25),min(1,max(xs)+dx*.3),min(1,max(ys)+dy*.25)]
    return result,box


def describe(values):
    names={'trunk_tilt_from_vertical_deg':'躯干偏离竖直的角度(0竖直,90水平)',
           'elbow_median_deg':'肘角中位数(180伸直)','elbow_range_deg':'肘角变化幅度',
           'upper_arm_to_trunk_median_deg':'上臂与躯干夹角中位数(0下垂,180过顶)','upper_arm_range_deg':'上臂角变化幅度',
           'wrist_above_shoulder_min_torso':'腕高于肩最低值(躯干长单位,负数低于肩)',
           'wrist_above_shoulder_max_torso':'腕高于肩最高值(躯干长单位)',
           'hand_separation_range_torso':'双腕间距变化(躯干长单位)','hip_vertical_range_torso':'髋上下移动幅度(躯干长单位)',
           'estimated_body_wrist_lateral_range_torso':'估计身体坐标中腕左右移动幅度',
           'estimated_body_wrist_depth_range_torso':'估计身体坐标中腕前后移动幅度'}
    names.update(upper_arm_from_image_down_deg='上臂相对图像向下竖直夹角(0下垂,180向上)',
                 wrist_height_min_estimated_torso='缺髋时按1.5倍可见肩宽估计单位的腕相对肩最低高度',
                 wrist_height_max_estimated_torso='缺髋时按1.5倍可见肩宽估计单位的腕相对肩最高高度')
    return '可见关节辅助观测，包含投影与估计误差，仅用于动作类别参考，不是正确性或真人三维测量：\n'+'\n'.join(names[k]+'='+str(v) for k,v in values.items()) if values else ''
