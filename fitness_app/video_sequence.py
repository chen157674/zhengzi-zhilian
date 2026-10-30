"""连续动作分类特征：保留关节遮挡掩码和时间顺序，不生成显示用的虚构骨架。"""
import numpy as np
from .frame_pipeline import usable_point

JOINTS=(0,11,12,13,14,15,16,23,24,25,26,27,28)
CONFIG={'schema':'video-image-pose-tcn-4','frames':32,'step_ms':100,'minimum_span_ms':1600,'maximum_pose_age_ms':150,
        'maximum_image_age_ms':1250,'minimum_person_coverage':.8,'minimum_image_coverage':.8,
        'minimum_torso_coverage':.6,'image_dimensions':513,'pose_dimensions':55,
        'coordinates':'visible_xyz_scaled_by_image_width_height; shoulder_body_basis_when_visible; explicit_scale_and_basis_flags',
        'depth':'MediaPipe estimated image-landmark z, not measured world distance',
        'missing':'zero_feature_with_explicit_mask_not_a_joint'}


def pose_vector(points,size,person_count=1,accepted=True):
    output=np.zeros((13,4),np.float32);flags=np.zeros(3,np.float32)
    def result(available):return np.concatenate((output.reshape(52),flags)),available
    if person_count!=1 or not accepted or len(points)!=33:return result(False)
    valid=np.asarray([usable_point(p) and p.get('valid') is not False for p in points])
    sides=[(11,23),(12,24)];scales=[]
    width,height=size
    if width<=0 or height<=0:return result(False)
    xy=np.asarray([[p.get('x',np.nan)*width,p.get('y',np.nan)*height,p.get('z',np.nan)*width] for p in points],np.float32)
    for shoulder,hip in sides:
        if valid[shoulder] and valid[hip]:scales.append(np.linalg.norm(xy[shoulder]-xy[hip]))
    shoulders=[xy[i] for i in (11,12) if valid[i]]
    if not shoulders:return result(False)
    if scales:
        scale=float(np.mean(scales));flags[0]=1
    elif len(shoulders)==2:
        scale=float(np.linalg.norm(shoulders[0]-shoulders[1]))*1.5;flags[1]=1
    else:return result(False)
    if scale<max(width,height)*.015:return result(False)
    center=np.mean(shoulders,axis=0)
    basis=np.eye(3,dtype=np.float32)
    hips=[xy[i] for i in (23,24) if valid[i]]
    if len(shoulders)==2 and hips:
        up=center-np.mean(hips,axis=0);up_norm=np.linalg.norm(up)
        if up_norm>1e-5:
            up/=up_norm;lateral=xy[12]-xy[11];lateral-=np.dot(lateral,up)*up;length=np.linalg.norm(lateral)
            if length>max(width,height)*.015:
                lateral/=length;depth=np.cross(lateral,up);basis=np.stack((lateral,up,depth),axis=1);flags[2]=1
    for j,index in enumerate(JOINTS):
        if valid[index]:output[j,:3]=((xy[index]-center)@basis)/scale;output[j,3]=1
    return result(True)


def window(arrays,end_ms):
    """固定过去3.1秒；只取已发生的图像，遮挡不跨帧补关节。"""
    times=np.asarray(arrays['timestamps']);ends=np.searchsorted(times,end_ms,side='right')
    if not ends or end_ms-times[0]<CONFIG['minimum_span_ms']:return None,'sequence_warming_up'
    span=min(3100,end_ms-times[0]);desired=np.linspace(end_ms-span,end_ms,32)
    indices=np.searchsorted(times,desired,side='right')-1
    pose=np.zeros((32,55),np.float32);images=np.zeros((32,513),np.float32);people=[];torso=[]
    if '_image_rows' not in arrays:arrays['_image_rows']=np.flatnonzero(np.isfinite(arrays['features']).all(axis=1))
    image_rows=arrays['_image_rows']
    image_times=times[image_rows]
    for i,(stamp,index) in enumerate(zip(desired,indices)):
        if index<0 or stamp-times[index]>CONFIG['maximum_pose_age_ms']:
            people.append(False);torso.append(False);continue
        single=int(arrays['people'][index])==1
        accepted=bool(arrays['identity'][index]) if 'identity' in arrays else True
        people.append(single and accepted)
        cached=arrays.setdefault('_pose_cache',{})
        if index not in cached:
            raw=arrays['points'][index]
            points=[dict(zip(('x','y','z','visibility','presence'),map(float,p))) for p in raw]
            if 'bounds' in arrays:
                left,top,right,bottom=arrays['bounds'][index]
                for p in points:p['valid']=bool(left<=p['x']<=right and top<=p['y']<=bottom)
            cached[index]=pose_vector(points,arrays['sizes'][index],int(arrays['people'][index]),accepted)
        pose[i],visible=cached[index]
        torso.append(visible)
        image_index=np.searchsorted(image_times,stamp,side='right')-1
        if image_index>=0 and stamp-image_times[image_index]<=CONFIG['maximum_image_age_ms']:
            images[i,:512]=arrays['features'][image_rows[image_index]];images[i,512]=1
    if np.mean(people)<CONFIG['minimum_person_coverage']:return None,'sequence_target_unavailable'
    if np.mean(torso)<CONFIG['minimum_torso_coverage']:return None,'sequence_torso_missing'
    if images[:,512].mean()<CONFIG['minimum_image_coverage']:return None,'sequence_image_missing'
    return (images,pose),None
