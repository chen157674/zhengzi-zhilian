
# 2D sequence normalization to compare user and ideal at the same scale.
# full_analysis block 9: normalize_pose_sequence(kps_for_norm, config=NormalizationConfig(...)).

from src.biomechanics.normalization.config import NormalizationConfig
from src.biomechanics.normalization.sequence import normalize_pose_sequence

__all__ = [
    "NormalizationConfig",
    "normalize_pose_sequence",
]
