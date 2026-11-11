"""同帧下肢补充定位，只关联原主体四个躯干锚点，不重新选择训练者。"""
import math
import logging
import threading
import numpy as np
from .paths import ROOT, configure_environment
from .pose import checked_model_hash

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_model = None
_warned = False
ANCHORS = ((5, 11), (6, 12), (11, 23), (12, 24))
MODEL_SHA256 = '869e83fcdffdc7371fa4e34cd8e51c838cc729571d1635e5141e3075e9319dc0'


def matching_target(candidates, source, width, height):
    """返回唯一同侧躯干匹配，多个匹配/锚点缺失时拒判。"""
    if len(source) != 33 or width <= 0 or height <= 0:
        return None, 'source_target_unavailable'
    for _, index in ANCHORS:
        p = source[index]
        if any(not isinstance(p.get(k), (int, float)) or not math.isfinite(p[k])
               for k in ('x', 'y', 'visibility', 'presence')):
            return None, 'source_anchor_unavailable'
        if min(p['visibility'], p['presence']) < .5 or not 0 <= p['x'] <= 1 or not 0 <= p['y'] <= 1:
            return None, 'source_anchor_unavailable'
    matches = []
    for item in candidates:
        points = item.get('keypoints', [])
        if len(points) != 17:
            continue
        distances = []
        for target, origin in ANCHORS:
            p, q = points[target], source[origin]
            if (not p.get('coordinate_valid') or p.get('score', 0) < .6 or
                    any(not isinstance(p.get(k), (int, float)) or not math.isfinite(p[k])
                        for k in ('x', 'y', 'score')) or not 0 <= p['x'] <= 1 or not 0 <= p['y'] <= 1):
                break
            distances.append(math.hypot((p['x'] - q['x']) * width,
                                       (p['y'] - q['y']) * height) / height)
        if len(distances) == 4 and max(distances) <= .06 and sum(distances) / 4 <= .04:
            matches.append(item)
    if len(matches) != 1:
        return None, 'target_ambiguous' if matches else 'target_mismatch'
    return matches[0], None


def observe(rgb, source, stamp):
    """同帧全图保留定位上下文，只返回原主体匹配的展示腿点，不修改判项输入。"""
    global _model, _warned
    h, w = rgb.shape[:2]
    empty = {'status': 'unavailable', 'reason': 'lower_pose_unavailable',
             'timestamp_ms': stamp, 'counting_input_modified': False}
    # 模型可用性检查前先验证原目标锚点；不对无主帧开启独立找人。
    _, reason = matching_target([], source, w, h)
    if reason == 'source_anchor_unavailable' or reason == 'source_target_unavailable':
        return {**empty, 'reason': reason}
    torso = max(source[23]['y'], source[24]['y']) - min(source[11]['y'], source[12]['y'])
    if torso < .1:
        return {**empty, 'reason': 'target_torso_unreliable'}
    # 独立检测保留上下文，显示结果仍须唯一匹配原主体四锚点，绝不选最高分人物。
    x0, x1, y0, y1 = 0, w, 0, h
    if x1-x0 < 32 or y1-y0 < 64:
        return {**empty, 'reason': 'target_crop_unavailable'}
    try:
        configure_environment()  # 必须先于第三方导入，避免其默认C盘配置。
        with _lock:
            if _model is None:
                path = ROOT / 'data/models/yolo11n-pose.pt'
                stat = path.stat()
                if not checked_model_hash(str(path), stat.st_mtime_ns, stat.st_size, MODEL_SHA256):
                    raise ValueError('下肢补充定位权重摘要不符')
                from ultralytics import YOLO
                _model = YOLO(str(path))
            result = _model.predict(np.ascontiguousarray(rgb[y0:y1,x0:x1,::-1]),
                                    device='cpu', verbose=False, save=False)[0]
            candidates = []
            if result.keypoints is not None and result.keypoints.conf is not None:
                for xy, scores in zip(result.keypoints.xy.cpu().numpy(), result.keypoints.conf.cpu().numpy()):
                    points = [{'x': float((p[0]+x0)/w), 'y': float((p[1]+y0)/h),
                               'score': float(score), 'coordinate_valid': bool(np.isfinite(p).all() and np.isfinite(score))}
                              for p, score in zip(xy, scores)]
                    candidates.append({'keypoints': points, 'model_sha256': MODEL_SHA256})
        target, reason = matching_target(candidates, source, w, h)
        if target is None:
            return {**empty, 'reason': reason}
        return {'status': 'observed', 'timestamp_ms': stamp, 'body': target,
                'target_basis': 'unique_same_frame_anatomical_shoulders_and_hips',
                'counting_input_modified': False, 'crop_pixels': [x0,y0,x1,y1]}
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        if not _warned:
            logger.warning('下肢补充定位不可用 type=%s', type(error).__name__)
            _warned = True
        return empty
