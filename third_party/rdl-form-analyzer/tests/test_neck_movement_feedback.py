"""Integration: neck subtype → evidence → aggregate → feedback report copy."""

from __future__ import annotations

from src.biomechanics.rdl.feedback import (
    aggregate_rdl_feedback_evidence,
    build_rdl_feedback_report,
    normalize_rdl_detector_evidence,
)
from src.biomechanics.rdl.feedback.builder.messages import get_message
from src.biomechanics.rdl.feedback.builder.recommendations import get_recommendation

_SEGMENT = "ecc_50_to_ecc_75"


def _detector_results(*, segment_subtype: str | None, severity: str = "media") -> dict:
    segment_data: dict = {"severity": severity, "anchor": "ecc_50"}
    if segment_subtype is not None:
        segment_data["subtype"] = segment_subtype
    return {
        "neck_movement": {
            "rep_results": [
                {
                    "detected": True,
                    "user_rep_order": 1,
                    "severity": severity,
                    "failed_segments": [_SEGMENT],
                    "segment_results": {_SEGMENT: segment_data},
                    "score": 7.5,
                }
            ]
        }
    }


def _build_report(segment_subtype: str | None, severity: str = "media") -> dict:
    detector_results = _detector_results(segment_subtype=segment_subtype, severity=severity)
    evidence = normalize_rdl_detector_evidence(detector_results=detector_results)
    aggregation = aggregate_rdl_feedback_evidence(feedback_evidence=evidence)
    return build_rdl_feedback_report(
        feedback_aggregation=aggregation,
        feedback_evidence=evidence,
    )


def _primary_error(report: dict) -> dict:
    rep_feedback = report.get("rep_feedback", [])
    assert len(rep_feedback) == 1
    primary = rep_feedback[0].get("primary_errors", [])
    assert len(primary) == 1
    return primary[0]


def test_flexion_down_full_pipeline_uses_specific_copy():
    report = _build_report("neck_flexion_down")
    item = _primary_error(report)
    generic = get_message("neck_movement")
    specific = get_message("neck_flexion_down")

    assert item["detector"] == "neck_movement"
    assert item["error_code"] == "neck_flexion_down"
    assert item["title"] == specific["title"]
    assert item["what_happens"] == specific["what_happens"]
    assert item["title"] != generic["title"]
    assert item["how_to_fix"] == get_recommendation("neck_flexion_down", item)


def test_extension_up_full_pipeline_uses_specific_copy():
    report = _build_report("neck_extension_up")
    item = _primary_error(report)
    generic = get_message("neck_movement")
    specific = get_message("neck_extension_up")

    assert item["detector"] == "neck_movement"
    assert item["error_code"] == "neck_extension_up"
    assert item["title"] == specific["title"]
    assert item["what_happens"] == specific["what_happens"]
    assert item["title"] != generic["title"]
    assert item["how_to_fix"] == get_recommendation("neck_extension_up", item)


def test_mixed_or_unclear_full_pipeline_uses_generic_copy():
    report = _build_report("mixed_or_unclear")
    item = _primary_error(report)
    generic = get_message("neck_movement")

    assert item["error_code"] == "neck_movement"
    assert item["detector"] == "neck_movement"
    assert item["title"] == generic["title"]
    assert item["what_happens"] == generic["what_happens"]
