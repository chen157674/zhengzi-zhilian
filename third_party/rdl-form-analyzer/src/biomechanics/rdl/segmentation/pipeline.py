# Orchestrator: pose → hip signal → peaks → reps → validate → segmentation_result dict.
# full_analysis block 8: run_rdl_segmentation_from_pose(pose_clean, config=config.segmentation).

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from src.biomechanics.rdl.segmentation.boundary_refinement import refine_rdl_candidate_boundaries
from src.biomechanics.rdl.segmentation.config import RDLSegmentationConfig
from src.biomechanics.rdl.segmentation.events import (
    add_boundary_top_bottom_events,
    center_top_bottom_plateaus,
    classify_top_bottom_events,
    collapse_double_bottoms,
    detect_top_bottom_candidates,
    enforce_top_bottom_alternation,
    merge_micro_oscillations,
    recover_boundary_tops,
)
from src.biomechanics.rdl.segmentation.io import load_pose_npz, validate_pose_data_dict
from src.biomechanics.rdl.segmentation.repetitions import build_rdl_repetitions
from src.biomechanics.rdl.segmentation.signal import RDLSignalBundle, build_rdl_signal
from src.biomechanics.rdl.segmentation.validation import (
    NO_VALID_RDL_SIGNAL,
    NO_VALID_REPS,
    PARTIAL_VIDEO,
    SEGMENTATION_STATUS_OK,
    TOO_NOISY,
    WRONG_MOVEMENT_OR_NOT_RDL,
    append_edge_artifact_candidates,
    compute_global_signal_quality,
    select_consistent_rep_block,
    validate_rdl_rep_candidates,
)
from src.pose_cleaning.constants import RIGHT_CHAIN

# shoulder(6), hip(12), knee(14): same 3 keypoints build_rdl_signal uses for the angle.
_ANGLE_KEYPOINTS = (6, 12, 14)


def _angle_keypoints_valid_mask(pose: dict[str, Any]) -> np.ndarray | None:
    """Per-frame validity for shoulder/hip/knee already resolved by pose_cleaning, if available.

    Lets build_rdl_signal trust a frame pose_cleaning already fixed (e.g. filled a short gap)
    without re-deriving that from raw confidence scores at a different threshold.
    """
    mask_valid_right_chain = pose.get("mask_valid_right_chain")
    if mask_valid_right_chain is None or not bool(pose.get("has_clean_pose")):
        return None
    try:
        positions = [RIGHT_CHAIN.index(kp) for kp in _ANGLE_KEYPOINTS]
    except ValueError:
        return None
    return np.asarray(mask_valid_right_chain, dtype=bool)[:, positions].all(axis=1)


def _as_float_list(arr: np.ndarray) -> list[float]:
    return [float(x) if np.isfinite(x) else float("nan") for x in arr.tolist()]


def _as_bool_list(arr: np.ndarray) -> list[bool]:
    return [bool(x) for x in arr.tolist()]


def run_rdl_segmentation(
    npz_path: str | Path,
    *,
    config: RDLSegmentationConfig | None = None,
) -> dict[str, Any]:
    """Segmentation from an NPZ file (scripts); loads via io.load_pose_npz."""
    cfg = config if config is not None else RDLSegmentationConfig()
    pose = load_pose_npz(npz_path)
    return _run_rdl_segmentation_core(pose, cfg)


def run_rdl_segmentation_from_pose(
    pose_data: dict[str, Any],
    *,
    config: RDLSegmentationConfig | None = None,
) -> dict[str, Any]:
    """Main pipeline entry point: in-memory pose_clean dict → segmentation_result."""
    cfg = config if config is not None else RDLSegmentationConfig()
    pose = validate_pose_data_dict(pose_data)
    return _run_rdl_segmentation_core(pose, cfg)


@dataclass
class _EventDetection:
    """Output of `_detect_rdl_events`: every stage of the top/bottom cleanup pipeline.

    Kept as separate before/after arrays (rather than just the final valid lists) because the
    segmentation summary reports how many candidates survive each cleanup stage.
    """

    bottom_raw: np.ndarray
    top_raw: np.ndarray
    bottom_after_alternation: np.ndarray
    top_after_alternation: np.ndarray
    bottom_after_merge: np.ndarray
    top_after_merge: np.ndarray
    bottom_after_collapse: np.ndarray
    top_after_collapse: np.ndarray
    bottom_final: np.ndarray
    top_final: np.ndarray
    bottom_valid: list[int]
    top_valid: list[int]
    bottom_details: list[dict[str, Any]]
    top_details: list[dict[str, Any]]
    boundary_top_debug: dict[str, Any]


def _stabilize_top_bottom_events(
    signal: np.ndarray,
    bottoms: np.ndarray,
    tops: np.ndarray,
    *,
    rom: float,
    double_bottom_window_frames: int,
    bridge_factor: float,
    max_iterations: int = 4,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Normalizes alternation, collapses double-bottoms, and centers events on their plateau.

    None of these three cleanups is safe to run just once: `collapse_double_bottoms` needs
    strictly alternating input to match its bottom-top-bottom pattern, and `center_top_bottom_plateaus`
    can shift a bottom close enough to a neighbor to create a new collapsible double-bottom that
    didn't exist before centering. So the group is repeated until a pass leaves the arrays
    unchanged (bounded by `max_iterations` as a safety cap, not because convergence is expected
    to take that long in practice).

    Returns `(bottom_after_first_collapse, top_after_first_collapse, bottom_final, top_final)`;
    the first pair is kept only for the segmentation summary counts.
    """
    bottom_alt, top_alt = enforce_top_bottom_alternation(signal, bottoms, tops)
    bottom_collapsed, top_collapsed = collapse_double_bottoms(
        signal,
        bottom_alt,
        top_alt,
        min_separation_frames=double_bottom_window_frames,
        bridge_factor=bridge_factor,
    )
    bottom, top = bottom_collapsed, top_collapsed
    for _ in range(max_iterations):
        centered_bottom, centered_top = center_top_bottom_plateaus(signal, bottom, top, rom=rom)
        new_bottom, new_top = collapse_double_bottoms(
            signal,
            centered_bottom,
            centered_top,
            min_separation_frames=double_bottom_window_frames,
            bridge_factor=bridge_factor,
        )
        stable = np.array_equal(new_bottom, bottom) and np.array_equal(new_top, top)
        bottom, top = new_bottom, new_top
        if stable:
            break
    return bottom_collapsed, top_collapsed, bottom, top


def _detect_rdl_events(
    signal: RDLSignalBundle,
    *,
    fps: float,
    global_quality: dict[str, Any],
    local_thr: float,
    cfg: RDLSegmentationConfig,
) -> _EventDetection:
    """Raw peaks → alternate top/bottom → drop noise → recover missing boundary events → classify.

    Each stage only cleans up what the previous stage produced; see events.py for what each one
    does. `recover_boundary_tops` runs last because it needs the fully-classified bottom/top
    lists to know whether the video is missing a top at the very start or end.
    """
    bottom_raw, top_raw = detect_top_bottom_candidates(
        signal.signal_smooth,
        min_distance=cfg.min_peak_distance,
        min_prominence_ratio=cfg.min_prominence_ratio,
    )
    bottom_alt, top_alt = enforce_top_bottom_alternation(signal.signal_smooth, bottom_raw, top_raw)
    bottom_merged, top_merged = merge_micro_oscillations(signal.signal_smooth, bottom_alt, top_alt, thr=local_thr)
    bottom_boundary, top_boundary = add_boundary_top_bottom_events(
        signal.signal_smooth,
        bottom_merged,
        top_merged,
        thr=local_thr,
        allow_boundary_events=cfg.allow_boundary_events,
        boundary_hold_frames=int(round(float(cfg.boundary_hold_seconds) * fps)),
        boundary_requires_stable_top=cfg.boundary_requires_stable_top,
        top_band=float(global_quality["top_band"]),
        valid_mask=signal.valid_mask,
    )
    double_bottom_window_frames = max(1, int(round(float(cfg.double_bottom_window_seconds) * fps)))
    bottom_collapsed, top_collapsed, bottom_final, top_final = _stabilize_top_bottom_events(
        signal.signal_smooth,
        bottom_boundary,
        top_boundary,
        rom=signal.rom,
        double_bottom_window_frames=double_bottom_window_frames,
        bridge_factor=cfg.double_bottom_bridge_factor,
    )
    bottom_valid, bottom_details, top_valid, top_details = classify_top_bottom_events(
        signal.signal_smooth,
        bottom_final,
        top_final,
        rom=signal.rom,
        thr=local_thr,
    )
    top_valid, recovered_top_details, boundary_top_debug = recover_boundary_tops(
        signal.signal_smooth,
        signal.valid_mask,
        bottom_valid,
        top_valid,
        top_final,
        global_rom=float(global_quality["global_rom"]),
        top_band=float(global_quality["top_band"]),
    )

    return _EventDetection(
        bottom_raw=bottom_raw,
        top_raw=top_raw,
        bottom_after_alternation=bottom_alt,
        top_after_alternation=top_alt,
        bottom_after_merge=bottom_merged,
        top_after_merge=top_merged,
        bottom_after_collapse=bottom_collapsed,
        top_after_collapse=top_collapsed,
        bottom_final=bottom_final,
        top_final=top_final,
        bottom_valid=bottom_valid,
        top_valid=top_valid,
        bottom_details=bottom_details,
        top_details=top_details + recovered_top_details,
        boundary_top_debug=boundary_top_debug,
    )


def _build_and_select_reps(
    signal: RDLSignalBundle,
    events: _EventDetection,
    *,
    fps: float,
    global_quality: dict[str, Any],
    cfg: RDLSegmentationConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Pair top/bottom events into rep candidates, refine, validate, and pick the final reps.

    Returns (final_reps, all_rep_candidates, debug). `all_rep_candidates` includes discarded
    candidates (with their rejection reason) plus edge artifacts, for debugging/visualization.
    """
    rep_candidates_raw = build_rdl_repetitions(
        signal.signal_smooth,
        events.bottom_valid,
        events.top_valid,
        fps=fps,
        global_quality=global_quality,
        config=cfg,
    )
    rep_candidates_refined, boundary_refinement_debug = refine_rdl_candidate_boundaries(
        rep_candidates_raw,
        signal.signal_smooth,
        signal.valid_mask,
        fps=fps,
        global_quality=global_quality,
        config=cfg,
    )
    rep_candidates, candidate_validation_debug = validate_rdl_rep_candidates(
        rep_candidates_refined,
        signal.signal_smooth,
        fps=fps,
        valid_mask=signal.valid_mask,
        valid_mask_raw=signal.valid_mask_raw,
        interp_mask=signal.interp_mask,
        global_quality=global_quality,
        config=cfg.validation,
    )
    final_reps, block_debug = select_consistent_rep_block(rep_candidates)
    rep_candidates = append_edge_artifact_candidates(
        rep_candidates,
        final_reps,
        signal.signal_smooth,
        fps=fps,
    )
    debug = {
        "boundary_refinement": boundary_refinement_debug,
        "candidate_validation": candidate_validation_debug,
        "consistent_block": block_debug,
    }
    return final_reps, rep_candidates, debug


def _determine_segmentation_status(final_reps: list[dict[str, Any]], rep_candidates: list[dict[str, Any]]) -> str:
    """Classifies *why* segmentation failed (or that it succeeded) from the discarded candidates."""
    if final_reps:
        return SEGMENTATION_STATUS_OK
    labels = {str(c.get("label", "")) for c in rep_candidates}
    reasons = {str(c.get("reason", "")) for c in rep_candidates}
    # "POST_EXERCISE_NOISE" is intentionally not checked here: validation.append_edge_artifact_candidates
    # only ever assigns it when final_reps is non-empty, which never happens on this branch.
    if labels & {"PARTIAL_START", "PARTIAL_END"}:
        return PARTIAL_VIDEO
    if labels & {"UNSTABLE_SIGNAL"} or reasons & {"unstable_signal", "abrupt_signal_jump"}:
        return TOO_NOISY
    if labels & {"WRONG_MOVEMENT_OR_NOT_RDL"}:
        return WRONG_MOVEMENT_OR_NOT_RDL
    return NO_VALID_REPS


def _empty_segmentation_result(signal: RDLSignalBundle, base_result: dict[str, Any]) -> dict[str, Any]:
    """Result shape when the video fails the global signal-quality gate (no point searching for reps)."""
    return {
        **base_result,
        "segmentation_status": NO_VALID_RDL_SIGNAL,
        "summary": {
            "rom": float(signal.rom),
            "p5": float(signal.p5) if np.isfinite(signal.p5) else float("nan"),
            "p95": float(signal.p95) if np.isfinite(signal.p95) else float("nan"),
            "num_reps": 0,
            "num_rep_candidates": 0,
            "num_reps_with_valid_anchors": 0,
            "num_reps_with_invalid_anchors": 0,
            "savgol_fallback_used": bool(signal.savgol_fallback_used),
        },
        "event_candidates": {
            "bottom_indices_candidates": [],
            "top_indices_candidates": [],
            "bottom_indices_valid": [],
            "top_indices_valid": [],
            "bottom_details": [],
            "top_details": [],
        },
        "reps": [],
    }


def _run_rdl_segmentation_core(
    pose: dict[str, Any],
    cfg: RDLSegmentationConfig,
) -> dict[str, Any]:
    """Full chain: build_rdl_signal → detect events → build reps → validate → segmentation_result."""
    fps = float(pose["fps"])
    signal = build_rdl_signal(
        pose["kps_xy"],
        pose["kps_score"],
        thr_conf=cfg.thr_conf,
        max_gap_interp=cfg.max_gap_interp,
        savgol_window_length=cfg.savgol_window_length,
        savgol_polyorder=cfg.savgol_polyorder,
        extra_valid_mask=_angle_keypoints_valid_mask(pose),
    )
    global_quality = compute_global_signal_quality(
        signal.signal_smooth,
        signal.valid_mask,
        config=cfg.validation,
    )

    base_result: dict[str, Any] = {
        "video_id": pose["video_id"],
        "exercise": "RDL",
        "fps": fps,
        "pose_source": pose["pose_source"],
        "has_clean_pose": bool(pose["has_clean_pose"]),
        "signal_name": "rdl_hip_angle_k6_k12_k14_deg",
        "config": asdict(cfg),
        "signals": {
            "signal_raw": _as_float_list(signal.signal_raw),
            "signal_smooth": _as_float_list(signal.signal_smooth),
            "valid_mask_raw": _as_bool_list(signal.valid_mask_raw),
            "interp_mask": _as_bool_list(signal.interp_mask),
            "valid_mask": _as_bool_list(signal.valid_mask),
        },
        "validation_debug": {"global_signal_quality": global_quality},
        "event_candidates": {},
        "rep_candidates": [],
    }

    if global_quality["status"] != SEGMENTATION_STATUS_OK:
        return _empty_segmentation_result(signal, base_result)

    local_thr = float(cfg.local_prominence_ratio) * float(signal.rom)
    events = _detect_rdl_events(signal, fps=fps, global_quality=global_quality, local_thr=local_thr, cfg=cfg)
    final_reps, rep_candidates, reps_debug = _build_and_select_reps(
        signal, events, fps=fps, global_quality=global_quality, cfg=cfg
    )

    num_candidates_with_valid_anchors = int(sum(1 for r in rep_candidates if bool(r.get("anchor_valid", False))))
    num_candidates_with_invalid_anchors = int(len(rep_candidates) - num_candidates_with_valid_anchors)
    num_reps_with_valid_anchors = int(sum(1 for r in final_reps if bool(r.get("anchor_valid", False))))
    num_reps_with_invalid_anchors = int(len(final_reps) - num_reps_with_valid_anchors)

    return {
        **base_result,
        "segmentation_status": _determine_segmentation_status(final_reps, rep_candidates),
        "summary": {
            "rom": float(signal.rom),
            "p5": float(signal.p5) if np.isfinite(signal.p5) else float("nan"),
            "p95": float(signal.p95) if np.isfinite(signal.p95) else float("nan"),
            "num_bottom_candidates_raw": int(events.bottom_raw.size),
            "num_top_candidates_raw": int(events.top_raw.size),
            "num_bottom_after_alternation": int(events.bottom_after_alternation.size),
            "num_top_after_alternation": int(events.top_after_alternation.size),
            "num_bottom_after_merge": int(events.bottom_after_merge.size),
            "num_top_after_merge": int(events.top_after_merge.size),
            "num_bottom_after_double_bottom_collapse": int(events.bottom_after_collapse.size),
            "num_top_after_double_bottom_collapse": int(events.top_after_collapse.size),
            "num_bottom_final": int(events.bottom_final.size),
            "num_top_final": int(events.top_final.size),
            "num_bottom_valid": int(len(events.bottom_valid)),
            "num_top_valid": int(len(events.top_valid)),
            "num_rep_candidates": len(rep_candidates),
            "num_reps": len(final_reps),
            "num_rep_candidates_with_valid_anchors": num_candidates_with_valid_anchors,
            "num_rep_candidates_with_invalid_anchors": num_candidates_with_invalid_anchors,
            "num_reps_with_valid_anchors": num_reps_with_valid_anchors,
            "num_reps_with_invalid_anchors": num_reps_with_invalid_anchors,
            "savgol_fallback_used": bool(signal.savgol_fallback_used),
        },
        "event_candidates": {
            "bottom_indices_candidates": [int(x) for x in events.bottom_final],
            "top_indices_candidates": [int(x) for x in events.top_final],
            "bottom_indices_valid": [int(x) for x in events.bottom_valid],
            "top_indices_valid": [int(x) for x in events.top_valid],
            "bottom_details": events.bottom_details,
            "top_details": events.top_details,
        },
        "rep_candidates": rep_candidates,
        "validation_debug": {
            "global_signal_quality": global_quality,
            "boundary_top_recovery": events.boundary_top_debug,
            **reps_debug,
        },
        "reps": final_reps,
    }
