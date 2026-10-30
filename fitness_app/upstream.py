"""复用 yakupzengin 的 YAML 定义读取及二维关节角结构（MIT）。

原文件位于 third_party/fitness-trainer-pose-estimation/exercises/loader.py
及 pose_estimation/angle_calculation.py；固定提交与许可见 third_party/sources.json。
修正：固定文件边界、有限值与零向量校验、acos 浮点夹取，不沿用质量阈值。
"""
import math
from pathlib import Path
import yaml

from .paths import ROOT

DEFINITIONS = ROOT / "third_party/fitness-trainer-pose-estimation/exercises/definitions"


def load_upstream_definition(name: str) -> dict:
    if not name.replace("_", "").isalnum():
        raise ValueError("动作定义名称非法")
    path = (DEFINITIONS / f"{name}.yaml").resolve()
    path.relative_to(DEFINITIONS.resolve())
    with path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict) or "name" not in config:
        raise ValueError("上游动作配置不完整")
    return config


def calculate_angle(a, b, c) -> float:
    points = [float(v) for point in (a, b, c) for v in point[:2]]
    if len(points) != 6 or not all(math.isfinite(v) for v in points):
        raise ValueError("关节坐标无效")
    ba = [points[0] - points[2], points[1] - points[3]]
    bc = [points[4] - points[2], points[5] - points[3]]
    denominator = math.hypot(*ba) * math.hypot(*bc)
    if denominator < 1e-12:
        raise ValueError("关节向量退化，无法计算角度")
    cosine = sum(x*y for x, y in zip(ba, bc)) / denominator
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
