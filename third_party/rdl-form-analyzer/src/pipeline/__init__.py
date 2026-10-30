# Public API of the pipeline package (config, run_full_analysis, results).
#
# Lazy loading (PEP 562): importing `src.pipeline` (or the lightweight submodule
# `src.pipeline.status`) does NOT pull in `full_analysis`/`config`, which depend on
# the heavy stack (numpy/torch/mmpose). This lets the Flet UI (.venv_flet, without
# that stack) import `src.pipeline.status` to reuse ALL_STATUSES.

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from src.pipeline import status  # lightweight submodule, no heavy dependencies

# Public surface of the pipeline package.
__all__ = [
    "FullAnalysisArtifacts",
    "FullAnalysisConfig",
    "PipelineResult",
    "ValidationResult",
    "OrientationConfig",
    "PoseExtractionConfig",
    "run_full_analysis",
    "status",
]

# Public name -> (module, attribute). Resolved on demand.
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "FullAnalysisArtifacts": ("src.pipeline.results", "FullAnalysisArtifacts"),
    "PipelineResult": ("src.pipeline.results", "PipelineResult"),
    "ValidationResult": ("src.pipeline.results", "ValidationResult"),
    "FullAnalysisConfig": ("src.pipeline.config", "FullAnalysisConfig"),
    "OrientationConfig": ("src.pipeline.config", "OrientationConfig"),
    "PoseExtractionConfig": ("src.pipeline.config", "PoseExtractionConfig"),
    "run_full_analysis": ("src.pipeline.full_analysis", "run_full_analysis"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value  # cache for later accesses
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


if TYPE_CHECKING:
    from src.pipeline.config import (
        FullAnalysisConfig,
        OrientationConfig,
        PoseExtractionConfig,
    )
    from src.pipeline.full_analysis import run_full_analysis
    from src.pipeline.results import (
        FullAnalysisArtifacts,
        PipelineResult,
        ValidationResult,
    )
