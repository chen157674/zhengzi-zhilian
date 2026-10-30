"""机位分类的固定输入；估计深度仅作分类线索，不称为真实三维角度。"""
import numpy as np

VERSION='pose-view-features-1'
NAMES=('shoulder_xy_torso','hip_xy_torso','shoulder_depth_fraction','hip_depth_fraction',
       'shoulder_image_fraction','hip_image_fraction','shoulder_depth_torso','hip_depth_torso',
       'left_torso_depth_fraction','right_torso_depth_fraction','shoulder_hip_width_ratio','lateral_depth_agreement')


def extract(points,width,height,people=1):
    try:
        p=np.asarray(points,np.float64)
        if people!=1 or p.shape!=(33,5) or min(width,height)<=0:return None
        torso=p[[11,12,23,24]]
        if not np.isfinite(torso).all() or np.any(torso[:,3:]<.5) or np.any(torso[:,:2]<0) or np.any(torso[:,:2]>1):return None
        xyz=p[:,:3]*np.asarray((width,height,width),np.float64)
        shoulder=xyz[12]-xyz[11];hip=xyz[24]-xyz[23];left=xyz[11]-xyz[23];right=xyz[12]-xyz[24]
        sxy=np.linalg.norm(shoulder[:2]);hxy=np.linalg.norm(hip[:2]);s3=np.linalg.norm(shoulder);h3=np.linalg.norm(hip)
        l3=np.linalg.norm(left);r3=np.linalg.norm(right);txy=(np.linalg.norm(left[:2])+np.linalg.norm(right[:2]))/2;t3=(l3+r3)/2
        if min(s3,h3,l3,r3,txy,t3)<1:return None
        result=np.asarray((sxy/txy,hxy/txy,abs(shoulder[2])/s3,abs(hip[2])/h3,sxy/s3,hxy/h3,
            abs(shoulder[2])/t3,abs(hip[2])/t3,abs(left[2])/l3,abs(right[2])/r3,sxy/max(hxy,1),
            abs(abs(shoulder[2])/s3-abs(hip[2])/h3)),np.float32)
        return result if np.isfinite(result).all() else None
    except (ValueError,TypeError,IndexError):return None
