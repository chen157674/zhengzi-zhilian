# Fixed copy per error_code: title, what happens, why it matters (English, no logic).

from __future__ import annotations


_MESSAGES: dict[str, dict[str, str]] = {
    "bent_arms": {
        "title": "Bent arms",
        "what_happens": "During the rep, your elbows do not stay fully extended.",
        "why_it_matters": "In the RDL your arms should act like straps. Bending them can change bar path and add unnecessary tension.",
    },
    "asymmetry": {
        "title": "Asymmetry during the movement",
        "what_happens": "A meaningful difference between left and right sides shows up during the rep.",
        "why_it_matters": "Persistent asymmetry may indicate compensation, loss of stability, or uneven loading.",
    },
    "asymmetry_arms": {
        "title": "Arm asymmetry",
        "what_happens": "Your arms do not behave symmetrically during the rep.",
        "why_it_matters": "This can alter bar path and make the lift less stable.",
    },
    "asymmetry_legs": {
        "title": "Leg asymmetry",
        "what_happens": "Your legs do not behave symmetrically during the rep.",
        "why_it_matters": "This may indicate uneven load distribution or side-to-side compensation.",
    },
    "bar_far": {
        "title": "Bar too far from the body",
        "what_happens": "The bar or your hands drift too far from your body during the movement.",
        "why_it_matters": "The farther the bar sits from you, the longer the lever arm and the harder it is to keep an efficient, stable path.",
    },
    "hip_hinge": {
        "title": "Insufficient hip hinge",
        "what_happens": "On the way down, you do not push your hips back enough relative to the reference.",
        "why_it_matters": "The Romanian deadlift should be hip-dominant. Too little hinge reduces posterior-chain stimulus and moves technique toward a less efficient pattern.",
    },
    "knee_dominant": {
        "title": "Excessive knee bend",
        "what_happens": "On the way down, you bend your knees more than expected for a Romanian deadlift.",
        "why_it_matters": "This shifts the pattern toward a partial squat and reduces emphasis on the hip hinge.",
    },
    "lockout": {
        "title": "Incomplete lockout at the top",
        "what_happens": "At the end of the rep, you do not fully return to the top position.",
        "why_it_matters": "An incomplete lockout may mean poor finish control or a rep that was not completed properly.",
    },
    "neck_movement": {
        "title": "Unwanted neck movement",
        "what_happens": "During the rep, your neck drifts away from a neutral position relative to your torso.",
        "why_it_matters": "Losing cervical alignment can reduce pattern stability and overall technical quality.",
    },
    "neck_flexion_down": {
        "title": "Neck flexed downward",
        "what_happens": "During the rep, your head drops too far down relative to your torso.",
        "why_it_matters": "This can break cervical alignment and make the movement less stable.",
    },
    "neck_extension_up": {
        "title": "Neck extended upward",
        "what_happens": "During the rep, your head tilts too far up relative to your torso.",
        "why_it_matters": "This breaks head–torso alignment and can affect neck position during the RDL.",
    },
    "short_rom": {
        "title": "Insufficient range of motion",
        "what_happens": "You do not reach enough depth on the way down compared with the reference.",
        "why_it_matters": "A shorter range can limit technical and muscular stimulus, especially for hamstrings and glutes.",
    },
    "spine_flexion": {
        "title": "Possible loss of torso position",
        "what_happens": (
            "A pattern consistent with losing torso position on the way down was observed. "
            "This assessment is based on torso position relative to the reference, not direct measurement "
            "of the spine."
        ),
        "why_it_matters": "A 2D side camera cannot confirm true spinal flexion, but this pattern may indicate you are losing torso position.",
    },
    "spine_flexion_possible": {
        "title": "Possible loss of torso control",
        "what_happens": (
            "Your torso drops more than expected in several segments of the descent, but another active error "
            "in the same area prevents confirmation. After fixing that error and re-analyzing, trunk assessment "
            "can be more accurate."
        ),
        "why_it_matters": "Uncontrolled trunk flexion during the deadlift can increase loading on the lower back.",
    },
}


def get_message(error_code: str) -> dict[str, str]:
    """Return title / what_happens / why_it_matters for an error code."""
    code = str(error_code or "")
    if code in _MESSAGES:
        return dict(_MESSAGES[code])
    return {
        "title": code if code else "technical_error",
        "what_happens": "A technical deviation associated with this pattern was detected.",
        "why_it_matters": "Worth reviewing because it can affect execution quality.",
    }
