"""The upload notification queue is bounded so it does not grow without limit
if no Flet session drains it."""

from __future__ import annotations

from src.app.drop_queue import pending


def test_pending_is_bounded():
    assert pending.maxlen is not None and pending.maxlen > 0


def test_pending_drops_oldest_when_full():
    pending.clear()
    try:
        for i in range(pending.maxlen + 10):
            pending.append(f"f{i}")
        assert len(pending) == pending.maxlen
        # the oldest ("f0") was dropped; the newest remains
        assert "f0" not in pending
        assert f"f{pending.maxlen + 9}" in pending
    finally:
        pending.clear()
