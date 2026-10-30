"""Debug bundle contract: run_full_analysis returns a runtime dict.

The *_debug.py scripts pass that dict to export_rdl_debug_bundle, so
_as_runtime/_as_lightweight must accept a plain dict as-is (regression of the
bug where the scripts called .to_runtime_dict() on a dict, causing AttributeError).
"""

from __future__ import annotations

from src.visualization.rdl.debug.debug_bundle import _as_lightweight, _as_runtime


def test_as_runtime_accepts_plain_dict():
    d = {"segmentation_result": {"video_id": "vid"}}
    assert _as_runtime(d) is d


def test_as_lightweight_accepts_plain_dict():
    d = {"segmentation_result": {"video_id": "vid", "summary": {}}}
    light = _as_lightweight(d, d)
    assert isinstance(light, dict)
