"""Regression guard for the double filter in rep_feedback (observations and 'leve')."""

from __future__ import annotations

from src.biomechanics.rdl.feedback import (
    aggregate_rdl_feedback_evidence,
    build_rdl_feedback_report,
    normalize_rdl_detector_evidence,
)


def _build_report(detector_results: dict) -> dict:
    evidence = normalize_rdl_detector_evidence(detector_results=detector_results)
    aggregation = aggregate_rdl_feedback_evidence(feedback_evidence=evidence)
    return build_rdl_feedback_report(
        feedback_aggregation=aggregation,
        feedback_evidence=evidence,
    )


def _all_items(rep: dict) -> list[dict]:
    return list(rep.get("primary_errors", [])) + list(rep.get("secondary_errors", [])) + list(rep.get("observations", []))


def test_spine_possible_appears_as_observation():
    detector_results = {
        "spine_flexion": {
            "rep_results": [
                {
                    "detected": False,
                    "user_rep_order": 1,
                    "possible_segments": ["ecc_50_to_ecc_75"],
                    "per_segment": {
                        "ecc_50_to_ecc_75": {"torso_low_severity": "grave", "torso_low_norm": 0.5}
                    },
                }
            ]
        }
    }
    report = _build_report(detector_results)
    rep_feedback = report.get("rep_feedback", [])
    assert len(rep_feedback) == 1
    observations = rep_feedback[0].get("observations", [])
    codes = [o.get("error_code") for o in observations]
    assert "spine_flexion_possible" in codes, f"observaciones perdidas: {codes}"


def test_leve_only_rep_not_dropped():
    detector_results = {
        "rom": {
            "rep_results": [
                {
                    "detected": True,
                    "user_rep_order": 1,
                    "severity": "leve",
                    "score": 1.0,
                    "metrics": {"rom_norm": 0.95},
                }
            ]
        }
    }
    report = _build_report(detector_results)
    rep_feedback = report.get("rep_feedback", [])
    assert len(rep_feedback) == 1, "una rep solo-leve no debe descartarse"
    codes = [i.get("error_code") for i in _all_items(rep_feedback[0])]
    assert "short_rom" in codes, f"error leve perdido: {codes}"


def test_spine_segment_not_double_emitted():
    """A triggered + possible segment must not emit spine_flexion and spine_flexion_possible at once."""
    detector_results = {
        "spine_flexion": {
            "rep_results": [
                {
                    "detected": True,
                    "user_rep_order": 1,
                    "triggered_segments": ["ecc_50_to_ecc_75"],
                    "possible_segments": ["ecc_50_to_ecc_75"],
                    "per_segment": {
                        "ecc_50_to_ecc_75": {"torso_low_severity": "grave", "torso_low_norm": 0.6}
                    },
                }
            ]
        }
    }
    report = _build_report(detector_results)
    rep_feedback = report.get("rep_feedback", [])
    assert len(rep_feedback) == 1
    codes = [i.get("error_code") for i in _all_items(rep_feedback[0])]
    assert codes.count("spine_flexion_possible") == 0, f"no debe duplicar como posible: {codes}"
