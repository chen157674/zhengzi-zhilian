"""Robustness of _extract_rep_feedback / _dedup_errors_by_code (results UI).

components.py imports flet, which is not in the test venv, so we replace it
with a stub before importing the module. We only test pure logic.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("flet", MagicMock())

from src.app import components  # noqa: E402


def test_dedup_keeps_worst_severity_per_code():
    items = [
        {"error_code": "spine_flexion", "severity": "leve"},
        {"error_code": "spine_flexion", "severity": "grave"},
        {"error_code": "neck", "severity": "media"},
    ]
    errors, max_sev = components._dedup_errors_by_code(items)
    assert max_sev == "grave"
    codes = {e["error_code"]: e["severity"] for e in errors}
    assert codes["spine_flexion"] == "grave"
    assert codes["neck"] == "media"
    # sorted by descending severity
    assert errors[0]["error_code"] == "spine_flexion"


def test_dedup_tolerates_non_dict_and_empty_codes():
    items = ["oops", None, 5, {"error_code": "", "severity": "grave"}, {"error_code": "none"}]
    errors, max_sev = components._dedup_errors_by_code(items)
    assert errors == []
    assert max_sev == "none"


def test_extract_rep_feedback_fallback_skips_non_dict_reps():
    result = {
        "feedback": {
            "rep_feedback": [
                "not-a-dict",
                {
                    "user_rep_order": 1,
                    "primary_errors": [{"error_code": "neck", "severity": "media"}],
                    "secondary_errors": "not-a-list",
                    "observations": [{"error_code": "spine_flexion_possible", "severity": "posible"}],
                },
            ]
        }
    }
    out = components._extract_rep_feedback(result)
    assert len(out) == 1
    assert out[0]["user_rep_order"] == 1
    assert out[0]["max_severity"] == "media"


def test_extract_rep_feedback_bad_feedback_returns_empty():
    assert components._extract_rep_feedback({"feedback": "nope"}) == []
    assert components._extract_rep_feedback({}) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
