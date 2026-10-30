
# Pelvis + torso normalization settings (right side per the project convention).

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizationConfig:
    """Parameters for normalize_pose_sequence.

    method: only 'pelvis_torso_scale' (hip as origin, scale = shoulder–hip).
    pelvis_idx=12, shoulder_idx=6: COCO right hip/shoulder keypoints after flip.
    sequence_scale_mode: 'fixed_median' (pipeline) uses a single scale = median torso
    across the video; 'framewise' scales each frame by its own torso (less stable).
    apply_rotation: disabled; global skeleton orientations are not aligned.
    """

    method: str = "pelvis_torso_scale"
    pelvis_idx: int = 12
    shoulder_idx: int = 6
    eps: float = 1e-6
    apply_rotation: bool = False
    sequence_scale_mode: str = "fixed_median"
