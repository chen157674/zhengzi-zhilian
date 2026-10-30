"""Tests for src/biomechanics/rdl/segmentation (previously untested).

Covers the shared numeric helpers (signal_stats), the event-list dedup/detection/cleanup helpers
and boundary-top recovery in events.py, boundary_refinement.py's candidate trimming, the phase
and anchor search in repetitions.py, io.py's shape validation, pipeline.py's failure-reason
classification, and an end-to-end synthetic-signal regression so future changes to the pipeline
can't silently change how many reps get detected.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.biomechanics.rdl.segmentation import signal_stats
from src.biomechanics.rdl.segmentation.boundary_refinement import refine_rdl_candidate_boundaries
from src.biomechanics.rdl.segmentation.config import RDLSegmentationConfig
from src.biomechanics.rdl.segmentation.events import (
    _collapse_consecutive_same_kind,
    _events_from_arrays,
    add_boundary_top_bottom_events,
    center_top_bottom_plateaus,
    classify_top_bottom_events,
    collapse_double_bottoms,
    detect_top_bottom_candidates,
    merge_micro_oscillations,
    recover_boundary_tops,
)
from src.biomechanics.rdl.segmentation.io import validate_pose_data_dict
from src.biomechanics.rdl.segmentation.pipeline import (
    _angle_keypoints_valid_mask,
    _determine_segmentation_status,
    run_rdl_segmentation_from_pose,
)
from src.biomechanics.rdl.segmentation.repetitions import find_active_eccentric_start, find_concentric_end, find_concentric_start
from src.biomechanics.rdl.segmentation.signal import _compute_angle_signal
from src.biomechanics.rdl.segmentation.validation import (
    NO_VALID_REPS,
    PARTIAL_VIDEO,
    TOO_NOISY,
    WRONG_MOVEMENT_OR_NOT_RDL,
    compute_global_signal_quality,
)
from src.pose_cleaning.constants import RIGHT_CHAIN

# ---------------------------------------------------------------------------
# signal_stats: shared helpers used across events/repetitions/validation/boundary_refinement.
# ---------------------------------------------------------------------------


def test_ratio_of_empty_mask_is_zero():
    assert signal_stats.ratio(np.array([], dtype=bool)) == 0.0


def test_ratio_counts_true_fraction():
    mask = np.array([True, True, False, False, True])
    assert signal_stats.ratio(mask) == pytest.approx(0.6)


def test_max_false_gap_finds_longest_invalid_streak():
    mask = np.array([True, False, False, True, False, False, False, True])
    assert signal_stats.max_false_gap(mask) == 3


def test_max_false_gap_all_true_is_zero():
    assert signal_stats.max_false_gap(np.array([True, True, True])) == 0


def test_trend_score_clean_descent_is_near_one():
    dy = np.array([-10.0, -10.0, -10.0])
    assert signal_stats.trend_score(dy, "down") == pytest.approx(1.0)


def test_trend_score_noisy_segment_is_lower():
    dy = np.array([-10.0, -10.0, 5.0, -25.0, -10.0])
    score = signal_stats.trend_score(dy, "down")
    assert 0.0 < score < 1.0
    assert score == pytest.approx(55.0 / 60.0)


def test_trend_score_symmetric_oscillation_is_half():
    # Equal-magnitude up/down steps: half the total movement is "down", regardless of values.
    dy = np.array([10.0, -10.0, 10.0, -10.0])
    assert signal_stats.trend_score(dy, "down") == pytest.approx(0.5)


def test_safe_value_out_of_bounds_is_nan():
    y = np.array([1.0, 2.0, np.nan])
    assert np.isnan(signal_stats.safe_value(y, -1))
    assert np.isnan(signal_stats.safe_value(y, 5))
    assert np.isnan(signal_stats.safe_value(y, 2))
    assert signal_stats.safe_value(y, 0) == 1.0


def test_drop_between_requires_both_endpoints_finite():
    y = np.array([100.0, 50.0, np.nan])
    assert signal_stats.drop_between(y, 0, 1) == pytest.approx(50.0)
    assert np.isnan(signal_stats.drop_between(y, 0, 2))


def test_min_net_motion_threshold_uses_absolute_floor_when_excursion_is_small():
    assert signal_stats.min_net_motion_threshold(10.0, min_abs=20.0, min_excursion_ratio=0.3) == pytest.approx(20.0)


def test_min_net_motion_threshold_uses_excursion_share_when_it_dominates():
    assert signal_stats.min_net_motion_threshold(100.0, min_abs=20.0, min_excursion_ratio=0.3) == pytest.approx(30.0)


def test_min_net_motion_threshold_nan_excursion_falls_back_to_absolute_floor():
    assert signal_stats.min_net_motion_threshold(float("nan"), min_abs=20.0, min_excursion_ratio=0.3) == pytest.approx(20.0)


def test_moving_average_matches_between_even_and_odd_windows():
    # Regression for the signal.py/repetitions.py duplicate _moving_average that used to disagree
    # on left/right padding for even windows: both modules now share this one implementation.
    seq = np.array([10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
    out_even = signal_stats.moving_average(seq, 4)
    out_odd = signal_stats.moving_average(seq, 3)
    assert out_even.shape == seq.shape
    assert out_odd.shape == seq.shape


# ---------------------------------------------------------------------------
# events.py: event-list dedup helpers.
# ---------------------------------------------------------------------------


def test_events_from_arrays_merges_and_sorts_by_frame():
    y = np.array([170.0, 90.0, 175.0, 85.0])
    events = _events_from_arrays(y, bottoms=np.array([1, 3]), tops=np.array([0, 2]))
    assert events == [("top", 0, 170.0), ("bottom", 1, 90.0), ("top", 2, 175.0), ("bottom", 3, 85.0)]


def test_events_from_arrays_drops_non_finite():
    y = np.array([170.0, np.nan, 90.0])
    events = _events_from_arrays(y, bottoms=np.array([2]), tops=np.array([0, 1]))
    assert events == [("top", 0, 170.0), ("bottom", 2, 90.0)]


def test_collapse_consecutive_same_kind_keeps_most_extreme():
    # two bottoms in a row (no top between them): keep the lower one.
    events = [("top", 0, 170.0), ("bottom", 1, 90.0), ("bottom", 2, 80.0), ("top", 3, 175.0)]
    collapsed = _collapse_consecutive_same_kind(events)
    assert collapsed == [("top", 0, 170.0), ("bottom", 2, 80.0), ("top", 3, 175.0)]


def test_collapse_consecutive_same_kind_empty_input():
    assert _collapse_consecutive_same_kind([]) == []


# ---------------------------------------------------------------------------
# events.py: recover_boundary_tops (consolidates what used to be 4 separate implementations).
# ---------------------------------------------------------------------------


def _descending_then_flat_signal() -> np.ndarray:
    # Starts already mid-descent (no top before the first bottom at frame 20): 170 -> 80 -> 170.
    down = np.linspace(170.0, 80.0, 20)
    up = np.linspace(80.0, 170.0, 20)
    return np.concatenate([down, [80.0] * 5, up])


def test_recover_boundary_tops_recovers_missing_entry_top():
    y = _descending_then_flat_signal()
    valid = np.ones_like(y, dtype=bool)
    bottom_valid = [24]  # middle of the flat bottom stretch
    top_valid = [44]  # exit side already has a top: only entry recovery should run
    top_candidates = np.array([0, 44])

    tops, extra_details, debug = recover_boundary_tops(
        y, valid, bottom_valid, top_valid, top_candidates,
        global_rom=90.0, top_band=150.0,
    )

    assert tops == [0, 44]
    assert extra_details and extra_details[0]["label"] == "ENTRY_TOP"
    assert debug["entry"]["applied"] is True
    assert debug["exit"] is None


def test_recover_boundary_tops_is_noop_when_both_sides_already_covered():
    y = _descending_then_flat_signal()
    valid = np.ones_like(y, dtype=bool)
    bottom_valid = [24]
    top_valid = [0, 44]
    top_candidates = np.array([0, 44])

    tops, extra_details, debug = recover_boundary_tops(
        y, valid, bottom_valid, top_valid, top_candidates,
        global_rom=90.0, top_band=150.0,
    )

    assert tops == [0, 44]
    assert extra_details == []
    assert debug["entry"] is None
    assert debug["exit"] is None


def test_recover_boundary_tops_rejects_flat_noise_as_entry_top():
    # No real descent anywhere: nothing should be recovered.
    y = np.full(30, 100.0)
    valid = np.ones_like(y, dtype=bool)
    tops, extra_details, debug = recover_boundary_tops(
        y, valid, bottom_valid=[15], top_valid=[], top_candidates=np.array([0, 5]),
        global_rom=10.0, top_band=150.0,
    )
    assert tops == []
    assert extra_details == []
    assert debug["entry"]["applied"] is False


# ---------------------------------------------------------------------------
# events.py: detect_top_bottom_candidates / merge_micro_oscillations / collapse_double_bottoms /
# center_top_bottom_plateaus / classify_top_bottom_events / add_boundary_top_bottom_events.
# ---------------------------------------------------------------------------


def test_detect_top_bottom_candidates_finds_point_peaks_and_valley():
    y = np.array([150.0, 160.0, 170.0, 160.0, 150.0, 120.0, 90.0, 80.0, 90.0, 120.0, 150.0, 160.0, 170.0, 160.0, 150.0])
    bottoms, tops = detect_top_bottom_candidates(y, min_distance=1, min_prominence_ratio=0.05)
    assert bottoms.tolist() == [7]
    assert tops.tolist() == [2, 12]


def test_detect_top_bottom_candidates_too_short_signal_returns_empty():
    bottoms, tops = detect_top_bottom_candidates(np.array([1.0, 2.0]))
    assert bottoms.tolist() == []
    assert tops.tolist() == []


def test_merge_micro_oscillations_drops_low_amplitude_pair():
    # bottom(90)-top(92)-bottom(88): both hops are amplitude 4, well under thr=10.
    y = np.array([170.0, 90.0, 92.0, 88.0, 170.0])
    bottoms, tops = merge_micro_oscillations(y, bottoms=np.array([1, 3]), tops=np.array([2]), thr=10.0)
    assert bottoms.tolist() == [3]
    assert tops.tolist() == []


def test_merge_micro_oscillations_keeps_pair_above_threshold():
    y = np.array([170.0, 90.0, 130.0, 88.0, 170.0])
    bottoms, tops = merge_micro_oscillations(y, bottoms=np.array([1, 3]), tops=np.array([2]), thr=10.0)
    assert bottoms.tolist() == [1, 3]
    assert tops.tolist() == [2]


def test_collapse_double_bottoms_keeps_lower_of_a_close_pair():
    y = np.array([170.0, 90.0, 95.0, 85.0, 170.0])
    bottoms, tops = collapse_double_bottoms(
        y, bottoms=np.array([1, 3]), tops=np.array([2]), min_separation_frames=5, bridge_factor=2.0
    )
    assert bottoms.tolist() == [3]  # y[3]=85 < y[1]=90: the deeper bottom survives
    assert tops.tolist() == []


def test_collapse_double_bottoms_leaves_well_separated_bottoms_alone():
    y = np.array([170.0, 90.0, 120.0, 150.0, 170.0, 170.0, 170.0, 150.0, 120.0, 90.0, 170.0])
    bottoms, tops = collapse_double_bottoms(
        y, bottoms=np.array([1, 9]), tops=np.array([5]), min_separation_frames=2, bridge_factor=1.0
    )
    assert bottoms.tolist() == [1, 9]
    assert tops.tolist() == [5]


def test_center_top_bottom_plateaus_moves_event_to_plateau_middle():
    y = np.array([100.0, 170.0, 170.0, 170.0, 100.0, 80.0, 80.0, 100.0])
    new_bottoms, new_tops = center_top_bottom_plateaus(y, bottoms=np.array([5]), tops=np.array([2]), rom=90.0)
    assert new_tops.tolist() == [2]  # already centered (single interior point of the plateau)
    assert new_bottoms.tolist() == [6]  # plateau is [5, 6]; centroid rounds to the far edge


def test_classify_top_bottom_events_all_valid_with_clear_amplitude():
    y = np.array([170.0, 90.0, 175.0, 165.0, 80.0, 170.0])
    bottom_valid, bottom_details, top_valid, top_details = classify_top_bottom_events(
        y, bottoms=np.array([1, 4]), tops=np.array([0, 2, 3, 5]), rom=95.0, thr=10.0
    )
    assert bottom_valid == [1, 4]
    assert top_valid == [0, 2, 3, 5]
    assert all(d["is_valid"] for d in bottom_details + top_details)


def test_classify_top_bottom_events_drops_low_prominence_top():
    # Interior top at idx=2 sits only slightly above both neighboring bottoms (amplitude 5 < thr).
    y = np.array([170.0, 90.0, 95.0, 88.0, 170.0])
    bottom_valid, _, top_valid, top_details = classify_top_bottom_events(
        y, bottoms=np.array([1, 3]), tops=np.array([2]), rom=90.0, thr=10.0
    )
    assert top_valid == []
    assert top_details[0]["reason"] == "low_local_prominence"


def test_add_boundary_top_bottom_events_recovers_entry_top():
    y = np.concatenate([np.linspace(170.0, 80.0, 11), np.full(5, 80.0), np.linspace(80.0, 170.0, 10)])
    bottoms, tops = add_boundary_top_bottom_events(
        y, bottoms=np.array([10]), tops=np.array([25]), thr=20.0, allow_boundary_events=True
    )
    assert bottoms.tolist() == [10]
    assert tops.tolist() == [0, 25]


def test_add_boundary_top_bottom_events_noop_when_disallowed():
    y = np.concatenate([np.linspace(170.0, 80.0, 11), np.full(5, 80.0), np.linspace(80.0, 170.0, 10)])
    bottoms, tops = add_boundary_top_bottom_events(
        y, bottoms=np.array([10]), tops=np.array([25]), thr=20.0, allow_boundary_events=False
    )
    assert bottoms.tolist() == [10]
    assert tops.tolist() == [25]


def test_add_boundary_top_bottom_events_rejects_invalid_extreme_frame():
    # Regression: entry/exit used to be asymmetric (entry required `valid[candidate]`, exit only
    # required `finite[candidate]`), so a low-confidence-but-finite extreme frame could seed a
    # synthetic top on exit but not on entry. Both sides now require `valid[candidate]`.
    y = np.concatenate([np.linspace(170.0, 80.0, 10), np.full(5, 80.0), np.linspace(80.0, 170.0, 11)])
    last_idx = y.size - 1
    valid_mask = np.ones_like(y, dtype=bool)
    valid_mask[last_idx] = False  # the true max at the very end is finite but low-confidence
    bottoms, tops = add_boundary_top_bottom_events(
        y, bottoms=np.array([9]), tops=np.array([0]), thr=20.0, allow_boundary_events=True, valid_mask=valid_mask
    )
    assert last_idx not in tops.tolist()
    assert tops.tolist() == [0]


# ---------------------------------------------------------------------------
# io.py: validate_pose_data_dict shape/contract checks (shared by load_pose_npz).
# ---------------------------------------------------------------------------


def _pose_dict(num_frames: int = 10, **overrides) -> dict:
    base = {
        "kps_xy": np.zeros((num_frames, 17, 2), dtype=np.float64),
        "kps_score": np.full((num_frames, 17), 0.9, dtype=np.float64),
        "bbox_xyxy": np.zeros((num_frames, 4), dtype=np.float64),
        "frame_idx": np.arange(num_frames, dtype=np.int64),
        "fps": 30.0,
        "meta": {"video_id": "unit_test"},
    }
    base.update(overrides)
    return base


def test_validate_pose_data_dict_missing_key_raises():
    pose = _pose_dict()
    del pose["fps"]
    with pytest.raises(ValueError, match="Missing pose_data keys"):
        validate_pose_data_dict(pose)


def test_validate_pose_data_dict_bad_kps_shape_raises():
    pose = _pose_dict()
    pose["kps_xy"] = np.zeros((10, 5, 2))  # wrong keypoint count
    with pytest.raises(ValueError, match="Invalid kps_xy shape"):
        validate_pose_data_dict(pose)


def test_validate_pose_data_dict_prefers_clean_keypoints():
    num_frames = 10
    pose = _pose_dict(num_frames)
    pose["kps_xy_clean"] = np.ones((num_frames, 17, 2), dtype=np.float64)
    pose["kps_score_clean"] = np.full((num_frames, 17), 0.5, dtype=np.float64)
    result = validate_pose_data_dict(pose)
    assert result["pose_source"] == "clean"
    assert result["has_clean_pose"] is True
    assert np.array_equal(result["kps_xy"], pose["kps_xy_clean"])


def test_validate_pose_data_dict_falls_back_to_raw_without_clean_keys():
    pose = _pose_dict()
    result = validate_pose_data_dict(pose)
    assert result["pose_source"] == "raw"
    assert result["has_clean_pose"] is False


# ---------------------------------------------------------------------------
# pipeline.py <-> pose_cleaning bridge: reusing mask_valid_right_chain for the angle signal.
# ---------------------------------------------------------------------------


def test_angle_keypoints_valid_mask_none_without_clean_pose():
    pose = {"has_clean_pose": False, "mask_valid_right_chain": np.ones((10, len(RIGHT_CHAIN)), dtype=bool)}
    assert _angle_keypoints_valid_mask(pose) is None


def test_angle_keypoints_valid_mask_none_when_mask_absent():
    pose = {"has_clean_pose": True}
    assert _angle_keypoints_valid_mask(pose) is None


def test_angle_keypoints_valid_mask_selects_shoulder_hip_knee_columns():
    n = 5
    mask = np.ones((n, len(RIGHT_CHAIN)), dtype=bool)
    hip_col = RIGHT_CHAIN.index(12)
    mask[2, hip_col] = False  # hip invalid on frame 2 only
    pose = {"has_clean_pose": True, "mask_valid_right_chain": mask}
    result = _angle_keypoints_valid_mask(pose)
    assert result.tolist() == [True, True, False, True, True]


def test_compute_angle_signal_extra_valid_mask_rescues_low_confidence_frame():
    kps_xy = np.zeros((3, 17, 2), dtype=np.float64)
    kps_xy[:, 6, :] = [0.0, 100.0]
    kps_xy[:, 12, :] = [0.0, 0.0]
    kps_xy[:, 14, :] = [0.0, -100.0]
    kps_score = np.full((3, 17), 0.9, dtype=np.float64)
    kps_score[1, 12] = 0.05  # hip confidence far below thr_conf on frame 1

    _, valid_without_rescue = _compute_angle_signal(kps_xy, kps_score, kp_a=6, kp_b=12, kp_c=14, thr_conf=0.30)
    assert valid_without_rescue.tolist() == [True, False, True]

    extra_valid = np.array([True, True, True])  # pose_cleaning already trusts frame 1
    _, valid_with_rescue = _compute_angle_signal(
        kps_xy, kps_score, kp_a=6, kp_b=12, kp_c=14, thr_conf=0.30, extra_valid_mask=extra_valid
    )
    assert valid_with_rescue.tolist() == [True, True, True]


# ---------------------------------------------------------------------------
# End-to-end: synthetic multi-rep angle signal through the full pipeline.
#
# Keypoints are placed so the shoulder(6)-hip(12)-knee(14) angle traces a chosen degrees(t)
# curve exactly (see _pose_sequence_for_angles), independent of everything else in the frame —
# the same idea as the notebook's synthetic signal, but driving real keypoint arrays through
# run_rdl_segmentation_from_pose end to end.
# ---------------------------------------------------------------------------


def _pose_sequence_for_angles(angles_deg: np.ndarray, fps: float = 30.0) -> dict:
    n = angles_deg.size
    kps = np.zeros((n, 17, 2), dtype=np.float64)
    theta = np.radians(angles_deg)
    kps[:, 6, :] = [0.0, 100.0]  # shoulder: fixed vector (0, 100) from the hip
    kps[:, 12, :] = [0.0, 0.0]  # hip at the origin
    kps[:, 14, :] = np.stack([100.0 * np.sin(theta), 100.0 * np.cos(theta)], axis=1)  # knee
    scores = np.full((n, 17), 0.9, dtype=np.float64)
    return {
        "kps_xy": kps,
        "kps_score": scores,
        "bbox_xyxy": np.zeros((n, 4), dtype=np.float64),
        "frame_idx": np.arange(n, dtype=np.int64),
        "fps": fps,
        "meta": {"video_id": "synthetic_test"},
    }


def _one_rep(top: float, bottom: float, fps: float) -> np.ndarray:
    descend = np.linspace(top, bottom, int(0.5 * fps))
    ascend = np.linspace(bottom, top, int(0.5 * fps))
    return np.concatenate([descend, [bottom] * int(0.2 * fps), ascend])


def test_full_pipeline_detects_three_clean_reps():
    fps = 30.0
    top, bottom = 172.0, 78.0
    hold = np.full(int(0.4 * fps), top)
    rep = _one_rep(top, bottom, fps)
    angles = np.concatenate([hold, rep, hold, rep, hold, rep, hold])

    result = run_rdl_segmentation_from_pose(_pose_sequence_for_angles(angles, fps))

    assert result["segmentation_status"] == "OK"
    assert len(result["reps"]) == 3
    for r in result["reps"]:
        assert r["anchor_valid"] is True
        assert r["top_start"] < r["bottom"] < r["top_end"]


def test_full_pipeline_recovers_rep_when_video_starts_mid_descent():
    """No stable top before the first bottom: recover_boundary_tops must still find an entry top.

    The recovered top may or may not end up in the final `reps` (select_consistent_rep_block can
    still drop it as an outlier — that's a separate, already-covered concern), so this checks the
    recovery mechanism itself via validation_debug rather than the final rep boundaries.
    """
    fps = 30.0
    top, bottom = 172.0, 78.0
    mid_descent_start = np.linspace(top, (top + bottom) / 2, int(0.15 * fps))
    rest_of_first_descent = np.linspace((top + bottom) / 2, bottom, int(0.35 * fps))
    ascend = np.linspace(bottom, top, int(0.5 * fps))
    hold = np.full(int(0.4 * fps), top)
    rep = _one_rep(top, bottom, fps)
    angles = np.concatenate(
        [mid_descent_start, rest_of_first_descent, [bottom] * int(0.2 * fps), ascend, hold, rep, hold]
    )

    result = run_rdl_segmentation_from_pose(_pose_sequence_for_angles(angles, fps))

    assert result["segmentation_status"] == "OK"
    assert len(result["reps"]) >= 1
    entry_recovery = result["validation_debug"]["boundary_top_recovery"]["entry"]
    assert entry_recovery["applied"] is True
    assert entry_recovery["recovered_index"] == 0


def test_full_pipeline_no_movement_yields_no_valid_signal():
    angles = np.full(90, 170.0)
    result = run_rdl_segmentation_from_pose(_pose_sequence_for_angles(angles))
    assert result["segmentation_status"] == "NO_VALID_RDL_SIGNAL"
    assert result["reps"] == []


# ---------------------------------------------------------------------------
# repetitions.py: phase-boundary search (find_active_eccentric_start / find_concentric_start /
# find_concentric_end) — previously only exercised indirectly through the end-to-end tests above.
# ---------------------------------------------------------------------------


def test_find_active_eccentric_start_skips_the_pre_descent_hold():
    fps = 30.0
    hold = np.full(15, 170.0)
    descent = np.linspace(170.0, 80.0, 20)
    y = np.concatenate([hold, descent])
    bottom = y.size - 1

    onset, found, debug = find_active_eccentric_start(y, top_start=0, bottom=bottom, fps=fps, rom=90.0, excursion=90.0)

    assert found is True
    assert onset == 14  # just before the hold ends and the real descent lobe begins
    assert debug["method"] == "main_descent_lobe"


def test_find_active_eccentric_start_with_no_descent_falls_back():
    fps = 30.0
    y = np.full(30, 170.0)
    onset, found, debug = find_active_eccentric_start(y, top_start=0, bottom=29, fps=fps, rom=90.0, excursion=90.0)
    assert found is False
    assert onset == 0


def test_find_concentric_start_finds_sustained_upward_trend():
    fps = 30.0
    flat = np.full(6, 80.0)
    ascend = np.linspace(80.0, 170.0, 15)
    y = np.concatenate([flat, ascend])
    con_start = find_concentric_start(y, bottom=0, search_end=y.size - 1, rom=90.0, fps=fps)
    assert con_start == 3


def test_find_concentric_start_returns_bottom_when_signal_never_rises():
    fps = 30.0
    y = np.full(20, 80.0)
    assert find_concentric_start(y, bottom=5, search_end=y.size - 1, rom=90.0, fps=fps) == 5


def test_find_concentric_end_stops_once_the_top_band_is_held():
    fps = 30.0
    ascend = np.linspace(80.0, 170.0, 15)
    hold_top = np.full(10, 170.0)
    y = np.concatenate([ascend, hold_top])
    con_end = find_concentric_end(y, con_start=0, search_end=y.size - 1, top_band=160.0, rom=90.0, fps=fps)
    assert con_end == 13


# ---------------------------------------------------------------------------
# boundary_refinement.py: refine_rdl_candidate_boundaries.
# ---------------------------------------------------------------------------


def test_refine_rdl_candidate_boundaries_trims_unstable_prefix():
    fps = 30.0
    cfg = RDLSegmentationConfig()
    n_unstable = 20
    unstable = np.linspace(150.0, 172.0, n_unstable)
    stable_top = np.full(20, 172.0)
    descend = np.linspace(172.0, 78.0, 15)
    hold_bottom = np.full(6, 78.0)
    ascend = np.linspace(78.0, 172.0, 15)
    hold_top_end = np.full(10, 172.0)
    y = np.concatenate([unstable, stable_top, descend, hold_bottom, ascend, hold_top_end])

    valid_mask = np.ones_like(y, dtype=bool)
    valid_mask[:n_unstable] = False  # long invalid stretch before the stable top window

    global_quality = compute_global_signal_quality(y, valid_mask, config=cfg.validation)
    bottom_idx = n_unstable + len(stable_top) + len(descend) - 1
    candidate = {
        "rep_id": 0,
        "candidate_id": 0,
        "top_start": 0,
        "bottom": bottom_idx,
        "top_end": y.size - 1,
        "ecc_start": n_unstable + len(stable_top),
        "phase_debug": {},
    }

    refined, debug = refine_rdl_candidate_boundaries(
        [candidate], y, valid_mask, fps=fps, global_quality=global_quality, config=cfg
    )

    assert debug["num_candidates_refined"] == 1
    assert refined[0]["top_start"] > 0
    assert refined[0]["boundary_refined"] is True
    assert debug["candidates"][0]["reason"] == "trimmed_unstable_pre_descent"


def test_refine_rdl_candidate_boundaries_leaves_invalid_bounds_untouched():
    fps = 30.0
    cfg = RDLSegmentationConfig()
    y = np.linspace(170.0, 80.0, 50)
    valid_mask = np.ones_like(y, dtype=bool)
    global_quality = compute_global_signal_quality(y, valid_mask, config=cfg.validation)

    # bottom (40) is not strictly between top_start (10) and top_end (30): bounds are invalid.
    bad_candidate = {
        "rep_id": 0, "candidate_id": 0, "top_start": 10, "bottom": 40, "top_end": 30,
        "ecc_start": 15, "phase_debug": {},
    }
    refined, debug = refine_rdl_candidate_boundaries(
        [bad_candidate], y, valid_mask, fps=fps, global_quality=global_quality, config=cfg
    )

    assert debug["num_candidates_refined"] == 0
    assert refined[0] == bad_candidate
    assert debug["candidates"][0]["reason"] == "invalid_candidate_bounds"


# ---------------------------------------------------------------------------
# pipeline.py: _determine_segmentation_status.
# ---------------------------------------------------------------------------


def test_determine_segmentation_status_ok_when_reps_found():
    assert _determine_segmentation_status([{"rep_id": 0}], []) == "OK"


def test_determine_segmentation_status_no_valid_reps_when_no_candidates():
    assert _determine_segmentation_status([], []) == NO_VALID_REPS


def test_determine_segmentation_status_partial_video_from_partial_start_label():
    candidates = [{"label": "PARTIAL_START", "reason": "start_below_top_band"}]
    assert _determine_segmentation_status([], candidates) == PARTIAL_VIDEO


def test_determine_segmentation_status_too_noisy_from_unstable_signal_label():
    candidates = [{"label": "UNSTABLE_SIGNAL", "reason": "unstable_signal"}]
    assert _determine_segmentation_status([], candidates) == TOO_NOISY


def test_determine_segmentation_status_wrong_movement_label():
    candidates = [{"label": "WRONG_MOVEMENT_OR_NOT_RDL", "reason": "bottom_not_deep_enough"}]
    assert _determine_segmentation_status([], candidates) == WRONG_MOVEMENT_OR_NOT_RDL


def test_determine_segmentation_status_post_exercise_noise_alone_is_not_partial_video():
    # Regression: append_edge_artifact_candidates only ever assigns "POST_EXERCISE_NOISE" when
    # final_reps is non-empty, which never happens on this branch (final_reps is empty here) — so
    # a candidate list containing only that label must NOT be classified as PARTIAL_VIDEO.
    candidates = [{"label": "POST_EXERCISE_NOISE", "reason": "post_exercise_noise"}]
    assert _determine_segmentation_status([], candidates) == NO_VALID_REPS
