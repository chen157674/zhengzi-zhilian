"""Characterize pose_clean / NPZ script tails before refactor (no production changes)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from src.pipeline.config import FullAnalysisConfig
from src.pipeline import status as pipeline_status
from src.scripts import run_pipeline as rp
from src.scripts import run_pipeline_json as rpj
from src.scripts import _pose_clean_rdl_tail as rdl_tail
from src.scripts._pose_clean_npz import load_pose_clean_npz
from src.scripts._pose_clean_rdl_tail import run_rdl_success_tail_from_pose_clean

_STUB_FEEDBACK_REPORT = {
    "status": "no_relevant_issues",
    "rep_feedback": [],
    "headline": "stub",
}
_STUB_EVIDENCE = {"exercise": "RDL", "evidence_items": [], "summary": {}, "warnings": []}
_STUB_AGG = {"exercise": "RDL", "issues": [], "summary": {}, "warnings": []}


def _minimal_pose_clean(num_frames: int = 6) -> dict:
    kps = np.zeros((num_frames, 17, 2), dtype=np.float64)
    scores = np.full((num_frames, 17), 0.9, dtype=np.float64)
    return {
        "kps_xy": kps,
        "kps_score": scores,
        "kps_xy_clean": kps,
        "kps_score_clean": scores,
        "bbox_xyxy": np.zeros((num_frames, 4), dtype=np.float64),
        "frame_idx": np.arange(num_frames, dtype=np.int64),
        "fps": 30.0,
        "meta": {"video_id": "char_test"},
        "cleaning_diagnostics": {},
    }


def _stub_segmentation_no_reps(*_args, **_kwargs):
    return {
        "video_id": "char_test",
        "reps": [],
        "summary": {"num_reps_with_valid_anchors": 0, "num_reps": 0},
    }


def _stub_segmentation(*_args, **_kwargs):
    return {
        "video_id": "char_test",
        "reps": [{"user_rep_order": 1}],
        "summary": {"num_reps_with_valid_anchors": 1, "num_reps": 1},
    }


def _stub_normalize(kps, config=None):
    _ = config
    kps = np.asarray(kps, dtype=np.float64)
    n = int(kps.shape[0])
    return {
        "kps_xy_normalized": kps,
        "mask_valid_normalized": np.ones((n, 17), dtype=bool),
        "origins": np.zeros((n, 2), dtype=np.float64),
        "scales": np.ones(n, dtype=np.float64),
        "raw_scales": np.ones(n, dtype=np.float64),
        "method": "pelvis_torso_scale",
        "sequence_scale_mode": "fixed_median",
        "rotation_applied": False,
        "valid_frame_count": n,
        "total_frame_count": n,
        "valid_frame_ratio": 1.0,
        "warnings": [],
    }


def _stub_context(**_kwargs):
    return {"exercise": "RDL", "anchor_pairs": {"num_paired_repetitions": 1}}


def _stub_detectors(_ctx):
    return {"exercise": "RDL", "detectors": {}, "summary": {}, "warnings": []}


def _install_tail_stubs(monkeypatch, module) -> None:
    monkeypatch.setattr(module, "run_rdl_segmentation_from_pose", _stub_segmentation)
    for target in (rdl_tail,):
        monkeypatch.setattr(target, "normalize_pose_sequence", _stub_normalize)
        monkeypatch.setattr(target, "build_rdl_analysis_context", _stub_context)
        monkeypatch.setattr(target, "run_rdl_detectors", _stub_detectors)
        monkeypatch.setattr(target, "normalize_rdl_detector_evidence", lambda **_kw: dict(_STUB_EVIDENCE))
        monkeypatch.setattr(target, "aggregate_rdl_feedback_evidence", lambda **_kw: dict(_STUB_AGG))
        monkeypatch.setattr(target, "build_rdl_feedback_report", lambda **_kw: dict(_STUB_FEEDBACK_REPORT))


def test_run_from_npz_nominal_pipeline_result_and_feedback(monkeypatch):
    pose_clean = _minimal_pose_clean()
    monkeypatch.setattr(rpj, "_load_pose_clean_npz", lambda _p: pose_clean)
    _install_tail_stubs(monkeypatch, rpj)

    npz_path = Path("/tmp/char_test_rtmpose_clean.npz")
    config = FullAnalysisConfig(video_path=npz_path)
    runtime = rpj._run_from_npz(npz_path, config)

    pr = runtime.get("pipeline_result")
    assert isinstance(pr, dict)
    assert pr.get("status") == "ok"
    assert pr.get("ok") is True
    assert isinstance(pr.get("feedback"), dict)
    assert pr["feedback"].get("headline") == "stub"
    assert runtime.get("feedback_report") == _STUB_FEEDBACK_REPORT
    assert runtime.get("analysis_context") is not None


def test_run_from_pose_clean_success_includes_pipeline_result(monkeypatch):
    _install_tail_stubs(monkeypatch, rp)
    pose_clean = _minimal_pose_clean()
    video_path = Path("/tmp/char_video.mp4")

    runtime = rp._run_from_pose_clean(video_path, pose_clean, source_note="test_stub")

    assert isinstance(runtime.get("feedback_report"), dict)
    assert runtime["feedback_report"].get("headline") == "stub"
    pr = runtime.get("pipeline_result")
    assert isinstance(pr, dict)
    assert pr.get("status") == "ok"
    assert pr.get("ok") is True
    assert isinstance(pr.get("feedback"), dict)
    assert pr["feedback"].get("headline") == "stub"


def test_run_from_npz_no_reps_shape_is_preserved(monkeypatch, tmp_path):
    npz_path = tmp_path / "char_test_rtmpose_clean.npz"
    _write_minimal_clean_npz(npz_path)
    monkeypatch.setattr(rpj, "run_rdl_segmentation_from_pose", _stub_segmentation_no_reps)

    config = FullAnalysisConfig(video_path=npz_path)
    runtime = rpj._run_from_npz(npz_path, config)

    assert set(runtime.keys()) == {"npz_path", "pose_clean", "segmentation_result", "pipeline_result"}
    assert runtime["npz_path"] == npz_path
    assert isinstance(runtime["pose_clean"], dict)
    assert isinstance(runtime["segmentation_result"], dict)

    pr = runtime["pipeline_result"]
    assert isinstance(pr, dict)
    assert pr["status"] == pipeline_status.NO_REPS_DETECTED
    assert pr["ok"] is False
    assert "npz_path" in pr.get("artifacts", {})
    assert pr["artifacts"]["npz_path"] == str(npz_path)
    assert pr.get("technical_warnings") == []


def test_run_from_pose_clean_no_reps_shape_is_preserved(monkeypatch):
    monkeypatch.setattr(rp, "run_rdl_segmentation_from_pose", _stub_segmentation_no_reps)
    pose_clean = _minimal_pose_clean()
    video_path = Path("/tmp/char_video_no_reps.mp4")

    runtime = rp._run_from_pose_clean(video_path, pose_clean, source_note="test_no_reps")

    expected_keys = {
        "video_path",
        "processed_video_path",
        "pose_raw",
        "pose_clean",
        "segmentation_result",
        "user_pose_sequence_normalization",
        "analysis_context",
        "detector_results",
        "feedback_evidence",
        "feedback_aggregation",
        "feedback_report",
        "pipeline_result",
        "orientation",
        "pose_meta",
        "cleaning_diagnostics",
    }
    assert set(runtime.keys()) == expected_keys
    assert runtime["video_path"] == str(video_path)
    assert runtime["processed_video_path"] == str(video_path)
    assert runtime["user_pose_sequence_normalization"] == {}
    assert runtime["analysis_context"] == {}
    assert runtime["feedback_report"] == {}

    pr = runtime["pipeline_result"]
    assert isinstance(pr, dict)
    assert pr["status"] == pipeline_status.NO_REPS_DETECTED
    assert pr["ok"] is False
    artifacts = pr.get("artifacts", {})
    assert artifacts.get("video_path") == str(video_path)
    assert artifacts.get("processed_video_path") == str(video_path)
    assert pr.get("technical_warnings") == ["segmentation_no_valid_repetitions"]


def test_run_rdl_success_tail_from_pose_clean_returns_ok_pipeline_result(monkeypatch):
    monkeypatch.setattr(rdl_tail, "normalize_pose_sequence", _stub_normalize)
    monkeypatch.setattr(rdl_tail, "build_rdl_analysis_context", _stub_context)
    monkeypatch.setattr(rdl_tail, "run_rdl_detectors", _stub_detectors)
    monkeypatch.setattr(rdl_tail, "normalize_rdl_detector_evidence", lambda **_kw: dict(_STUB_EVIDENCE))
    monkeypatch.setattr(rdl_tail, "aggregate_rdl_feedback_evidence", lambda **_kw: dict(_STUB_AGG))
    monkeypatch.setattr(rdl_tail, "build_rdl_feedback_report", lambda **_kw: dict(_STUB_FEEDBACK_REPORT))

    pose_clean = _minimal_pose_clean()
    segmentation_result = _stub_segmentation()
    config = FullAnalysisConfig(video_path=Path("/tmp/tail_unit.mp4"))

    tail = run_rdl_success_tail_from_pose_clean(
        pose_clean,
        segmentation_result,
        config=config,
        kps_for_norm=pose_clean["kps_xy_clean"],
        pose_source="test_pose_source",
        source_note="unit_test",
        video_id_fallback="fallback_vid",
        artifacts={"npz_path": "/tmp/tail_unit.npz"},
    )

    assert tail.feedback_report == _STUB_FEEDBACK_REPORT
    assert tail.pipeline_result["status"] == pipeline_status.OK
    assert tail.pipeline_result["ok"] is True
    assert tail.pipeline_result["feedback"]["headline"] == "stub"
    assert tail.user_pose_sequence_normalization["pose_source"] == "test_pose_source"
    assert tail.user_pose_sequence_normalization["meta"]["source_note"] == "unit_test"


def _write_minimal_clean_npz(
    npz_path: Path,
    n: int = 4,
    *,
    meta_payload: dict | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    kps = np.arange(n * 17 * 2, dtype=np.float64).reshape(n, 17, 2)
    scores = np.full((n, 17), 0.5, dtype=np.float64)
    frame_idx = np.arange(n, dtype=np.int64)
    meta_dict = {"extra": 1}
    if meta_payload:
        meta_dict = {**meta_dict, **meta_payload}
    np.savez(
        npz_path,
        kps_xy_clean=kps,
        kps_score_clean=scores,
        bbox_xyxy=np.zeros((n, 4)),
        frame_idx=frame_idx,
        fps=np.array([25.0]),
        meta=np.array([meta_dict], dtype=object),
    )
    return kps, scores


def test_load_pose_clean_npz_shared_explicit_video_id_overrides_stem(tmp_path):
    npz_path = tmp_path / "clip_rtmpose_clean.npz"
    kps, _ = _write_minimal_clean_npz(npz_path)
    loaded = load_pose_clean_npz(npz_path, video_id="explicit_id")
    assert loaded["meta"]["video_id"] == "explicit_id"
    np.testing.assert_array_equal(loaded["kps_xy_clean"], kps)


def test_load_pose_clean_npz_explicit_video_id_overrides_stale_npz_meta(tmp_path):
    npz_path = tmp_path / "clip_rtmpose_clean.npz"
    _write_minimal_clean_npz(npz_path, meta_payload={"video_id": "stale_meta_id"})
    loaded = load_pose_clean_npz(npz_path, video_id="explicit_id")
    assert loaded["meta"]["video_id"] == "explicit_id"
    assert loaded["meta"].get("extra") == 1


def test_load_pose_clean_npz_inferred_stem_overrides_stale_npz_meta(tmp_path):
    npz_path = tmp_path / "clip_rtmpose_clean.npz"
    _write_minimal_clean_npz(npz_path, meta_payload={"video_id": "stale_meta_id"})
    loaded = load_pose_clean_npz(npz_path)
    assert loaded["meta"]["video_id"] == "clip"


def test_load_pose_clean_npz_shared_infers_video_id_from_stem(tmp_path):
    npz_path = tmp_path / "clip_rtmpose_clean.npz"
    _write_minimal_clean_npz(npz_path)
    loaded = load_pose_clean_npz(npz_path)
    assert loaded["meta"]["video_id"] == "clip"


def test_load_pose_clean_npz_equivalent_arrays_between_scripts(tmp_path):
    npz_path = tmp_path / "clip_rtmpose_clean.npz"
    kps, scores = _write_minimal_clean_npz(npz_path)

    from_json = rpj._load_pose_clean_npz(npz_path)
    from_pipeline = rp._load_pose_clean_npz(npz_path, video_id="clip")

    np.testing.assert_array_equal(from_json["kps_xy_clean"], kps)
    np.testing.assert_array_equal(from_pipeline["kps_xy_clean"], kps)
    np.testing.assert_array_equal(from_json["kps_score_clean"], scores)
    assert from_json["fps"] == pytest.approx(25.0)
    assert from_pipeline["fps"] == pytest.approx(25.0)
    assert from_json["meta"]["video_id"] == "clip"
    assert from_pipeline["meta"]["video_id"] == "clip"
