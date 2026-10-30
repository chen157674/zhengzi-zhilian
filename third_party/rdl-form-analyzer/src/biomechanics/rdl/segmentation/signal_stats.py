
# Small numeric helpers shared across segmentation modules (events, repetitions,
# validation, boundary_refinement). Previously each module reimplemented these
# independently, sometimes with subtly different formulas.

from __future__ import annotations

import numpy as np


def ratio(mask: np.ndarray) -> float:
    """Fraction of True values in a boolean mask (0.0 for an empty mask)."""
    arr = np.asarray(mask, dtype=bool)
    return float(np.mean(arr)) if arr.size else 0.0


def max_false_gap(mask: np.ndarray) -> int:
    """Longest run of False values in a boolean mask (longest invalid streak)."""
    max_gap = 0
    cur = 0
    for value in np.asarray(mask, dtype=bool):
        if value:
            cur = 0
        else:
            cur += 1
            max_gap = max(max_gap, cur)
    return int(max_gap)


def trend_score(dy: np.ndarray, direction: str) -> float:
    """Fraction of total frame-to-frame movement that goes in `direction` ("down" or "up").

    1.0 = clean monotonic move in that direction; lower values mean the segment
    oscillates back and forth instead of trending consistently.
    """
    dy = np.asarray(dy, dtype=np.float64)
    dy = dy[np.isfinite(dy)]
    if dy.size == 0:
        return 0.0
    denom = float(np.sum(np.abs(dy))) + 1e-8
    if direction == "down":
        return float(np.sum(np.maximum(-dy, 0.0)) / denom)
    return float(np.sum(np.maximum(dy, 0.0)) / denom)


def safe_value(y: np.ndarray, idx: int) -> float:
    """y[idx] if idx is in bounds and finite, else NaN."""
    if 0 <= int(idx) < y.size and np.isfinite(y[int(idx)]):
        return float(y[int(idx)])
    return float("nan")


def drop_between(y: np.ndarray, start: int, end: int) -> float:
    """y[start] - y[end], or NaN if either endpoint is invalid."""
    a = safe_value(y, start)
    b = safe_value(y, end)
    if not np.isfinite(a) or not np.isfinite(b):
        return float("nan")
    return float(a - b)


def min_net_motion_threshold(excursion: float, *, min_abs: float, min_excursion_ratio: float) -> float:
    """Minimum net drop/rise required to trust a phase as real motion, not noise.

    `max(min_abs, min_excursion_ratio * excursion)`: an absolute floor plus a share of the rep's
    own excursion, so short/shallow reps aren't held to the same absolute bar as tall ones.
    Previously duplicated identically across validation.py (3x) and boundary_refinement.py (1x).
    """
    return max(float(min_abs), float(min_excursion_ratio) * max(float(excursion), 0.0))


def moving_average(values: np.ndarray, win: int) -> np.ndarray:
    """Simple moving average with edge padding (avoids the zero-padding bias of mode="same").

    Previously reimplemented independently in signal.py and repetitions.py with different
    left/right padding splits, which made the two moving averages disagree by up to one frame
    for even `win`. Both now call this single implementation.
    """
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return arr.copy()
    win = max(1, int(win))
    if win <= 1:
        return arr.copy()
    pad_left = (win - 1) // 2
    pad_right = win // 2
    padded = np.pad(arr, (pad_left, pad_right), mode="edge")
    kernel = np.ones(win, dtype=np.float64) / float(win)
    return np.convolve(padded, kernel, mode="valid")
