from __future__ import annotations

import src.biomechanics.rdl.feedback.builder as feedback_builder
from src.biomechanics.rdl.feedback.builder import (
    FeedbackItem,
    FeedbackReport,
    build_rdl_feedback_report,
    render_plain_text_report,
)


def test_per_rep_feedback_summary_not_exported():
    assert "PerRepFeedbackSummary" not in feedback_builder.__all__


def test_builder_public_exports_importable():
    assert callable(build_rdl_feedback_report)
    assert callable(render_plain_text_report)
    assert FeedbackItem is not None
    assert FeedbackReport is not None
