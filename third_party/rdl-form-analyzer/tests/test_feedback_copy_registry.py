"""Active feedback error_code values must have registered message and recommendation copy."""

from __future__ import annotations

from src.biomechanics.rdl.feedback.builder.messages import get_message
from src.biomechanics.rdl.feedback.builder.recommendations import get_recommendation

ACTIVE_FEEDBACK_ERROR_CODES = frozenset({
    "bent_arms",
    "bar_far",
    "hip_hinge",
    "knee_dominant",
    "lockout",
    "short_rom",
    "asymmetry_arms",
    "asymmetry_legs",
    "neck_movement",
    "neck_flexion_down",
    "neck_extension_up",
    "spine_flexion",
    "spine_flexion_possible",
})

_DEFAULT_RECOMMENDATION_PREFIX = "Review this technical point"

_REMOVED_SPINE_NO_HIP_TITLE = "Torso lower than expected without hinge cause"


def test_active_error_codes_have_message_and_recommendation():
    for code in sorted(ACTIVE_FEEDBACK_ERROR_CODES):
        msg = get_message(code)
        assert msg["title"].strip(), code
        assert msg["what_happens"].strip(), code
        assert msg["title"] != code, f"{code} should use a registered title, not fallback"
        reco = get_recommendation(code, {})
        assert isinstance(reco, str) and reco.strip(), code
        assert not reco.startswith(_DEFAULT_RECOMMENDATION_PREFIX), code


def test_spine_flexion_no_hip_failure_not_in_static_copy():
    msg = get_message("spine_flexion_no_hip_failure")
    assert msg["title"] != _REMOVED_SPINE_NO_HIP_TITLE
    assert msg["title"] == "spine_flexion_no_hip_failure"
    reco = get_recommendation("spine_flexion_no_hip_failure", {})
    assert reco.startswith(_DEFAULT_RECOMMENDATION_PREFIX)
