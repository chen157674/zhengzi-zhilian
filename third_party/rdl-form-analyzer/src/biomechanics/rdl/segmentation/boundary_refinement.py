# Fine-tuning of top_start / ecc_start when there are many invalid frames before the descent.
# The pipeline calls refine_rdl_candidate_boundaries after build_rdl_repetitions.

from __future__ import annotations

from typing import Any

import numpy as np

from src.biomechanics.rdl.segmentation.config import RDLSegmentationConfig
from src.biomechanics.rdl.segmentation.repetitions import (
    INFERRED_START_MIN_EXCURSION_ABS,
    rebuild_rdl_candidate_phase_and_anchors,
)
from src.biomechanics.rdl.segmentation.signal_stats import max_false_gap, min_net_motion_threshold


def _find_stable_top_before_descent(
    signal_smooth: np.ndarray,
    valid_mask: np.ndarray,
    *,
    top_start: int,
    proposed_ecc_start: int,
    bottom: int,
    top_band: float,
    fps: float,
    tolerance: float,
) -> tuple[int | None, dict[str, Any]]:
    y = np.asarray(signal_smooth, dtype=np.float64)
    valid = np.asarray(valid_mask, dtype=bool)
    n = y.size
    lo = max(0, int(top_start))
    hi = min(n - 1, int(proposed_ecc_start) - 1, int(bottom) - 1)
    min_window = max(3, int(round(0.20 * float(fps))))
    debug: dict[str, Any] = {
        "found": False,
        "search_start": int(lo),
        "search_end": int(hi),
        "min_window_frames": int(min_window),
        "tolerance": float(tolerance),
        "top_threshold": float(top_band - tolerance),
    }
    if hi - lo + 1 < min_window:
        debug["reason"] = "search_window_too_short"
        return None, debug

    best_start: int | None = None
    best_window_debug: dict[str, Any] | None = None
    threshold = float(top_band - tolerance)
    for end in range(hi, lo + min_window - 2, -1):
        start = end - min_window + 1
        window_values = y[start : end + 1]
        finite = np.isfinite(window_values)
        valid_window = valid[start : end + 1] & finite
        valid_ratio = float(np.mean(valid_window)) if valid_window.size else 0.0
        near_top = valid_window & (window_values >= threshold)
        near_top_ratio = float(np.mean(near_top)) if near_top.size else 0.0
        if valid_ratio >= 0.80 and near_top_ratio >= 0.80:
            best_start = int(start)
            best_window_debug = {
                "start": int(start),
                "end": int(end),
                "valid_ratio": float(valid_ratio),
                "near_top_ratio": float(near_top_ratio),
            }
            break

    if best_start is None:
        debug["reason"] = "no_stable_window_before_descent"
        return None, debug
    debug["found"] = True
    debug["selected_window"] = best_window_debug
    return int(best_start), debug


def refine_rdl_candidate_boundaries(
    rep_candidates: list[dict],
    signal_smooth: np.ndarray,
    valid_mask: np.ndarray,
    *,
    fps: float,
    global_quality: dict,
    config: RDLSegmentationConfig,
) -> tuple[list[dict], dict]:
    """Trims an unstable start before the eccentric if it reduces invalid gaps and keeps the minimum excursion."""
    y = np.asarray(signal_smooth, dtype=np.float64)
    valid = np.asarray(valid_mask, dtype=bool)
    rom = float(global_quality.get("global_rom", 0.0) or 0.0)
    top_band_global = float(global_quality.get("top_band", float("nan")))
    bottom_band_global = float(global_quality.get("bottom_band", float("nan")))
    max_invalid_gap_frames = int(round(float(fps) * float(config.validation.max_invalid_gap_seconds)))
    min_excursion = max(float(config.validation.min_excursion_deg), float(INFERRED_START_MIN_EXCURSION_ABS))
    net_motion_ratio = float(config.validation.min_net_motion_excursion_ratio)
    min_net_motion_abs = float(config.validation.min_net_motion_abs)

    refined_candidates: list[dict[str, Any]] = []
    debug_candidates: list[dict[str, Any]] = []
    refined_ids: list[int] = []

    def _reject(cand: dict[str, Any], cand_debug: dict[str, Any], reason: str, **extra: Any) -> None:
        """Records why a candidate's boundary wasn't refined and keeps it unchanged."""
        cand_debug["reason"] = reason
        cand_debug.update(extra)
        debug_candidates.append(cand_debug)
        refined_candidates.append(cand)

    for idx, rep in enumerate(rep_candidates):
        cand = dict(rep)
        old_top_start = int(cand.get("top_start", -1))
        old_ecc_start = int(cand.get("ecc_start", old_top_start))
        bottom = int(cand.get("bottom", -1))
        top_end = int(cand.get("top_end", -1))
        candidate_id = int(cand.get("candidate_id", cand.get("rep_id", idx)))
        cand_debug: dict[str, Any] = {
            "candidate_id": candidate_id,
            "old_top_start": old_top_start,
            "old_ecc_start": old_ecc_start,
            "bottom": bottom,
            "top_end": top_end,
            "refined": False,
            "reason": "not_applicable",
        }

        if not (0 <= old_top_start < bottom < top_end < y.size):
            _reject(cand, cand_debug, "invalid_candidate_bounds")
            continue
        if not np.isfinite([y[old_top_start], y[bottom], y[top_end]]).all():
            _reject(cand, cand_debug, "non_finite_key_points")
            continue

        # ecc_start was already found by build_rdl_repetitions (find_active_eccentric_start);
        # reuse it instead of re-running that search here with the same top_start/bottom.
        proposed_ecc_start = old_ecc_start
        cand_debug["proposed_ecc_start"] = int(proposed_ecc_start)
        cand_debug["ecc_start_debug"] = cand.get("phase_debug", {}).get("ecc_start", {})
        if proposed_ecc_start <= old_top_start or proposed_ecc_start >= bottom:
            _reject(cand, cand_debug, "no_late_descent_onset_found")
            continue

        top_band = float(cand.get("top_band", top_band_global))
        if not np.isfinite(top_band):
            _reject(cand, cand_debug, "missing_top_band")
            continue
        tolerance = max(2.0, 0.03 * max(rom, 0.0))
        stable_top, stable_debug = _find_stable_top_before_descent(
            y,
            valid,
            top_start=old_top_start,
            proposed_ecc_start=proposed_ecc_start,
            bottom=bottom,
            top_band=top_band,
            fps=fps,
            tolerance=tolerance,
        )
        cand_debug["stable_top_debug"] = stable_debug
        if stable_top is None:
            _reject(cand, cand_debug, "no_stable_top_before_descent")
            continue

        new_top_start = int(stable_top)
        new_ecc_start = max(new_top_start, int(proposed_ecc_start))
        if not (new_top_start > old_top_start and new_top_start < bottom):
            _reject(cand, cand_debug, "refined_top_not_strictly_better")
            continue
        # new_excursion is checked against two different thresholds below: a flat floor
        # (min_excursion) and one proportional to the excursion itself (min_net_motion).
        new_excursion = float(y[new_top_start] - y[bottom])
        min_net_motion = min_net_motion_threshold(new_excursion, min_abs=min_net_motion_abs, min_excursion_ratio=net_motion_ratio)
        if not np.isfinite(new_excursion) or new_excursion < min_excursion:
            _reject(cand, cand_debug, "refined_excursion_too_small", new_excursion=new_excursion)
            continue
        if not np.isfinite(new_excursion) or new_excursion < min_net_motion:
            _reject(cand, cand_debug, "refined_net_drop_too_small", net_drop=new_excursion, min_net_motion=float(min_net_motion))
            continue

        old_pre = valid[old_top_start : proposed_ecc_start + 1]
        new_pre = valid[new_top_start : proposed_ecc_start + 1]
        old_pre_gap = max_false_gap(old_pre)
        new_pre_gap = max_false_gap(new_pre)
        cand_debug["old_pre_invalid_gap"] = int(old_pre_gap)
        cand_debug["new_pre_invalid_gap"] = int(new_pre_gap)
        if not (old_pre_gap > max_invalid_gap_frames and new_pre_gap <= max_invalid_gap_frames):
            _reject(cand, cand_debug, "trim_does_not_fix_pre_descent_invalid_gap")
            continue

        updated = dict(cand)
        updated["top_start"] = int(new_top_start)
        updated["ecc_start"] = int(new_ecc_start)
        updated = rebuild_rdl_candidate_phase_and_anchors(
            updated,
            y,
            fps=fps,
            global_rom=rom,
            top_band_floor=top_band_global,
            bottom_band_floor=bottom_band_global,
            config=config,
        )
        updated["boundary_refined"] = True
        updated["boundary_refinement"] = {
            "old_top_start": int(old_top_start),
            "new_top_start": int(new_top_start),
            "old_ecc_start": int(old_ecc_start),
            "new_ecc_start": int(new_ecc_start),
            "reason": "trimmed_unstable_pre_descent",
            "debug": {
                "old_pre_invalid_gap": int(old_pre_gap),
                "new_pre_invalid_gap": int(new_pre_gap),
                "max_invalid_gap_frames": int(max_invalid_gap_frames),
                "new_excursion": float(new_excursion),
                "min_excursion_required": float(min_excursion),
            },
        }
        cand_debug["refined"] = True
        cand_debug["reason"] = "trimmed_unstable_pre_descent"
        cand_debug["new_top_start"] = int(new_top_start)
        cand_debug["new_ecc_start"] = int(new_ecc_start)
        refined_ids.append(candidate_id)
        debug_candidates.append(cand_debug)
        refined_candidates.append(updated)

    debug = {
        "num_candidates_refined": int(len(refined_ids)),
        "refined_candidate_ids": sorted(refined_ids),
        "candidates": debug_candidates,
    }
    return refined_candidates, debug
