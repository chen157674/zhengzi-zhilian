"""Detector geometry can fail per frame; the warning must be emitted
once per detector and per render (prepare resets it), not frame by frame."""

from __future__ import annotations

import logging

import pytest

from src.visualization.overlay.layers import detector_geometry as dg


@pytest.fixture()
def captured():
    records: list[str] = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _H()
    dg.logger.addHandler(handler)
    prev_level = dg.logger.level
    dg.logger.setLevel(logging.WARNING)
    dg._geometry_failures_logged.clear()
    try:
        yield records
    finally:
        dg.logger.removeHandler(handler)
        dg.logger.setLevel(prev_level)
        dg._geometry_failures_logged.clear()


def test_repeated_same_detector_logs_once(captured):
    exc = ValueError("boom")
    for _ in range(50):
        dg._log_geometry_failure("neck_movement", exc)
    assert len(captured) == 1


def test_distinct_detectors_each_log(captured):
    exc = ValueError("boom")
    dg._log_geometry_failure("neck_movement", exc)
    dg._log_geometry_failure("hip_hinge", exc)
    assert len(captured) == 2


def test_reset_allows_relog(captured):
    exc = ValueError("boom")
    dg._log_geometry_failure("bar_far", exc)
    dg._geometry_failures_logged.clear()  # simulates a new render (prepare)
    dg._log_geometry_failure("bar_far", exc)
    assert len(captured) == 2
