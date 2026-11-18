"""本地手部输入候选；关键点存在不代表手指可见或握法正确。"""
import json
import math
from pathlib import Path

from .paths import ROOT, project_path
from .pose import checked_model_hash


def correction_resource(name):
    manifest = project_path(ROOT/'data/models/correction-resources.json')
    if not manifest.is_file():
        raise FileNotFoundError('请配置纠错输入资源')
    record = json.loads(manifest.read_text(encoding='utf-8'))['resources'][name]
    path = project_path(record['path'])
    stat = path.stat()
    if stat.st_size != record['bytes'] or not checked_model_hash(str(path), stat.st_mtime_ns, stat.st_size, record['sha256']):
        raise ValueError('纠错输入资源校验失败')
    return path, record


class HandPoseService:
    def __init__(self, video=False):
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        path, self.resource = correction_resource('hands')
        self.mp = mp
        self.video = video
        self.last_stamp = -1
        options = vision.HandLandmarkerOptions(base_options=python.BaseOptions(model_asset_buffer=path.read_bytes()),
            running_mode=vision.RunningMode.VIDEO if video else vision.RunningMode.IMAGE,
            num_hands=2, min_hand_detection_confidence=.5, min_hand_presence_confidence=.5,
            min_tracking_confidence=.5)
        self.landmarker = vision.HandLandmarker.create_from_options(options)

    def detect(self, rgb, timestamp_ms=0, crop_pixels=None):
        import numpy as np
        if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError('手部输入必须为RGB uint8图像')
        height, width = rgb.shape[:2]
        crop = crop_pixels or (0, 0, width, height)
        if len(crop) != 4 or any(type(v) is not int for v in crop):
            raise ValueError('裁剪必须为整数像素范围')
        left, top, right, bottom = crop
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError('手部裁剪超出原图')
        if type(timestamp_ms) is not int or timestamp_ms < 0 or self.video and timestamp_ms <= self.last_stamp:
            raise ValueError('手部源时间必须有效且递增')
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB,
                              data=np.ascontiguousarray(rgb[top:bottom, left:right]))
        result = self.landmarker.detect_for_video(image, timestamp_ms) if self.video else self.landmarker.detect(image)
        self.last_stamp = timestamp_ms
        hands = []
        for landmarks, categories in zip(result.hand_landmarks, result.handedness):
            category = max(categories, key=lambda item: item.score)
            points = []
            for point in landmarks:
                x = (left+point.x*(right-left))/width
                y = (top+point.y*(bottom-top))/height
                valid = all(math.isfinite(v) for v in (x,y,point.z)) and 0 <= x <= 1 and 0 <= y <= 1
                points.append({'x': x, 'y': y, 'z': point.z*(right-left)/width,
                               'coordinate_valid': valid})
            hands.append({'handedness_candidate': category.category_name, 'handedness_score': category.score,
                          'landmarks': points, 'finger_visibility_verified': False})
        return {'schema_version': 1, 'timestamp_ms': timestamp_ms, 'width': width, 'height': height,
                'crop_pixels': list(crop), 'coordinate_space': 'original_image_normalized',
                'model': self.resource['model'], 'model_sha256': self.resource['sha256'], 'hands': hands,
                'grip_assessment': 'not_implemented', 'no_hand_does_not_mean_correct_grip': True}

    def close(self):
        self.landmarker.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
