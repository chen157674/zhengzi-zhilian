# Configuration dataclasses: video, YOLO/RTMPose weights, orientation, segmentation, PM-Ideal.

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.biomechanics.rdl import RDLSegmentationConfig
from src.utils.paths import (
    rdl_reference_dir,
    RTMPOSE_CHECKPOINT,
    RTMPOSE_CONFIG,
    YOLO_WEIGHTS,
)


@dataclass(frozen=True)
class PoseExtractionConfig:
    # Model/weight configuration for pose extraction.
    config_path: Path = RTMPOSE_CONFIG
    checkpoint_path: Path = RTMPOSE_CHECKPOINT
    yolo_weights: Path = YOLO_WEIGHTS
    verbose: bool = False


@dataclass(frozen=True)
class OrientationConfig:
    target_facing: str = "right"
    score_threshold: float = 0.30
    min_valid_frames: int = 10
    margin: float = 0.03


@dataclass(frozen=True)
class FullAnalysisConfig:
    # Main input for the initial flow.
    video_path: Path
    # Pose sub-configuration.
    pose: PoseExtractionConfig = field(default_factory=PoseExtractionConfig)
    # Lateral orientation estimation configuration.
    orientation: OrientationConfig = field(default_factory=OrientationConfig)
    # RDL segmentation configuration.
    segmentation: RDLSegmentationConfig = field(default_factory=RDLSegmentationConfig)
    # Fixed ideal reference for building the RDL context.
    reference_dir: Path = rdl_reference_dir("PM-Ideal")
    ideal_valid_rep_index: int = 1
