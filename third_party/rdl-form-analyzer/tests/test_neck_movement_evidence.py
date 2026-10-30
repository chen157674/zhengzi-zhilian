from __future__ import annotations

from src.biomechanics.rdl.feedback.builder.messages import get_message
from src.biomechanics.rdl.feedback.evidence_normalizer.extractors import extract_neck_movement_evidence

_SEGMENT = "ecc_50_to_ecc_75"


def _neck_result(*, segment_subtype: str | None, severity: str = "media") -> dict:
    segment_data: dict = {
        "severity": severity,
        "anchor": "ecc_50",
    }
    if segment_subtype is not None:
        segment_data["subtype"] = segment_subtype
    return {
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


def _single_item(detector_result: dict):
    items = extract_neck_movement_evidence(detector_result)
    assert len(items) == 1
    return items[0]


def test_neck_flexion_down_error_code():
    item = _single_item(_neck_result(segment_subtype="neck_flexion_down"))
    assert item.detector == "neck_movement"
    assert item.error_code == "neck_flexion_down"
    assert item.summary_metrics.get("subtype") == "neck_flexion_down"


def test_neck_extension_up_error_code():
    item = _single_item(_neck_result(segment_subtype="neck_extension_up"))
    assert item.detector == "neck_movement"
    assert item.error_code == "neck_extension_up"


def test_mixed_or_unclear_falls_back_to_neck_movement():
    item = _single_item(_neck_result(segment_subtype="mixed_or_unclear"))
    assert item.error_code == "neck_movement"


def test_missing_subtype_falls_back_to_neck_movement():
    item = _single_item(_neck_result(segment_subtype=None))
    assert item.error_code == "neck_movement"


def test_segment_severity_preserved():
    item = _single_item(_neck_result(segment_subtype="neck_flexion_down", severity="grave"))
    assert item.severity == "grave"


def test_messages_exist_for_specific_neck_codes():
    for code in ("neck_flexion_down", "neck_extension_up"):
        msg = get_message(code)
        assert msg["title"]
        assert "neck" in msg["title"].lower() or "Neck" in msg["title"]
