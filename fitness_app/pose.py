"""正式本机 Tasks Pose Landmarker；不沿用上游已弃用的 mp.solutions。"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from .paths import ROOT, project_path

DEFAULT_MODEL = ROOT / "data/models/pose_landmarker_full.task"


@lru_cache(maxsize=8)
def checked_model_hash(path,modified_ns,size,expected):
    # 同文件状态／预期哈希不变时复用校验，不在每次会话查询重读模型。
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected


def model_status(path: Path | None = None) -> dict:
    path = project_path(path or DEFAULT_MODEL)
    if not path.is_file():
        return {"available": False, "reason": "MODEL_MISSING"}
    if path == DEFAULT_MODEL:
        manifest_path = path.parent / "manifest.json"
        if not manifest_path.is_file():
            return {"available": False, "reason": "MODEL_MANIFEST_MISSING"}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        stat=path.stat()
        if not checked_model_hash(str(path),stat.st_mtime_ns,stat.st_size,manifest['sha256']):
            return {"available": False, "reason": "MODEL_HASH_MISMATCH"}
    return {"available": True, "variant": "full", "bytes": path.stat().st_size}


class PoseService:
    def __init__(self, model_path: Path | None = None, video: bool = False):
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        path = project_path(model_path or DEFAULT_MODEL)
        if not model_status(path)["available"]:
            raise FileNotFoundError("姿态模型不存在或校验失败")
        self.mp = mp
        self.video = video
        self.last_timestamp = -1
        options = vision.PoseLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_buffer=path.read_bytes(), delegate=python.BaseOptions.Delegate.CPU),
            running_mode=vision.RunningMode.VIDEO if video else vision.RunningMode.IMAGE,
            num_poses=2,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self.landmarker = vision.PoseLandmarker.create_from_options(options)

    def detect(self, rgb, timestamp_ms: int = 0) -> dict:
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        if self.video:
            if timestamp_ms <= self.last_timestamp:
                raise ValueError("源时间戳必须递增")
            self.last_timestamp = timestamp_ms
            result = self.landmarker.detect_for_video(image, timestamp_ms)
        else:
            result = self.landmarker.detect(image)
        return {"timestamp_ms": timestamp_ms, "poses": [
            [{"x": p.x, "y": p.y, "z": p.z, "visibility": p.visibility,
              "presence": p.presence} for p in pose] for pose in result.pose_landmarks],
                "world_poses": [[{"x":p.x,"y":p.y,"z":p.z,"visibility":p.visibility,"presence":p.presence}
                                  for p in pose] for pose in result.pose_world_landmarks],
                "model": "mediapipe-full-float16-1", "schema_version": 1}

    def close(self) -> None:
        self.landmarker.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
