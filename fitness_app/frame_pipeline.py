"""统一帧解码和姿态有效性；不把取景可用性当作动作正确性。"""
from io import BytesIO
import math
import warnings

from .auth import APIError
from .content_bounds import validate_content_bounds,point_in_content,BOUNDS_VERSION

MAX_JPEG_BYTES=2*1024*1024
MODEL_VERSION='mediapipe-full-float16-1'
FRAME_SCHEMA_VERSION=1
VISIBILITY_THRESHOLD=0.5

# 侧面允许一条完整近侧链；分侧／正面动作仍需对应双侧证据。
SIDE_BODY=[[11,23,25,27],[12,24,26,28]]
SIDE_ARMS=[[11,13,15,23,25,27],[12,14,16,24,26,28]]
BOTH_BODY=[[11,12,23,24,25,26,27,28]]
REQUIRED={
    'bodyweight_squat':SIDE_BODY,'standard_push_up':SIDE_ARMS,'reverse_lunge':BOTH_BODY,
    'bodyweight_glute_bridge':SIDE_BODY,'bent_knee_sit_up':SIDE_BODY,'forearm_plank':SIDE_ARMS,
    'jumping_jack':[[11,12,13,14,15,16,23,24,27,28]],'high_knees':[[11,12,23,24,25,27],[11,12,23,24,26,28]],
    'standing_dumbbell_curl':[[11,13,15,23],[12,14,16,24]],
    'barbell_overhead_press':[[11,13,15,23,25,27,31],[12,14,16,24,26,28,32]],
    'barbell_bent_over_row':[[11,13,15,23,25,27,31],[12,14,16,24,26,28,32]],'conventional_deadlift':SIDE_ARMS,
}
MESSAGES={'NO_PERSON':'未检测到人体，请全身入镜','TARGET_AMBIGUOUS':'人体目标不唯一，请确认画面内只有一人并调整取景',
    'MISSING_PARTS':'必要部位不可见，请调整取景','VISIBLE':'必要部位关键点已检出，正在检查取景稳定性'}

from .gym_catalogue import REQUIRED as GYM_REQUIRED
REQUIRED.update(GYM_REQUIRED)


def jpeg_dimensions(jpeg):
    if not isinstance(jpeg,bytes) or not 0<len(jpeg)<=MAX_JPEG_BYTES:
        raise APIError('FRAME_TOO_LARGE','JPEG 单帧最多 2 MiB',413)
    from PIL import Image,UnidentifiedImageError
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(jpeg)) as image:
                if image.format!='JPEG':raise APIError('INVALID_FRAME','仅接受实际 JPEG 图片',422)
                width,height=image.size
                if min(width,height)<1 or max(width,height)>1920 or min(width,height)>1080:
                    raise APIError('FRAME_DIMENSIONS','请提交长边1920、短边1080以内的画面',422)
                if image.getexif().get(274,1)!=1:raise APIError('FRAME_ORIENTATION','请提交已转正的图像',422)
                image.verify()
    except (Image.DecompressionBombError,Image.DecompressionBombWarning) as error:raise APIError('FRAME_DIMENSIONS','JPEG 尺寸超出上限',422) from error
    except (UnidentifiedImageError,OSError,ValueError) as error:raise APIError('INVALID_FRAME','JPEG 内容无效',422) from error
    return width,height


def decode_jpeg(jpeg,dimensions=None):
    """先查实际头部尺寸，再由与离线视频相同的 PyAV 输出 RGB。"""
    import av
    import numpy as np
    expected=dimensions or jpeg_dimensions(jpeg)
    try:
        with av.open(BytesIO(jpeg),format='mjpeg') as container:
            frame=next(container.decode(video=0))
            if (frame.width,frame.height)!=expected:raise ValueError('decoded dimensions')
            return np.ascontiguousarray(frame.to_ndarray(format='rgb24'))
    except (av.FFmpegError,StopIteration,ValueError) as error:raise APIError('INVALID_FRAME','请重新提交JPEG画面',422) from error


def usable_point(point):
    keys=('x','y','z','visibility','presence')
    if not isinstance(point,dict) or any(not isinstance(point.get(key),(int,float)) or isinstance(point[key],bool) or not math.isfinite(point[key]) for key in keys):return False
    return 0<=point['x']<=1 and 0<=point['y']<=1 and min(point['visibility'],point['presence'])>=VISIBILITY_THRESHOLD


def framing(action_id,result):
    poses=result.get('poses',[])
    code='NO_PERSON' if not poses else 'TARGET_AMBIGUOUS' if len(poses)!=1 else None
    landmarks=poses[0] if len(poses)==1 else []
    bounds=validate_content_bounds(result.get('content_bounds',[0,0,1,1]))
    in_content=[point_in_content(point,bounds) if usable_point(point) else False for point in landmarks] if len(landmarks)==33 else [False]*33
    valid=[usable_point(point) and in_content[index] for index,point in enumerate(landmarks)] if len(landmarks)==33 else [False]*33
    outside=[index for index,point in enumerate(landmarks) if usable_point(point) and not in_content[index]] if len(landmarks)==33 else []
    if code is None:code='VISIBLE' if any(all(valid[index] for index in group) for group in REQUIRED[action_id]) else 'MISSING_PARTS'
    # 不返回歧义人物的骨架，也不把不可见点补零或沿用上一帧。
    points=[{**point,'valid':True} if valid[index] else {'valid':False} for index,point in enumerate(landmarks)] if len(landmarks)==33 else []
    return {'code':code,'message':MESSAGES[code],'usable':code=='VISIBLE','person_count':len(poses),'landmarks':points,
            'visibility_threshold':VISIBILITY_THRESHOLD,'coordinate_space':'image_normalized','schema_version':FRAME_SCHEMA_VERSION,
            'content_bounds':list(bounds),'content_bounds_version':BOUNDS_VERSION,'outside_content_indices':outside}


def analysis_framing(action_id,result):
    """在原计数取景之外开放肩推局部观察，不改冻结计数的取景实现。"""
    quality=framing(action_id,result)
    if action_id=='machine_chest_press':quality['scene_reference']=result.get('scene_reference') or {'status':'unavailable'}
    if action_id!='barbell_overhead_press':return quality
    counting_usable=quality['usable'];p=quality['landmarks']
    local=quality['person_count']==1 and len(p)==33 and all(p[i].get('valid') for i in (11,12,13,14,15,16,23,24))
    if local and not counting_usable:
        quality.update(usable=True,code='VISIBLE',message='肩肘腕及髋部可见，仅可观察上肢姿态')
    quality.update(counting_usable=counting_usable,posture_scope='upper_body' if local and not counting_usable else 'action_required_parts')
    return quality
