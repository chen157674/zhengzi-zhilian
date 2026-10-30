from __future__ import annotations

from src.app.result_filters import filter_reps


def _one_rep(*errors: dict) -> list[dict]:
    return [
        {
            "user_rep_order": 1,
            "max_severity": "media",
            "errors": list(errors),
        }
    ]


def _kept_one(rep_feedback: list[dict], sev_filter: str, det_filter: str) -> bool:
    out = filter_reps(rep_feedback, sev_filter, det_filter)
    return len(out) == 1 and len(out[0]["errors"]) == 1


def test_filter_asymmetry_by_detector_not_error_code():
    rep = _one_rep(
        {"detector": "asymmetry", "error_code": "asymmetry_arms", "severity": "media"},
    )
    assert _kept_one(rep, "all", "asymmetry")


def test_filter_rom_short_rom_by_detector():
    rep = _one_rep(
        {"detector": "rom", "error_code": "short_rom", "severity": "leve"},
    )
    assert _kept_one(rep, "all", "rom")


def test_filter_spine_flexion_possible():
    rep = _one_rep(
        {
            "detector": "spine_flexion",
            "error_code": "spine_flexion_possible",
            "severity": "posible",
        },
    )
    assert _kept_one(rep, "all", "spine_flexion")


def test_filter_hip_hinge_one_to_one():
    rep = _one_rep(
        {"detector": "hip_hinge", "error_code": "hip_hinge", "severity": "grave"},
    )
    assert _kept_one(rep, "all", "hip_hinge")


def test_filter_excludes_wrong_detector():
    rep = _one_rep(
        {"detector": "rom", "error_code": "short_rom", "severity": "leve"},
    )
    assert filter_reps(rep, "all", "hip_hinge") == []


def test_filter_fallback_error_code_without_detector():
    rep = _one_rep({"error_code": "hip_hinge", "severity": "media"})
    assert _kept_one(rep, "all", "hip_hinge")


def test_filter_severity_and_detector_combined():
    rep = _one_rep(
        {"detector": "rom", "error_code": "short_rom", "severity": "leve"},
        {"detector": "rom", "error_code": "short_rom", "severity": "grave"},
    )
    out = filter_reps(rep, "grave", "rom")
    assert len(out) == 1
    assert len(out[0]["errors"]) == 1
    assert out[0]["errors"][0]["severity"] == "grave"


def test_sev_filter_excludes_below_threshold():
    rep = _one_rep(
        {"detector": "hip_hinge", "error_code": "hip_hinge", "severity": "leve"},
        {"detector": "hip_hinge", "error_code": "hip_hinge", "severity": "grave"},
    )
    out = filter_reps(rep, "grave", "all")
    assert len(out) == 1
    assert len(out[0]["errors"]) == 1
    assert out[0]["errors"][0]["severity"] == "grave"
