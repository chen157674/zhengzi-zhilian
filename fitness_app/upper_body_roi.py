"""离线细节表示：同一窗口固定上肢裁剪，缺少可靠单人姿态则保留原画面。"""
import numpy as np

VERSION='upper-body-context-roi-1'
REQUIRED=(11,12,13,14,15,16)


def upper_body_roi(arrays,times):
    stamps=np.asarray(arrays['timestamps']);points=np.asarray(arrays['points']);people=np.asarray(arrays['people'])
    if stamps.ndim!=1 or not len(stamps) or points.shape!=(len(stamps),33,5) or people.shape!=(len(stamps),):raise ValueError('上肢裁剪姿态维度无效')
    if len(times)!=8 or np.any(np.diff(stamps)<=0):raise ValueError('上肢裁剪需要8个实际帧和递增姿态时间')
    bounds=[];used=[]
    for stamp in times:
        index=int(np.abs(stamps-stamp).argmin())
        if abs(int(stamps[index])-int(stamp))>80 or people[index]!=1:continue
        joints=points[index];arms=joints[list(REQUIRED)]
        if not np.isfinite(arms).all() or np.any(arms[:,3:]<.6) or np.any(arms[:,:2]<0) or np.any(arms[:,:2]>1):continue
        extra=joints[[0,23,24]];extra=extra[np.isfinite(extra).all(axis=1)&(extra[:,3:]>=.6).all(axis=1)&(extra[:,:2]>=0).all(axis=1)&(extra[:,:2]<=1).all(axis=1)]
        visible=np.concatenate([arms,extra]);bounds.append(visible[:,:2]);used.append(int(stamps[index]))
    result={'version':VERSION,'crop':None,'valid_samples':len(bounds),'pose_times':used,'fallback':'insufficient_visible_single_person_arms'}
    if len(bounds)!=8:return result
    coordinates=np.concatenate(bounds);left,top=coordinates.min(0);right,bottom=coordinates.max(0)
    width=float(right-left);height=float(bottom-top)
    if width<.05 or height<.05:return result|{'fallback':'degenerate_visible_extent'}
    crop=[max(0.,float(left)-max(.08,width*.2)),max(0.,float(top)-max(.06,height*.15)),
          min(1.,float(right)+max(.08,width*.2)),min(1.,float(bottom)+max(.06,height*.12))]
    if (crop[2]-crop[0])*(crop[3]-crop[1])>=.85:return result|{'fallback':'near_full_frame'}
    return result|{'crop':crop,'fallback':None}


def crop_images(images,crop):
    if crop is None:return images
    if len(crop)!=4 or not all(np.isfinite(crop)) or not 0<=crop[0]<crop[2]<=1 or not 0<=crop[1]<crop[3]<=1:raise ValueError('上肢裁剪范围无效')
    output=[]
    for rgb in images:
        h,w=rgb.shape[:2];l,t,r,b=crop;value=np.ascontiguousarray(rgb[round(t*h):round(b*h),round(l*w):round(r*w)])
        if min(value.shape[:2])<2:raise ValueError('上肢裁剪没有足够实际像素')
        output.append(value)
    return output
