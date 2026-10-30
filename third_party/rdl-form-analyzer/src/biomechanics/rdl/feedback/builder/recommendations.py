# Correction tips per error_code (and neck variants); used in how_to_fix on the report.

from __future__ import annotations

from typing import Any


_RECOMMENDATIONS: dict[str, str] = {
    "neck_extension_up": "On the way down, keep your head aligned with your torso. Avoid looking straight ahead or up; aim your gaze slightly toward the floor, following trunk angle.",
    "neck_flexion_down": "Keep your neck neutral and avoid letting your head drop. Think about lengthening the back of your neck and looking at a spot on the floor a few meters ahead.",
    "hip_hinge": "Start the descent by pushing your hips back, not only folding at the torso. Keep a slight knee bend and build tension in your hamstrings.",
    "knee_dominant": "Reduce knee bend on the way down. Think hips back and shins relatively vertical.",
    "bar_far": "Keep the bar or your hands close to your body throughout. Let the bar travel near your thighs and shins.",
    "bent_arms": "Keep elbows extended and arms relaxed. Let your arms hang from the bar without pulling with your biceps.",
    "lockout": "Finish each rep in a strong top position: hips extended, torso upright and controlled, without hyperextending your back.",
    "short_rom": "Let your hips travel back far enough for useful range while keeping hamstring tension and a neutral spine. Do not cut the descent unless you lose position.",
    "asymmetry": "Balance weight evenly across both feet. Check setup, grip, and that the bar stays centered.",
    "asymmetry_arms": "Make sure both arms hang equally and you are not pulling harder on one side. Keep shoulders and hands balanced.",
    "asymmetry_legs": "Check foot placement and pressure into the floor. Push symmetrically with both legs.",
    "spine_flexion": "Reduce range if you lose torso position. Before going deeper, set your rib cage, brace your core, and push hips back without collapsing the torso.",
    "spine_flexion_possible": (
        "Fix the main error identified in that part of the descent and record again. "
        "Practice bracing your core before you start down. "
        "Keep your back in a neutral position through the whole descent."
    ),
    "neck_movement": "Keep a neutral neck position throughout the rep. Follow trunk angle without excessive head movement up or down.",
}


def get_recommendation(error_code: str, issue: dict[str, Any]) -> str:
    """Return practical how-to-fix text for an error_code (issue dict reserved for future use)."""
    _ = issue
    code = str(error_code or "")
    return _RECOMMENDATIONS.get(
        code,
        "Review this technical point and repeat the movement with more control, prioritizing stability and useful range.",
    )
