# hip_hinge rules: detect_hip_hinge_from_trajectory compares per-phase deltas of the descent.

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from src.biomechanics.rdl.detectors.hip_hinge.metrics import HIP_HINGE_ANCHORS, HipBackMetrics

# Types

Severity = Literal["none", "leve", "media", "grave"]
Confidence = Literal["low", "medium", "high"]
DominantPhase = Literal["descent_start", "descent_end", "none"]

# Evaluated anchors (backward-compatible imports).
ORDERED_ANCHORS: tuple[str, ...] = HIP_HINGE_ANCHORS

# Thresholds (normalized delta_hip_back; user − ideal).
DELTA_FAIL_THR: float = 0.08
SEV_THR: dict[str, float] = {
    "leve":  0.08,
    "media": 0.15,
    "grave": 0.25,
}


# Data classes

@dataclass
class AnchorRuling:

    anchor: str
    failed: bool
    delta_hip_back: float
    trace: list[str] = field(default_factory=list)


@dataclass
class RepHipHingeVerdict:

    detected: bool
    severity: Severity
    confidence: Confidence
    dominant_phase: DominantPhase
    magnitude: float
    mean_deficit: float
    max_deficit: float
    n_failed: int
    per_anchor: dict[str, AnchorRuling] = field(default_factory=dict)
    trace: list[str] = field(default_factory=list)


# Helpers

def _finite(v: float) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(float(v))


def _severity_from_magnitude(mag: float) -> Severity:
    if not _finite(mag):
        return "none"
    if mag > SEV_THR["grave"]:
        return "grave"
    if mag > SEV_THR["media"]:
        return "media"
    if mag > SEV_THR["leve"]:
        return "leve"
    return "none"


def _confidence(n_failed: int) -> Confidence:
    ratio = n_failed / float(len(ORDERED_ANCHORS))
    if ratio > 0.7:
        return "high"
    if ratio >= 0.4:
        return "medium"
    return "low"


def _dominant_phase(failed_anchors: list[str]) -> DominantPhase:
    if not failed_anchors:
        return "none"
    start = 0
    end = 0
    for a in failed_anchors:
        if a == "ecc_0":
            start += 1
        elif a == "ecc_25":
            start += 1
        elif a == "ecc_50":
            start += 1
            end += 1
        elif a == "ecc_75":
            end += 1
        elif a == "ecc_100":
            end += 1
        elif a == "bottom":
            end += 1
    if start > end:
        return "descent_start"
    return "descent_end"


def _has_consecutive_failures(
    ordered_rulings: list[AnchorRuling], min_run: int = 2
) -> bool:
    run = 0
    for r in ordered_rulings:
        if r.failed:
            run += 1
            if run >= min_run:
                return True
        else:
            run = 0
    return False


# Per-anchor rule

def rule_anchor(m: HipBackMetrics) -> AnchorRuling:
    trace: list[str] = []
    d = m.delta_hip_back
    d_txt = f"{d:+.3f}" if _finite(d) else "nan"
    trace.append(
        f"Δhip_back = {d_txt}   (fail if > {DELTA_FAIL_THR:+.2f})"
    )

    failed = _finite(d) and d > DELTA_FAIL_THR
    if failed:
        trace.append("  →  FAIL (user does not shift back far enough)")
    else:
        trace.append("  →  no-fail")
    return AnchorRuling(
        anchor=m.anchor,
        failed=failed,
        delta_hip_back=float(d) if _finite(d) else float("nan"),
        trace=trace,
    )


# Per-rep classification

def detect_hip_hinge_from_trajectory(
    anchor_metrics: dict[str, HipBackMetrics],
) -> RepHipHingeVerdict:
    trace: list[str] = []

    per_anchor: dict[str, AnchorRuling] = {}
    ordered: list[AnchorRuling] = []
    for a in ORDERED_ANCHORS:
        m = anchor_metrics.get(a)
        if m is None:
            ruling = AnchorRuling(
                anchor=a, failed=False,
                delta_hip_back=float("nan"),
                trace=[f"{a}: anchor not available"],
            )
        else:
            ruling = rule_anchor(m)
        per_anchor[a] = ruling
        ordered.append(ruling)

    failed_rulings = [r for r in ordered if r.failed]
    n_failed = len(failed_rulings)

    persistence_ok = _has_consecutive_failures(ordered, min_run=2)
    trace.append(
        "Persistence: "
        + ("OK (≥2 consecutive anchors)" if persistence_ok
           else "no 2 consecutive → noise")
    )

    if not persistence_ok or n_failed == 0:
        return RepHipHingeVerdict(
            detected=False,
            severity="none",
            confidence=_confidence(n_failed),
            dominant_phase=_dominant_phase([r.anchor for r in failed_rulings]),
            magnitude=0.0,
            mean_deficit=0.0,
            max_deficit=0.0,
            n_failed=n_failed,
            per_anchor=per_anchor,
            trace=trace,
        )

    deltas = [abs(r.delta_hip_back) for r in failed_rulings if _finite(r.delta_hip_back)]
    # delta_hip_back of failed anchors is positive (> threshold);
    # abs() is a safety net — in practice it does not change values.
    if not deltas:
        return RepHipHingeVerdict(
            detected=False,
            severity="none",
            confidence=_confidence(n_failed),
            dominant_phase=_dominant_phase([r.anchor for r in failed_rulings]),
            magnitude=0.0,
            mean_deficit=0.0,
            max_deficit=0.0,
            n_failed=n_failed,
            per_anchor=per_anchor,
            trace=trace,
        )

    mean_deficit = float(sum(deltas) / len(deltas))
    max_deficit = float(max(deltas))
    magnitude = 0.7 * mean_deficit + 0.3 * max_deficit

    trace.append(
        f"mean Δhip_back = {mean_deficit:.3f}   max Δhip_back = {max_deficit:.3f}"
    )
    trace.append(
        f"magnitude = 0.7·mean + 0.3·max = {magnitude:.3f}"
    )

    severity = _severity_from_magnitude(magnitude)
    trace.append(f"severity (by aggregated magnitude) = {severity}")

    return RepHipHingeVerdict(
        detected=True,
        severity=severity,
        confidence=_confidence(n_failed),
        dominant_phase=_dominant_phase([r.anchor for r in failed_rulings]),
        magnitude=magnitude,
        mean_deficit=mean_deficit,
        max_deficit=max_deficit,
        n_failed=n_failed,
        per_anchor=per_anchor,
        trace=trace,
    )


# Backward-compatible alias (old name in __init__.py).
classify_rep = detect_hip_hinge_from_trajectory
