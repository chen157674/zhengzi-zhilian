"""Block 18C: shared severity helper and protection of 'posible' in the cross-detector aggregator."""

from __future__ import annotations

import src.biomechanics.rdl.detectors.pipeline as pipeline_mod
from src.biomechanics.rdl.severity import SEVERITY_RANK, max_severity, severity_rank


# --------------------------------------------------------------------------- #
# 1) Helper unit tests.
# --------------------------------------------------------------------------- #
def test_severity_rank_total_order():
    assert (
        severity_rank("none")
        < severity_rank("posible")
        < severity_rank("leve")
        < severity_rank("media")
        < severity_rank("grave")
    )


def test_severity_rank_unknown_and_none_map_to_zero():
    assert severity_rank(None) == 0
    assert severity_rank("unknown") == 0
    assert severity_rank("") == 0


def test_severity_rank_is_case_insensitive():
    assert severity_rank("GRAVE") == severity_rank("grave")


def test_severity_rank_matches_canonical_table():
    assert SEVERITY_RANK == {"none": 0, "posible": 1, "leve": 2, "media": 3, "grave": 4}


def test_max_severity_basic():
    assert max_severity(["none", "posible"]) == "posible"
    assert max_severity(["leve", "posible", "grave"]) == "grave"
    assert max_severity(["media", "leve"]) == "media"


def test_max_severity_empty_and_unknown_default_to_none():
    assert max_severity([]) == "none"
    assert max_severity(["unknown", None]) == "none"


# --------------------------------------------------------------------------- #
# 2) Cross-detector aggregator: run_rdl_detectors uses the helper correctly.
# --------------------------------------------------------------------------- #
_DETECTOR_PATCH_TARGETS = (
    "detect_bent_arms",
    "detect_asymmetry",
    "detect_bar_far",
    "detect_hip_hinge",
    "detect_knee_dominant_error",
    "detect_no_lockout_error",
    "detect_neck_movement_error",
    "detect_short_rom_error",
    "detect_spine_flexion_error",
)


def _fake_detector_result(name: str, severity: str) -> dict:
    return {
        "detector": name,
        "detected": severity != "none",
        "evaluable": True,
        "severity": severity,
        "score": 0.0,
        "num_reps_analyzed": 1,
        "num_reps_detected": 1 if severity != "none" else 0,
        "rep_results": [],
        "warnings": [],
    }


def test_run_rdl_detectors_max_severity_preserves_posible(monkeypatch):
    # One detector emits "posible", the rest "none": the cross-detector summary
    # must NOT downgrade "posible" to "none".
    severities = {
        "detect_asymmetry": "posible",
    }
    for target in _DETECTOR_PATCH_TARGETS:
        sev = severities.get(target, "none")

        def _make(sev_value: str):
            def _fn(*_args, **_kwargs):
                return _fake_detector_result("mock", sev_value)
            return _fn

        monkeypatch.setattr(pipeline_mod, target, _make(sev))

    result = pipeline_mod.run_rdl_detectors({})
    assert result["summary"]["max_severity"] == "posible"


def test_run_rdl_detectors_max_severity_picks_worst(monkeypatch):
    severities = {
        "detect_asymmetry": "posible",
        "detect_hip_hinge": "media",
        "detect_bent_arms": "leve",
    }
    for target in _DETECTOR_PATCH_TARGETS:
        sev = severities.get(target, "none")

        def _make(sev_value: str):
            def _fn(*_args, **_kwargs):
                return _fake_detector_result("mock", sev_value)
            return _fn

        monkeypatch.setattr(pipeline_mod, target, _make(sev))

    result = pipeline_mod.run_rdl_detectors({})
    assert result["summary"]["max_severity"] == "media"


# --------------------------------------------------------------------------- #
# 3) Regression guard: "posible" does not rank the same as "none".
# --------------------------------------------------------------------------- #
def test_posible_is_strictly_above_none():
    assert severity_rank("posible") > severity_rank("none")
    assert max_severity(["none", "posible", "none"]) == "posible"
