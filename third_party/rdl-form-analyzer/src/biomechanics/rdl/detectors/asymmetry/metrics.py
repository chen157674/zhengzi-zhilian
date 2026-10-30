# Per-frame metrics: normalized position difference between L/R counterparts (shoulders, elbows, hips, knees…).

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Bilateral joint pairs (COCO-17): joint → (L_index, R_index)

BILATERAL_PAIRS: dict[str, tuple[int, int]] = {
    "shoulder": (5, 6),
    "elbow":    (7, 8),
    "wrist":    (9, 10),
    "hip":      (11, 12),
    "knee":     (13, 14),
    "ankle":    (15, 16),
}

# Roles in the sagittal view:
#   primary   → severity classification
#   secondary → reinforces confidence if co-asymmetric with its primary,
#               does not trigger severity on its own.
# Shoulders/hips excluded: in lateral view X reflects width/perspective, not useful asymmetry.
ARM_PRIMARY_JOINTS:   tuple[str, ...] = ("wrist",)
ARM_SECONDARY_JOINTS: tuple[str, ...] = ("elbow",)
ARM_JOINTS:           tuple[str, ...] = ARM_PRIMARY_JOINTS + ARM_SECONDARY_JOINTS

LEG_PRIMARY_JOINTS:   tuple[str, ...] = ("ankle",)
LEG_SECONDARY_JOINTS: tuple[str, ...] = ("knee",)
LEG_JOINTS:           tuple[str, ...] = LEG_PRIMARY_JOINTS + LEG_SECONDARY_JOINTS

# Reference segment for body scale: R_shoulder(6) → R_hip(12).
# Right side only in lateral view: shoulders/hips project onto the same X band; vertical length is stable.
_SCALE_REF = (6, 12)

ALL_BILATERAL_IDX: frozenset[int] = frozenset(
    idx for pair in BILATERAL_PAIRS.values() for idx in pair
)


# Data class

@dataclass(frozen=True)
class JointAsymmetry:

    joint: str
    diff_x: float           # L.x − R.x  (signed px)
    diff_y: float           # L.y − R.y (px; reference, not used for classification)
    signed_diff: float      # L.x − R.x (semantic alias of diff_x)
    forward_diff: float     # abs(signed_diff) in px
    forward_diff_norm: float  # forward_diff / body_scale (primary metric)
    confident: bool         # True if L and R both have score ≥ thr_conf
    body_scale: float       # R_shoulder→R_hip length in px; NaN if degenerate


# Helpers

def body_scale(kps: np.ndarray) -> float:
    i, j = _SCALE_REF
    v = kps[j].astype(float) - kps[i].astype(float)
    n = float(np.linalg.norm(v))
    return n if n > 1e-6 else float("nan")


# Public API

def frame_asymmetry(
    kps: np.ndarray,      # (17, 2)
    scores: np.ndarray,   # (17,)
    *,
    thr_conf: float = 0.3,
) -> dict[str, JointAsymmetry]:
    if kps.shape != (17, 2):
        raise ValueError(f"Expected kps shape (17, 2), got {kps.shape}.")
    scores_1d = np.asarray(scores, dtype=float).ravel()
    if scores_1d.shape[0] != 17:
        raise ValueError(f"Expected 17 scores, got {scores_1d.shape[0]}.")

    scale = body_scale(kps)
    result: dict[str, JointAsymmetry] = {}

    for joint, (li, ri) in BILATERAL_PAIRS.items():
        lp = kps[li].astype(float)
        rp = kps[ri].astype(float)
        if not (np.isfinite(lp).all() and np.isfinite(rp).all()):
            continue

        dx = float(lp[0] - rp[0])
        dy = float(lp[1] - rp[1])
        fwd = abs(dx)
        fwd_norm = fwd / scale if np.isfinite(scale) else float("nan")
        confident = bool(scores_1d[li] >= thr_conf and scores_1d[ri] >= thr_conf)

        result[joint] = JointAsymmetry(
            joint=joint,
            diff_x=dx,
            diff_y=dy,
            signed_diff=dx,
            forward_diff=fwd,
            forward_diff_norm=fwd_norm,
            confident=confident,
            body_scale=scale,
        )

    return result
