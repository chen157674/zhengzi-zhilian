
# From a segmentation rep → frame number for each anchor (ecc_0, bottom, con_50, …).

from __future__ import annotations

from typing import Any

from src.biomechanics.rdl.analysis_context.constants import RDL_ANCHOR_NAMES


def _coerce_frame(value: Any) -> int | None:
    """Convert to an integer frame index ≥ 0, or None if unusable."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        frame = int(value)
    except (TypeError, ValueError):
        return None
    if frame < 0:
        return None
    return frame


def _from_anchor_entry(entry: Any) -> tuple[int | None, bool]:
    """Read a frame from the simple rep['anchors'] map (int value or dict with 'frame')."""
    if isinstance(entry, dict):
        frame = _coerce_frame(entry.get("frame"))
        valid_flag = entry.get("valid", True)
        return frame, bool(valid_flag) and frame is not None
    frame = _coerce_frame(entry)
    return frame, bool(frame is not None)


def _resolve_from_details(anchor_details: dict[str, Any], anchor_name: str) -> tuple[int | None, bool]:
    """Read a frame from rep['anchor_details'] (rich segmentation / signal_progress output)."""
    mapping = {
        "ecc_0": ("eccentric", "0"),
        "ecc_25": ("eccentric", "25"),
        "ecc_50": ("eccentric", "50"),
        "ecc_75": ("eccentric", "75"),
        "ecc_100": ("eccentric", "100"),
        "con_0": ("concentric", "0"),
        "con_25": ("concentric", "25"),
        "con_50": ("concentric", "50"),
        "con_75": ("concentric", "75"),
        "con_100": ("concentric", "100"),
    }
    phase, pct = mapping[anchor_name]
    item = (((anchor_details.get(phase) or {}).get("anchors") or {}).get(pct))
    if not isinstance(item, dict):
        return None, False
    frame = _coerce_frame(item.get("frame"))
    valid_flag = item.get("valid", True)
    return frame, bool(valid_flag) and frame is not None


def resolve_rdl_anchor_frames(rep: dict) -> dict:
    """For a rep (user or ideal), return each anchor's frame and whether it is valid.

    bottom may come from anchors.bottom or fall back to ecc_100 if needed.
    Output: frames, valid, source (where each anchor came from), warnings.
    """
    frames: dict[str, int | None] = {name: None for name in RDL_ANCHOR_NAMES}
    valid: dict[str, bool] = {name: False for name in RDL_ANCHOR_NAMES}
    source: dict[str, str] = {name: "missing" for name in RDL_ANCHOR_NAMES}
    warnings: list[str] = []

    anchor_details = rep.get("anchor_details") if isinstance(rep, dict) else None
    anchors = rep.get("anchors") if isinstance(rep, dict) else None
    anchors = anchors if isinstance(anchors, dict) else {}
    has_details = isinstance(anchor_details, dict) and bool(anchor_details)

    for anchor_name in RDL_ANCHOR_NAMES:
        frame: int | None = None
        ok = False
        src = "missing"

        if anchor_name == "bottom":
            bottom_entry = anchors.get("bottom")
            frame, ok = _from_anchor_entry(bottom_entry)
            if ok:
                src = "anchors.bottom"
            else:
                ecc100_frame = frames.get("ecc_100")
                ecc100_ok = valid.get("ecc_100", False)
                if ecc100_ok and ecc100_frame is not None:
                    frame, ok, src = ecc100_frame, True, "ecc_100_fallback"
        else:
            if has_details and anchor_name.startswith(("ecc_", "con_")):
                frame, ok = _resolve_from_details(anchor_details, anchor_name)
                if ok:
                    src = "anchor_details"
            if not ok and anchor_name in anchors:
                frame, ok = _from_anchor_entry(anchors.get(anchor_name))
                if ok:
                    src = "anchors"

        if frame is None or not ok:
            frames[anchor_name] = None
            valid[anchor_name] = False
            source[anchor_name] = "missing"
            warnings.append(f"MISSING_ANCHOR:{anchor_name}")
            continue

        frames[anchor_name] = int(frame)
        valid[anchor_name] = True
        source[anchor_name] = src

    return {
        "frames": frames,
        "valid": valid,
        "source": source,
        "warnings": warnings,
    }
