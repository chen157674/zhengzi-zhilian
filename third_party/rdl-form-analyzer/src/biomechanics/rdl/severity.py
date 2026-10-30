# Shared RDL severity helper (5 levels): none < posible < leve < media < grave.
#
# Single lightweight source for ranking/combining severities. No dependencies on
# feedback, detectors, or the heavy stack, so it can be imported from any layer.

from __future__ import annotations

from collections.abc import Iterable

# Canonical severity order (higher value = more severe).
SEVERITY_RANK: dict[str, int] = {
    "none": 0,
    "posible": 1,
    "leve": 2,
    "media": 3,
    "grave": 4,
}


def severity_rank(severity: str | None) -> int:
    """Numeric ranking of a severity. Unknown/None -> 0 (equivalent to 'none')."""
    if severity is None:
        return 0
    return SEVERITY_RANK.get(str(severity).lower(), 0)


def max_severity(severities: Iterable[str | None]) -> str:
    """Return the highest-ranked severity. No valid values -> 'none'."""
    best = "none"
    best_rank = 0
    for sev in severities:
        rank = severity_rank(sev)
        if rank > best_rank:
            best_rank = rank
            best = str(sev).lower()
    return best
