# Thresholds and classify_group: decide whether there is significant arm and/or leg asymmetry per rep.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Severity = Literal["none", "posible", "leve", "media", "grave"]

_ORDER: tuple[Severity, ...] = ("none", "posible", "leve", "media", "grave")

# Thresholds

# Per-frame forward_diff_norm (raised thresholds: fewer false positives)
NORM_THR: dict[str, float] = {
    "leve":  0.11,   # previously media
    "media": 0.18,   # previously grave
    "grave": 0.26,   # grave only with very marked asymmetry
}

# Per-phase ratio thresholds
RATIO_THR: dict[str, float] = {
    "leve":  0.45,
    "media": 0.72,
    "grave": 0.85,
}

# Temporal consistency of the dominant side across asymmetric frames.
CONSISTENCY_THR: float = 0.70

# Lateral imbalance across ALL valid frames — filters out high consistency
# driven by only a few asymmetric frames.
SIDE_IMBALANCE_THR_MEDIA: float = 0.55
SIDE_IMBALANCE_THR_GRAVE: float = 0.68

# Stability: std of forward_diff_norm over the asymmetric portion.
# Above → noisy signal, drop one level.
STABILITY_STD_THR: float = 0.05

# Stability downgrade only if ratio is non-trivial and direction passes the gates;
# otherwise variance was already filtered and would be counted twice.
STABILITY_MIN_RATIO: float = 0.40


# Per-frame classification

def frame_severity(fwd_norm: float, confident: bool) -> Severity:
    if fwd_norm != fwd_norm:  # NaN guard
        return "none"
    if not confident:
        return "posible" if fwd_norm > NORM_THR["grave"] else "none"
    if fwd_norm > NORM_THR["grave"]:
        return "grave"
    if fwd_norm > NORM_THR["media"]:
        return "media"
    if fwd_norm > NORM_THR["leve"]:
        return "leve"
    return "none"


# Phase ratio only (legacy aggregate)

def phase_severity(ratios: dict[str, float]) -> tuple[Severity, str]:
    if not ratios:
        return "none", ""
    worst_phase = max(ratios, key=lambda p: ratios[p])
    worst_ratio = ratios[worst_phase]
    above_media = [p for p, r in ratios.items() if r > RATIO_THR["media"]]
    if worst_ratio > RATIO_THR["grave"] or len(above_media) >= 2:
        return "grave", worst_phase
    if worst_ratio > RATIO_THR["media"]:
        return "media", worst_phase
    if worst_ratio > RATIO_THR["leve"]:
        return "leve", worst_phase
    return "none", worst_phase


# Multi-signal group classifier

@dataclass(frozen=True)
class GroupClassification:

    severity: Severity
    magnitude: float            # 0.7·mean + 0.3·max of primary fwd_norm
    ratio: float                # asymmetric frames / valid frames
    consistency: float          # dominant-side ratio among asymmetric frames
    side_imbalance: float       # abs(L-R) / valid frames
    stability_std: float        # std over asymmetric frames only
    dominant_side: str          # "L" | "R" | "none"
    dominant_phase: str
    confidence_label: str       # "high" | "mixed" | "low"


def classify_group(
    *,
    magnitude: float,
    ratio: float,
    consistency: float,
    side_imbalance: float,
    stability_std: float,
    dominant_side: str,
    dominant_phase: str,
    confidence_label: str,
    multi_phase_above_media: bool = False,
) -> GroupClassification:
    direction_ok_media = (
        consistency >= CONSISTENCY_THR
        and side_imbalance >= SIDE_IMBALANCE_THR_MEDIA
    )
    direction_ok_grave = (
        consistency >= CONSISTENCY_THR
        and side_imbalance >= SIDE_IMBALANCE_THR_GRAVE
    )

    severity: Severity = "none"

    grave_ok = (
        magnitude > NORM_THR["grave"]
        and ratio > RATIO_THR["grave"]
        and direction_ok_grave
    )
    media_ok = (
        magnitude > NORM_THR["media"]
        and ratio > RATIO_THR["media"]
        and direction_ok_media
    )
    leve_ok = magnitude > NORM_THR["leve"] and ratio > RATIO_THR["leve"]

    if grave_ok:
        severity = "grave"
    elif media_ok:
        severity = "media"
    elif leve_ok:
        severity = "leve"
    else:
        severity = "none"

    # Magnitude/ratio in the MEDIA range but failing the MEDIA gate → LEVE (normal variation), do not escalate.
    media_like = magnitude > NORM_THR["media"] and ratio > RATIO_THR["media"]
    if media_like and not media_ok and severity in ("none", "posible"):
        severity = "leve"

    # Instability penalty
    # Downgrade only if the signal is substantial AND passes the direction gate;
    # otherwise there is no real signal and it would be double-counted against the prior filter.
    if (
        severity in ("grave", "media")
        and stability_std > STABILITY_STD_THR
        and ratio > STABILITY_MIN_RATIO
        and direction_ok_media
    ):
        severity = _demote(severity)

    # Low confidence caps severity at "posible".
    if confidence_label == "low" and severity not in ("none", "posible"):
        severity = "posible"

    return GroupClassification(
        severity=severity,
        magnitude=magnitude,
        ratio=ratio,
        consistency=consistency,
        side_imbalance=side_imbalance,
        stability_std=stability_std,
        dominant_side=dominant_side,
        dominant_phase=dominant_phase,
        confidence_label=confidence_label,
    )


def _demote(sev: Severity) -> Severity:
    demote_map: dict[Severity, Severity] = {
        "grave":   "media",
        "media":   "leve",
        "leve":    "posible",
        "posible": "none",
        "none":    "none",
    }
    return demote_map[sev]


# Utilities

def _severity_index(s: Severity) -> int:
    try:
        return _ORDER.index(s)
    except ValueError:
        return 0


def max_severity(a: Severity, b: Severity) -> Severity:
    return _ORDER[max(_severity_index(a), _severity_index(b))]


def stability_label(std: float) -> str:
    if std != std:  # NaN
        return "low"
    return "high" if std <= STABILITY_STD_THR else "low"


def severity_color(sev: Severity) -> str:
    return {
        "none":    "#2ecc71",
        "posible": "#f39c12",
        "leve":    "#f1c40f",
        "media":   "#e67e22",
        "grave":   "#e74c3c",
    }.get(sev, "gray")
