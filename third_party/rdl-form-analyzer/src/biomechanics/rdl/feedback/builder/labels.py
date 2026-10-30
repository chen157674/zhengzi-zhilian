# "Where" copy and humanized phase, severity, and rep labels for the report.

from __future__ import annotations

from typing import Any


_ANCHOR_WHERE_TEXT: dict[str, str] = {
    "ecc_0": "at the start of the descent",
    "ecc_25": "early in the descent",
    "ecc_50": "mid descent",
    "ecc_75": "late in the descent",
    "ecc_100": "at the bottom position",
    "bottom": "at the bottom position",
    "con_0": "at the bottom position",
    "con_25": "early in the ascent",
    "con_50": "mid ascent",
    "con_75": "late in the ascent",
    "con_100": "at lockout",
    "end": "at lockout",
}

_ANCHOR_ORDER: dict[str, int] = {
    "ecc_0": 0,
    "ecc_25": 1,
    "ecc_50": 2,
    "ecc_75": 3,
    "ecc_100": 4,
    "bottom": 4,
    "con_0": 4,
    "con_25": 5,
    "con_50": 6,
    "con_75": 7,
    "con_100": 8,
    "end": 8,
}

_FAILED_SEGMENT_TEXT: dict[str, str] = {
    "ecc_0_to_ecc_25": "early in the descent",
    "ecc_25_to_ecc_50": "mid descent",
    "ecc_50_to_ecc_75": "between mid and late descent",
    "ecc_75_to_ecc_100": "late descent, near the bottom",
}


def humanize_severity(severity: str) -> str:
    mapping = {
        "grave": "high",
        "media": "moderate",
        "leve": "low",
        "posible": "possible",
        "none": "no issue",
    }
    return mapping.get(str(severity or "none").lower(), "no issue")


def humanize_phase(phase: str) -> str:
    mapping = {
        "eccentric": "during the descent",
        "bottom": "at the bottom position",
        "concentric": "during the ascent",
        "lockout": "at the end of the rep",
        "full_rep": "during the rep",
        "unknown": "at an undetermined moment",
    }
    return mapping.get(str(phase or "unknown").lower(), "at an undetermined moment")


def humanize_location_label(location_label: str) -> str:
    mapping = {
        "eccentric": "during the descent",
        "concentric": "during the ascent",
        "lockout": "at lockout",
        "full_rep": "during the rep",
        "eccentric_start": "at the start of the descent",
        "eccentric_early": "early in the descent",
        "eccentric_mid": "mid descent",
        "eccentric_mid_late": "between mid and late descent",
        "eccentric_late": "late in the descent",
        "eccentric_late_bottom": "late descent, near the bottom",
        "eccentric_range": "through the descent range",
        "bottom": "at the bottom position",
        "concentric_early": "early in the ascent",
        "concentric_mid": "mid ascent",
        "concentric_late": "late in the ascent",
        "concentric_late_lockout": "late ascent, near lockout",
        "unknown": "at an undetermined moment",
    }
    return mapping.get(str(location_label or "unknown").lower(), "at an undetermined moment")


def humanize_reps(reps: list[int]) -> str:
    vals = sorted({int(r) for r in reps if isinstance(r, int)})
    if not vals:
        return "reps not determined"
    if len(vals) == 1:
        return f"rep {vals[0]}"
    if len(vals) == 2:
        return f"reps {vals[0]} and {vals[1]}"
    return f"reps {', '.join(str(x) for x in vals[:-1])} and {vals[-1]}"


def _where_from_anchors(anchors: list[str]) -> str:
    clean = [str(a).strip().lower() for a in anchors if str(a).strip()]
    if not clean:
        return "at an undetermined moment"
    uniq = sorted(set(clean), key=lambda a: (_ANCHOR_ORDER.get(a, 999), a))
    parts: list[str] = []
    for a in uniq:
        text = _ANCHOR_WHERE_TEXT.get(a)
        if text and text not in parts:
            parts.append(text)
    if not parts:
        return "at an undetermined moment"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _where_from_failed_segments(segments: list[str]) -> str:
    clean = [str(s).strip().lower() for s in segments if str(s).strip()]
    if not clean:
        return ""
    parts: list[str] = []
    for seg in clean:
        txt = _FAILED_SEGMENT_TEXT.get(seg)
        if txt and txt not in parts:
            parts.append(txt)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def build_where_text(issue: dict[str, Any], *, include_reps: bool = True) -> str:
    """Short phrase: segment/anchors and optionally which reps saw the issue."""
    detector = str(issue.get("detector", "")).lower()
    location_labels = issue.get("location_labels", [])
    phases = issue.get("phases", [])
    anchors = issue.get("anchors", [])
    clean_labels = (
        [str(x).lower() for x in location_labels if str(x) and str(x).lower() != "unknown"]
        if isinstance(location_labels, list)
        else []
    )
    if detector == "asymmetry":
        phase_set = {str(p).lower() for p in phases} if isinstance(phases, list) else set()
        if "full_rep" in phase_set:
            loc_text = "during the rep"
        elif {"eccentric", "concentric"}.issubset(phase_set):
            loc_text = "during the rep"
        elif "eccentric" in phase_set:
            loc_text = "during the descent"
        elif "concentric" in phase_set:
            loc_text = "during the ascent"
        else:
            loc_text = "during the rep"
    elif clean_labels:
        label_set = set(clean_labels)
        if {"eccentric_late", "eccentric_late_bottom"}.issubset(label_set):
            loc_text = "late descent, near the bottom"
        elif {"eccentric_mid", "eccentric_late"}.issubset(label_set):
            loc_text = "between mid and late descent"
        elif {"concentric_mid", "concentric_late"}.issubset(label_set):
            loc_text = "during the second half of the ascent"
        elif len(clean_labels) == 1:
            one = clean_labels[0]
            if one in {"eccentric", "concentric", "lockout", "full_rep"}:
                loc_text = humanize_phase(one)
            else:
                loc_text = humanize_location_label(one)
        else:
            human = []
            for l in clean_labels:
                txt = humanize_location_label(l)
                if txt not in human:
                    human.append(txt)
            if len(human) == 1:
                loc_text = human[0]
            elif len(human) == 2:
                loc_text = f"{human[0]} and {human[1]}"
            else:
                loc_text = f"{', '.join(human[:-1])}, and {human[-1]}"
    elif isinstance(anchors, list) and anchors:
        loc_text = _where_from_anchors(anchors)
    elif isinstance(phases, list) and phases:
        clean_phases = [str(p).lower() for p in phases if str(p) and str(p).lower() != "unknown"]
        loc_text = humanize_phase(clean_phases[0]) if clean_phases else "during the rep"
    else:
        metrics = issue.get("summary_metrics")
        failed_segments = metrics.get("failed_segments", []) if isinstance(metrics, dict) else []
        if isinstance(failed_segments, list) and failed_segments:
            loc_text = _where_from_failed_segments(failed_segments) or "during the rep"
        elif detector in {"hip_hinge", "knee_dominant", "spine_flexion"}:
            loc_text = "during the descent"
        elif detector == "lockout":
            loc_text = "at lockout"
        else:
            loc_text = "during the rep"
    if loc_text == "at an undetermined moment":
        if detector in {"hip_hinge", "knee_dominant", "spine_flexion", "neck_movement", "bar_far", "bent_arms", "rom"}:
            loc_text = "during the descent"
        elif detector == "lockout":
            loc_text = "at lockout"
        elif detector == "asymmetry":
            loc_text = "during the rep"
        else:
            loc_text = "during the rep"
    if not include_reps:
        return loc_text
    reps_text = humanize_reps(issue.get("reps", []))
    return f"{reps_text}, {loc_text}"
