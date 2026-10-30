from __future__ import annotations

from src.biomechanics.rdl.feedback import aggregate_rdl_feedback_evidence
from src.biomechanics.rdl.feedback.aggregation.severity_grouping import severity_rank


def test_severity_rank_total_order():
    order = ("none", "posible", "leve", "media", "grave")
    for i in range(len(order) - 1):
        assert severity_rank(order[i]) < severity_rank(order[i + 1])


def _evidence_item(*, error_code: str, detector: str, severity: str) -> dict:
    return {
        "detected": True,
        "error_code": error_code,
        "detector": detector,
        "severity": severity,
        "user_rep_order": 1,
        "user_rep_raw_index": 0,
        "phase": "eccentric",
        "score": 1.0,
        "anchors": [],
        "frames": [],
        "location_label": "during the descent",
    }


def test_aggregate_issues_sorted_by_severity_desc():
    evidence = {
        "exercise": "RDL",
        "evidence_items": [
            _evidence_item(error_code="hip_hinge", detector="hip_hinge", severity="media"),
            _evidence_item(error_code="knee_dominant", detector="knee_dominant", severity="grave"),
            _evidence_item(error_code="bent_arms", detector="bent_arms", severity="leve"),
            _evidence_item(error_code="spine_flexion_possible", detector="spine_flexion", severity="posible"),
        ],
    }
    agg = aggregate_rdl_feedback_evidence(feedback_evidence=evidence)
    issues = agg.get("issues", [])
    assert len(issues) == 4
    severities = [str(i.get("severity")) for i in issues]
    assert severities == ["grave", "media", "leve", "posible"]
