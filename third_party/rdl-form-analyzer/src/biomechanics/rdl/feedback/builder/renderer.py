# Plain-text feedback (CLI/debug); not what Flet renders directly.

from __future__ import annotations

from typing import Any

from .labels import humanize_severity


def _render_feedback_bucket(title: str, items: list[dict[str, Any]]) -> list[str]:
    lines = [f"{title}:"]
    if not items:
        lines.append("- None.")
        return lines
    for idx, item in enumerate(items, start=1):
        lines.append(f"{idx}. {item.get('title', item.get('error_code', 'error'))}")
        lines.append(f"   - Severity: {humanize_severity(str(item.get('severity', 'none')))}")
        lines.append(f"   - Where: {item.get('where', 'n/a')}")
        lines.append(f"   - What happens: {item.get('what_happens', '')}")
        lines.append(f"   - Why it matters: {item.get('why_it_matters', '')}")
        lines.append(f"   - How to fix: {item.get('how_to_fix', '')}")
    return lines


def render_plain_text_report(report_dict: dict[str, Any]) -> str:
    """Concatenate headline, buckets, and rep_feedback into a readable string."""
    if not isinstance(report_dict, dict):
        return "RDL ANALYSIS\n\nSummary:\nNot enough data to generate feedback."

    if report_dict.get("status") == "no_relevant_issues":
        return (
            "RDL ANALYSIS\n\n"
            "Summary:\n"
            "No relevant technical errors were detected in the analyzed reps.\n\n"
            "Keep your current technique and check that movement stays consistent across sets and loads."
        )

    lines: list[str] = []
    lines.append("RDL ANALYSIS")
    lines.append("")
    lines.append("Summary:")
    lines.append(str(report_dict.get("headline", "")))
    lines.append("")
    rep_feedback = list(report_dict.get("rep_feedback", []))
    rep_feedback = [r for r in rep_feedback if isinstance(r, dict)]
    rep_feedback = [
        r
        for r in rep_feedback
        if list(r.get("primary_errors", [])) or list(r.get("secondary_errors", [])) or list(r.get("observations", []))
    ]
    for rep in rep_feedback:
        lines.append(f"Rep {rep.get('user_rep_order', 'n/a')}")
        lines.append("")
        lines.extend(_render_feedback_bucket("Primary errors", list(rep.get("primary_errors", []))))
        lines.append("")
        lines.extend(_render_feedback_bucket("Secondary errors", list(rep.get("secondary_errors", []))))
        lines.append("")
        lines.extend(_render_feedback_bucket("Observations", list(rep.get("observations", []))))
        lines.append("")
    return "\n".join(lines)
