# Detection and cleanup of top/bottom events in the angular signal (peaks = top, valleys = bottom of the RDL).
# Called from pipeline._run_rdl_segmentation_core in sequence, before build_rdl_repetitions.

from __future__ import annotations

from typing import Any, Callable

import numpy as np
from scipy.signal import find_peaks

from src.biomechanics.rdl.segmentation.signal_stats import drop_between, ratio, trend_score

Event = tuple[str, int, float]


def _events_from_arrays(y: np.ndarray, bottoms: np.ndarray, tops: np.ndarray) -> list[Event]:
    """Merge bottom/top index arrays into a single (kind, index, value) list, sorted by frame."""
    events: list[Event] = []
    for idx_raw in bottoms:
        idx = int(idx_raw)
        if 0 <= idx < y.size and np.isfinite(y[idx]):
            events.append(("bottom", idx, float(y[idx])))
    for idx_raw in tops:
        idx = int(idx_raw)
        if 0 <= idx < y.size and np.isfinite(y[idx]):
            events.append(("top", idx, float(y[idx])))
    events.sort(key=lambda e: e[1])
    return events


def _collapse_consecutive_same_kind(events: list[Event]) -> list[Event]:
    """Where two same-kind events are adjacent (no opposite kind between them), keep only the
    most extreme one (lowest bottom / highest top) so the sequence strictly alternates."""
    if not events:
        return []
    collapsed: list[Event] = [events[0]]
    for kind, idx, val in events[1:]:
        prev_kind, _prev_idx, prev_val = collapsed[-1]
        if kind == prev_kind:
            if (kind == "bottom" and val < prev_val) or (kind == "top" and val > prev_val):
                collapsed[-1] = (kind, idx, val)
        else:
            collapsed.append((kind, idx, val))
    return collapsed


def _split_events(events: list[Event]) -> tuple[np.ndarray, np.ndarray]:
    bottom = np.asarray(sorted(idx for k, idx, _ in events if k == "bottom"), dtype=np.int64)
    top = np.asarray(sorted(idx for k, idx, _ in events if k == "top"), dtype=np.int64)
    return bottom, top


def detect_top_bottom_candidates(
    signal: np.ndarray,
    *,
    min_distance: int = 15,
    min_prominence_ratio: float = 0.05,
) -> tuple[np.ndarray, np.ndarray]:
    """find_peaks on the signal and its negative: bottom and top candidates. Prominence ∝ ROM."""
    y = np.asarray(signal, dtype=np.float64)
    finite = np.isfinite(y)
    valid_vals = y[finite]
    if valid_vals.size < 3:
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)
    p5 = float(np.percentile(valid_vals, 5.0))
    p95 = float(np.percentile(valid_vals, 95.0))
    rom = max(0.0, p95 - p5)
    min_prom = float(min_prominence_ratio) * rom
    top_idx, _ = find_peaks(y, distance=max(1, int(min_distance)), prominence=min_prom)
    bottom_idx, _ = find_peaks(-y, distance=max(1, int(min_distance)), prominence=min_prom)
    return (
        np.asarray(sorted(bottom_idx), dtype=np.int64),
        np.asarray(sorted(top_idx), dtype=np.int64),
    )


def enforce_top_bottom_alternation(
    signal: np.ndarray,
    bottoms: np.ndarray,
    tops: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Removes two consecutive tops or bottoms, keeping the most extreme one (temporal order top-bottom-top…)."""
    y = np.asarray(signal, dtype=np.float64)
    events = _events_from_arrays(y, bottoms, tops)
    return _split_events(_collapse_consecutive_same_kind(events))


def merge_micro_oscillations(
    signal: np.ndarray,
    bottoms: np.ndarray,
    tops: np.ndarray,
    *,
    thr: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Removes top-bottom pairs with amplitude below thr (high-frequency signal noise)."""
    y = np.asarray(signal, dtype=np.float64)
    events = _events_from_arrays(y, bottoms, tops)

    changed = True
    while changed and len(events) >= 2:
        changed = False
        min_amp = float("inf")
        min_pos = -1
        for i in range(len(events) - 1):
            amp = abs(events[i][2] - events[i + 1][2])
            if amp < min_amp:
                min_amp = amp
                min_pos = i
        if min_amp < thr and min_pos >= 0:
            del events[min_pos : min_pos + 2]
            events = _collapse_consecutive_same_kind(events)
            changed = True

    return _split_events(events)


def _boundary_event_candidate(
    y: np.ndarray,
    valid: np.ndarray,
    *,
    region_lo: int,
    region_hi: int,
    boundary_idx: int,
    kind_to_add: str,
    thr: float,
    stable_top_window: Callable[[int, int], bool],
) -> int | None:
    """Index of a synthetic top/bottom to add in `[region_lo, region_hi)`, next to `boundary_idx`.

    `kind_to_add` is the extreme to look for ("top" -> max, "bottom" -> min). The candidate must
    itself pass `valid` (not merely be finite) and clear `thr` amplitude against the boundary
    event; a "top" candidate additionally has to sit in a stable top window (see
    `boundary_requires_stable_top` on the caller). Returns None if no such candidate qualifies.
    """
    if region_hi <= region_lo:
        return None
    region = y[region_lo:region_hi]
    valid_region = valid[region_lo:region_hi]
    if not np.any(valid_region):
        return None
    if kind_to_add == "top" and not stable_top_window(region_lo, region_hi - 1):
        return None
    candidate = region_lo + (int(np.nanargmax(region)) if kind_to_add == "top" else int(np.nanargmin(region)))
    if not valid[candidate]:
        return None
    boundary_val = float(y[boundary_idx])
    candidate_val = float(y[candidate])
    amp = (candidate_val - boundary_val) if kind_to_add == "top" else (boundary_val - candidate_val)
    return candidate if amp > thr else None


def add_boundary_top_bottom_events(
    signal: np.ndarray,
    bottoms: np.ndarray,
    tops: np.ndarray,
    *,
    thr: float,
    allow_boundary_events: bool = True,
    boundary_hold_frames: int | None = None,
    boundary_requires_stable_top: bool = False,
    top_band: float | None = None,
    valid_mask: np.ndarray | None = None,
    min_window_valid_ratio: float = 0.85,
    max_window_p95_delta: float = 3.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Adds a top/bottom at the start or end of the video if the movement begins/ends mid-phase.

    Applies `_boundary_event_candidate` symmetrically to the start and the end of the video
    (mirroring how `recover_boundary_tops` below shares `_recover_one_boundary_top` for its own
    entry/exit search), instead of duplicating the entry/exit logic per event kind.
    """
    y = np.asarray(signal, dtype=np.float64)
    if not allow_boundary_events:
        return bottoms, tops
    finite = np.isfinite(y)
    valid = finite if valid_mask is None else (np.asarray(valid_mask, dtype=bool) & finite)
    bottom_list = sorted(int(i) for i in bottoms)
    top_list = sorted(int(i) for i in tops)

    def _stable_top_window(start: int, end: int) -> bool:
        if not boundary_requires_stable_top:
            return True
        if top_band is None or not np.isfinite(top_band):
            return False
        hold = max(1, int(boundary_hold_frames or 1))
        start = max(0, int(start))
        end = min(y.size - 1, int(end))
        if end - start + 1 < hold:
            return False
        for ws in range(start, end - hold + 2):
            we = ws + hold
            window = y[ws:we]
            valid_w = valid[ws:we]
            if window.size < hold:
                continue
            if float(np.mean(valid_w)) < min_window_valid_ratio:
                continue
            valid_vals = window[valid_w]
            if valid_vals.size == 0:
                continue
            top_ratio = float(np.mean(valid_vals >= float(top_band) - 5.0))
            if top_ratio < 0.80:
                continue
            deltas = np.abs(np.diff(valid_vals))
            p95_delta = float(np.percentile(deltas, 95.0)) if deltas.size else 0.0
            if p95_delta <= max_window_p95_delta:
                return True
        return False

    all_events = [(int(i), "bottom") for i in bottoms] + [(int(i), "top") for i in tops]
    all_events.sort()
    if not all_events:
        return bottoms, tops

    first_idx, first_kind = all_events[0]
    last_idx, last_kind = all_events[-1]

    if first_idx > 0:
        kind_to_add = "top" if first_kind == "bottom" else "bottom"
        candidate = _boundary_event_candidate(
            y, valid,
            region_lo=0, region_hi=first_idx, boundary_idx=first_idx,
            kind_to_add=kind_to_add, thr=thr, stable_top_window=_stable_top_window,
        )
        if candidate is not None:
            if kind_to_add == "top":
                top_list = sorted(set(top_list + [candidate]))
            else:
                bottom_list = sorted(set(bottom_list + [candidate]))

    if last_idx < y.size - 1:
        kind_to_add = "top" if last_kind == "bottom" else "bottom"
        candidate = _boundary_event_candidate(
            y, valid,
            region_lo=last_idx + 1, region_hi=y.size, boundary_idx=last_idx,
            kind_to_add=kind_to_add, thr=thr, stable_top_window=_stable_top_window,
        )
        if candidate is not None:
            if kind_to_add == "top":
                top_list = sorted(set(top_list + [candidate]))
            else:
                bottom_list = sorted(set(bottom_list + [candidate]))

    return np.asarray(bottom_list, dtype=np.int64), np.asarray(top_list, dtype=np.int64)


def collapse_double_bottoms(
    signal: np.ndarray,
    bottoms: np.ndarray,
    tops: np.ndarray,
    *,
    min_separation_frames: int,
    bridge_factor: float = 2.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Merges a closely spaced bottom-top-bottom pattern into a single bottom (a single real descent)."""
    y = np.asarray(signal, dtype=np.float64)
    min_sep = max(1, int(min_separation_frames))
    bridge_sep = max(min_sep, int(round(float(bridge_factor) * min_sep)))
    events = _events_from_arrays(y, bottoms, tops)

    changed = True
    while changed and len(events) >= 3:
        changed = False
        i = 0
        while i < len(events) - 2:
            k0, t0, v0 = events[i]
            k1, t1, _v1 = events[i + 1]
            k2, t2, v2 = events[i + 2]
            if k0 == "bottom" and k1 == "top" and k2 == "bottom":
                direct_close = (t2 - t0) <= min_sep
                bridge_close = min(t1 - t0, t2 - t1) <= bridge_sep
                if direct_close or bridge_close:
                    kept = events[i] if v0 <= v2 else events[i + 2]
                    events[i : i + 3] = [kept]
                    changed = True
                    break
            i += 1

    return enforce_top_bottom_alternation(
        y,
        np.asarray([t for k, t, _ in events if k == "bottom"], dtype=np.int64),
        np.asarray([t for k, t, _ in events if k == "top"], dtype=np.int64),
    )


def center_top_bottom_plateaus(
    signal: np.ndarray,
    bottoms: np.ndarray,
    tops: np.ndarray,
    *,
    rom: float,
    plateau_epsilon_ratio: float = 0.10,
) -> tuple[np.ndarray, np.ndarray]:
    """Moves each event to the center of its plateau (flat region near the local max or min)."""
    y = np.asarray(signal, dtype=np.float64)
    epsilon = float(plateau_epsilon_ratio) * float(rom)
    bottom_list = sorted(int(i) for i in bottoms)
    top_list = sorted(int(i) for i in tops)

    def _centroid(idx: int, kind: str) -> int:
        opp = bottom_list if kind == "top" else top_list
        left_bound = 0
        for o in opp:
            if o < idx:
                left_bound = o
        right_bound = y.size - 1
        for o in opp:
            if o > idx:
                right_bound = o
                break
        region = y[left_bound : right_bound + 1]
        finite_mask = np.isfinite(region)
        if not np.any(finite_mask):
            return idx
        if kind == "top":
            ref_val = float(np.nanmax(region))
            mask = (region >= ref_val - epsilon) & finite_mask
        else:
            ref_val = float(np.nanmin(region))
            mask = (region <= ref_val + epsilon) & finite_mask
        indices_above = np.where(mask)[0]
        if indices_above.size == 0:
            return idx
        return int(np.round(np.mean(indices_above))) + left_bound

    new_top = np.asarray([_centroid(int(i), "top") for i in tops], dtype=np.int64)
    new_bottom = np.asarray([_centroid(int(i), "bottom") for i in bottoms], dtype=np.int64)
    return new_bottom, new_top


def _nearest(sorted_indices: list[int], target: int, direction: str) -> int | None:
    if direction == "left":
        for v in reversed(sorted_indices):
            if v < target:
                return int(v)
    else:
        for v in sorted_indices:
            if v > target:
                return int(v)
    return None


def _amplitude_label(amplitude: float, rom: float) -> str:
    if amplitude > (0.25 * rom):
        return "VALID"
    if amplitude > (0.10 * rom):
        return "LOW_AMPLITUDE"
    return "NOISE"


def _classify_one_kind(
    y: np.ndarray,
    indices: list[int],
    opposite_indices: list[int],
    kind: str,
    *,
    rom: float,
    thr: float,
) -> tuple[list[int], list[dict]]:
    valid: list[int] = []
    details: list[dict] = []
    for pos, idx in enumerate(indices):
        val = float(y[idx]) if np.isfinite(y[idx]) else float("nan")
        left_opp = _nearest(opposite_indices, idx, "left")
        right_opp = _nearest(opposite_indices, idx, "right")
        if kind == "bottom":
            left_amp = float(y[left_opp] - val) if left_opp is not None and np.isfinite(val) else float("nan")
            right_amp = float(y[right_opp] - val) if right_opp is not None and np.isfinite(val) else float("nan")
        else:
            left_amp = float(val - y[left_opp]) if left_opp is not None and np.isfinite(val) else float("nan")
            right_amp = float(val - y[right_opp]) if right_opp is not None and np.isfinite(val) else float("nan")
        positive_amps = [a for a in [left_amp, right_amp] if np.isfinite(a) and a > 0]
        is_valid = False
        reason = "no_valid_neighbor"
        local_amp = float("nan")
        is_border = pos == 0 or pos == len(indices) - 1
        if kind == "bottom" and len(positive_amps) != 2:
            if len(positive_amps) == 1 and is_border:
                local_amp = positive_amps[0]
                is_valid = local_amp > thr
                reason = "ok" if is_valid else "low_local_prominence"
            else:
                reason = "missing_two_sided_prominence"
        elif len(positive_amps) == 2:
            local_amp = float(min(positive_amps))
            is_valid = all(a > thr for a in positive_amps)
            reason = "ok" if is_valid else "low_local_prominence"
        elif len(positive_amps) == 1 and is_border:
            local_amp = positive_amps[0]
            is_valid = local_amp > thr
            reason = "ok" if is_valid else "low_local_prominence"
        if is_valid:
            valid.append(idx)
        details.append(
            {
                "kind": kind,
                "index": idx,
                "value": val,
                "left_neighbor_index": left_opp,
                "right_neighbor_index": right_opp,
                "left_amplitude": left_amp,
                "right_amplitude": right_amp,
                "local_amplitude": local_amp,
                "is_valid": bool(is_valid),
                "reason": reason,
                "label": _amplitude_label(local_amp, rom) if np.isfinite(local_amp) else "NOISE",
            }
        )
    return valid, details


def classify_top_bottom_events(
    signal: np.ndarray,
    bottoms: np.ndarray,
    tops: np.ndarray,
    *,
    rom: float,
    thr: float,
) -> tuple[list[int], list[dict], list[int], list[dict]]:
    """Filters events with low local prominence relative to the opposite neighbor; returns valid lists + details."""
    y = np.asarray(signal, dtype=np.float64)
    top_list = sorted(int(i) for i in tops)
    bottom_list = sorted(int(i) for i in bottoms)
    bottom_valid, bottom_details = _classify_one_kind(
        y, bottom_list, top_list, "bottom", rom=rom, thr=thr
    )
    top_valid, top_details = _classify_one_kind(
        y, top_list, bottom_list, "top", rom=rom, thr=thr
    )
    return bottom_valid, bottom_details, top_valid, top_details


def _score_top_candidate(
    y: np.ndarray,
    valid: np.ndarray,
    *,
    candidate: int,
    bottom: int,
    min_drop: float,
    top_band: float,
) -> dict[str, Any]:
    """Does `candidate` look like a real top relative to `bottom`: big enough, clean, mostly-valid drop?"""
    lo, hi = (candidate, bottom) if candidate < bottom else (bottom, candidate)
    segment = y[lo : hi + 1]
    segment_valid = valid[lo : hi + 1]
    dy = np.diff(segment)
    if candidate > bottom:
        dy = dy[::-1]  # walk from candidate towards bottom either way
    score = trend_score(dy, "down")
    valid_ratio = ratio(segment_valid)
    drop = abs(drop_between(y, candidate, bottom))
    top_ok = bool(np.isfinite(y[candidate]) and y[candidate] >= float(top_band) - 8.0)
    accepted = bool(
        np.isfinite(drop) and drop >= min_drop and score >= 0.70 and valid_ratio >= 0.60 and top_ok
    )
    return {
        "index": int(candidate),
        "drop": float(drop) if np.isfinite(drop) else None,
        "trend_score": float(score),
        "valid_ratio": float(valid_ratio),
        "signal_value": float(y[candidate]) if np.isfinite(y[candidate]) else None,
        "top_threshold": float(top_band) - 8.0,
        "accepted": accepted,
    }


def _recover_one_boundary_top(
    y: np.ndarray,
    valid: np.ndarray,
    *,
    bottom: int,
    region_lo: int,
    region_hi: int,
    peak_candidates: list[int],
    min_drop: float,
    fallback_min_drop: float,
    top_band: float,
) -> tuple[int | None, dict[str, Any]]:
    """Recovers a missing top on one side of `bottom`, within [region_lo, region_hi).

    Tier A: the closest already-detected peak candidate with a clean, large-enough drop to
    `bottom`. Tier B (looser fallback): the most extreme point in the region, if its drop is
    at least `fallback_min_drop` (no shape/stability requirement).
    """
    debug: dict[str, Any] = {"applied": False, "recovered_index": None, "reason": "not_needed", "tier": None, "evaluated_candidates": []}
    if region_hi <= region_lo:
        debug["reason"] = "empty_region"
        return None, debug

    in_range = [m for m in peak_candidates if region_lo < m < region_hi and np.isfinite(y[m])]
    scored = [_score_top_candidate(y, valid, candidate=m, bottom=bottom, min_drop=min_drop, top_band=top_band) for m in in_range]
    debug["evaluated_candidates"] = scored
    accepted = [s["index"] for s in scored if s["accepted"]]
    if accepted:
        recovered = min(accepted, key=lambda m: abs(m - bottom))
        debug.update(applied=True, recovered_index=int(recovered), reason="recovered_from_candidate_peak", tier="candidate_trend")
        return int(recovered), debug

    region = y[region_lo:region_hi]
    if not np.any(np.isfinite(region)):
        debug["reason"] = "no_finite_values_in_region"
        return None, debug
    extreme = region_lo + int(np.nanargmax(region))
    drop = abs(drop_between(y, extreme, bottom))
    if np.isfinite(drop) and drop >= fallback_min_drop:
        debug.update(applied=True, recovered_index=int(extreme), reason="recovered_from_region_extreme", tier="region_extreme")
        return int(extreme), debug

    debug["reason"] = "no_candidate_passed_checks"
    return None, debug


def recover_boundary_tops(
    signal: np.ndarray,
    valid_mask: np.ndarray,
    bottom_valid: list[int],
    top_valid: list[int],
    top_candidates: np.ndarray,
    *,
    global_rom: float,
    top_band: float,
    fallback_min_drop: float = 20.0,
) -> tuple[list[int], list[dict[str, Any]], dict[str, Any]]:
    """If the video starts or ends mid-descent (a bottom with no top on that side), recovers one.

    Applies the same two-tier search (see `_recover_one_boundary_top`) symmetrically to the
    start and the end of the video. Returns the updated top list, synthetic detail entries for
    any recovered tops (same shape as `classify_top_bottom_events` details, for visualization),
    and a debug dict.
    """
    y = np.asarray(signal, dtype=np.float64)
    valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(y)
    min_drop = max(35.0, 0.5 * float(global_rom))
    candidates = np.asarray(top_candidates, dtype=np.int64).tolist()
    tops = sorted(int(t) for t in top_valid)
    bottoms = sorted(int(b) for b in bottom_valid)
    extra_details: list[dict[str, Any]] = []
    debug: dict[str, Any] = {"entry": None, "exit": None}

    if bottoms and (not tops or bottoms[0] < tops[0]):
        first_bottom = bottoms[0]
        recovered, entry_debug = _recover_one_boundary_top(
            y, valid, bottom=first_bottom, region_lo=0, region_hi=first_bottom,
            peak_candidates=candidates, min_drop=min_drop, fallback_min_drop=fallback_min_drop, top_band=top_band,
        )
        debug["entry"] = entry_debug
        if recovered is not None:
            tops = sorted(set(tops) | {recovered})
            extra_details.append(_boundary_detail("top", recovered, y[recovered], right_neighbor=first_bottom, right_value=y[first_bottom], label="ENTRY_TOP"))

    if bottoms and (not tops or bottoms[-1] > tops[-1]):
        last_bottom = bottoms[-1]
        recovered, exit_debug = _recover_one_boundary_top(
            y, valid, bottom=last_bottom, region_lo=last_bottom + 1, region_hi=y.size,
            peak_candidates=candidates, min_drop=min_drop, fallback_min_drop=fallback_min_drop, top_band=top_band,
        )
        debug["exit"] = exit_debug
        if recovered is not None:
            tops = sorted(set(tops) | {recovered})
            extra_details.append(_boundary_detail("top", recovered, y[recovered], left_neighbor=last_bottom, left_value=y[last_bottom], label="EXIT_TOP"))

    return tops, extra_details, debug


def _boundary_detail(
    kind: str,
    index: int,
    value: float,
    *,
    label: str,
    left_neighbor: int | None = None,
    left_value: float | None = None,
    right_neighbor: int | None = None,
    right_value: float | None = None,
) -> dict[str, Any]:
    left_amp = float(value - left_value) if left_value is not None else float("nan")
    right_amp = float(value - right_value) if right_value is not None else float("nan")
    return {
        "kind": kind,
        "index": int(index),
        "value": float(value),
        "left_neighbor_index": left_neighbor,
        "right_neighbor_index": right_neighbor,
        "left_amplitude": left_amp,
        "right_amplitude": right_amp,
        "local_amplitude": right_amp if left_neighbor is None else left_amp,
        "is_valid": True,
        "reason": "boundary_recovered",
        "label": label,
    }

