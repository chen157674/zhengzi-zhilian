"""Unit tests for src.scripts._debug_interactive."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scripts import _debug_interactive as di


def test_ask_yes_no_accepts_answers(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "s")
    assert di.ask_yes_no("Q?") is True
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert di.ask_yes_no("Q?") is True
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert di.ask_yes_no("Q?") is False
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert di.ask_yes_no("Q?", default=True) is True
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert di.ask_yes_no("Q?", default=False) is False


def test_ask_mode_returns_video_bundle_exit(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "1")
    assert di.ask_mode() == "video"
    monkeypatch.setattr("builtins.input", lambda _: "2")
    assert di.ask_mode() == "bundle"
    monkeypatch.setattr("builtins.input", lambda _: "3")
    assert di.ask_mode() == "exit"


def test_list_videos_filters_extensions(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "clip.mp4").write_bytes(b"x")
    (data_dir / "clip.mov").write_bytes(b"x")
    (data_dir / "clip.avi").write_bytes(b"x")
    (data_dir / "notes.txt").write_bytes(b"x")
    sub = data_dir / "sub"
    sub.mkdir()
    (sub / "nested.mkv").write_bytes(b"x")

    found = di.list_videos(data_dir)
    assert [p.name for p in found] == ["clip.avi", "clip.mov", "clip.mp4", "nested.mkv"]


def test_list_bundles_returns_sorted_directories_only(tmp_path):
    root = tmp_path / "bundles"
    root.mkdir()
    (root / "b_bundle").mkdir()
    (root / "a_bundle").mkdir()
    (root / "file.json").write_text("{}", encoding="utf-8")

    assert [p.name for p in di.list_bundles(root)] == ["a_bundle", "b_bundle"]


def test_load_json_reads_dict(tmp_path):
    path = tmp_path / "payload.json"
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert di.load_json(path) == {"a": 1}

    bad = tmp_path / "list.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        di.load_json(bad)
