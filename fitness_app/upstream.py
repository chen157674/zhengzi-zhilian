"""动作 YAML 定义读取及二维关节几何角计算工具模块。"""
import copy
from functools import lru_cache
import math
from pathlib import Path
import yaml

from .paths import ROOT

DEFINITIONS = ROOT / "fitness_app/definitions"


@lru_cache(maxsize=64)
def _read_definition(path,modified_ns,size):
    # 按文件真实状态缓存解析，调用方拿副本，不能污染下一次能力快照。
    with Path(path).open(encoding="utf-8") as stream:
        config=yaml.safe_load(stream)
    if not isinstance(config,dict) or "name" not in config:
        raise ValueError("动作配置不完整")
    return config


def load_upstream_definition(name: str) -> dict:
    if not name.replace("_", "").isalnum():
        raise ValueError("动作定义名称非法")
    path = (DEFINITIONS / f"{name}.yaml").resolve()

    if not path.exists():
        raise ValueError(f"未找到动作定义: {name}")
    stat=path.stat()
    return copy.deepcopy(_read_definition(str(path),stat.st_mtime_ns,stat.st_size))


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
