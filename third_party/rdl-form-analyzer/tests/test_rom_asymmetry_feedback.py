"""Integration: rom / asymmetry detector vs error_code through feedback report."""

from __future__ import annotations

from src.biomechanics.rdl.feedback import (
    aggregate_rdl_feedback_evidence,
    build_rdl_feedback_report,
    normalize_rdl_detector_evidence,
)
from src.biomechanics.rdl.feedback.builder.messages import get_message
from src.biomechanics.rdl.feedback.builder.recommendations import get_recommendation


def _build_report(detector_results: dict) -> dict:
    evidence = normalize_rdl_detector_evidence(detector_results=detector_results)
    aggregation = aggregate_rdl_feedback_evidence(feedback_evidence=evidence)
    return build_rdl_feedback_report(
        feedback_aggregation=aggregation,
        feedback_evidence=evidence,
    )


def _feedback_item(report: dict, error_code: str) -> dict:
    items: list[dict] = []
    for rep in report.get("rep_feedback", []):
        items.extend(rep.get("primary_errors", []))
        items.extend(rep.get("secondary_errors", []))
        items.extend(rep.get("observations", []))
    matches = [i for i in items if i.get("error_code") == error_code]
    assert len(matches) == 1, f"expected one {error_code}, got {[i.get('error_code') for i in items]}"
    return matches[0]


def _assert_copy_matches(item: dict, error_code: str) -> None:
    msg = get_message(error_code)
    assert item["title"] == msg["title"]
    assert item["what_happens"] == msg["what_happens"]
    assert item["how_to_fix"] == get_recommendation(error_code, item)


def test_rom_short_rom_full_pipeline():
    detector_results = {
        "rom": {
            "rep_results": [
                {
                    "detected": True,
                    "user_rep_order": 1,
                    "severity": "media",
                    "score": 4.0,
                    "metrics": {"rom_norm": 0.85},
                }
            ]
        }
    }
    item = _feedback_item(_build_report(detector_results), "short_rom")
    assert item["detector"] == "rom"
    _assert_copy_matches(item, "short_rom")


def test_asymmetry_arms_full_pipeline():
    detector_results = {
        "asymmetry": {
            "rep_results": [
                {
                    "user_rep_order": 1,
                    "arms": {
                        "severity": "media",
                        "magnitude": 2.5,
                        "dominant_phase": "ecc",
                    },
                }
            ]
        }
    }
    item = _feedback_item(_build_report(detector_results), "asymmetry_arms")
    assert item["detector"] == "asymmetry"
    generic = get_message("asymmetry")
    specific = get_message("asymmetry_arms")
    _assert_copy_matches(item, "asymmetry_arms")
    assert item["title"] != generic["title"]
    assert item["title"] == specific["title"]


def test_asymmetry_legs_full_pipeline():
    detector_results = {
        "asymmetry": {
            "rep_results": [
                {
                    "user_rep_order": 1,
                    "legs": {
                        "severity": "media",
                        "magnitude": 1.8,
                        "dominant_phase": "eccentric",
                    },
                }
            ]
        }
    }
    item = _feedback_item(_build_report(detector_results), "asymmetry_legs")
    assert item["detector"] == "asymmetry"
    _assert_copy_matches(item, "asymmetry_legs")
