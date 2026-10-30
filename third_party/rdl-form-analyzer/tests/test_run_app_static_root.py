"""Guards that the Flet web app never serves the whole repository root.

These checks are dependency-free (they do not import ``run_app``, which needs
``flet_web``) so they run in the default test environment. They protect against
a regression where ``FletStaticFiles(assets_dir=PROJECT_ROOT)`` would expose
source code, outputs, data and weights over HTTP.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_APP = PROJECT_ROOT / "src" / "scripts" / "run_app.py"
PUBLIC_ASSETS_DIR = PROJECT_ROOT / "src" / "app" / "web_assets"


def test_run_app_does_not_serve_project_root():
    source = RUN_APP.read_text(encoding="utf-8")
    assert "assets_dir=str(PROJECT_ROOT)" not in source
    assert "assets_dir=str(PUBLIC_ASSETS_DIR)" in source
    assert "PUBLIC_ASSETS_DIR" in source


def test_run_app_mounts_outputs_explicitly():
    """outputs/ must be served via an explicit StaticFiles mount, not via the
    Flet catch-all, so that video URLs never fall back to index.html."""
    source = RUN_APP.read_text(encoding="utf-8")
    assert 'mount("/outputs"' in source
    assert "OUTPUTS_DIR" in source


def test_public_assets_dir_exists_with_index():
    assert PUBLIC_ASSETS_DIR.is_dir()
    assert (PUBLIC_ASSETS_DIR / "index.html").is_file()


def test_public_assets_dir_contains_only_safe_files():
    """The public web root must not leak code or private artifacts."""
    forbidden_suffixes = {".py", ".npz", ".mp4", ".pt", ".pth", ".onnx"}
    for path in PUBLIC_ASSETS_DIR.rglob("*"):
        if path.is_file():
            assert path.suffix.lower() not in forbidden_suffixes, path
