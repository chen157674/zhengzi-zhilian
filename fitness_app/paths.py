"""正式应用的文件边界及运行环境；第三方库导入前调用。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = "fitness-assistant"


def project_path(value: str | Path) -> Path:
    path = Path(value)
    path = (path if path.is_absolute() else ROOT / path).resolve()
    path.relative_to(ROOT)
    return path


def configure_environment() -> None:
    if str(ROOT).lower() != r"E:\07_Projects\05_PersonalProjects\10_fitness-assistant".lower():
        raise RuntimeError("正式项目目录不符合当前确认的文件边界")
    locations = {"TEMP": ".tmp/runtime", "TMP": ".tmp/runtime",
                 "MPLCONFIGDIR": ".cache/matplotlib", "XDG_CACHE_HOME": ".cache/xdg",
                 "CUDA_CACHE_PATH": ".cache/cuda", "TORCH_HOME": ".cache/torch",
                 "TORCHINDUCTOR_CACHE_DIR": ".cache/torchinductor", "TRITON_CACHE_DIR": ".cache/triton",
                 "HF_HOME": ".cache/huggingface", "HF_HUB_CACHE": ".cache/huggingface/hub",
                 "HF_DATASETS_CACHE": ".cache/huggingface/datasets", "YOLO_CONFIG_DIR": ".cache/ultralytics"}
    for name, relative in locations.items():
        target = project_path(relative)
        target.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(target)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.environ['YOLO_AUTOINSTALL'] = 'false'
    os.environ['YOLO_OFFLINE'] = 'true'


def runtime_path() -> Path:
    path = project_path(os.environ.get("FITNESS_RUNTIME_DIR", ".tmp/runtime"))
    path.mkdir(parents=True, exist_ok=True)
    return path
