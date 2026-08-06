"""Regression tests for EnterpriseGuard outbound-DLP hardening.

Two gaps in the "black box" guarantee (files/secrets must never leave the
protected zone), each demonstrated then fixed:

  1. check_path() matched the literal path string only — a symlink into a
     protected zone, or a non-canonical/relative spelling of a path inside it,
     evaded a glob written for the canonical location.
  2. check_text() truncated outbound content to the first 64 KiB — any
     credential past that offset went out unscanned.
"""
from __future__ import annotations

import os

import pytest

from synthelion.enterprise_guard import (
    EnterpriseGuard,
    _SCAN_CAP_BYTES,
)


def _guard(**overrides):
    cfg = {
        "enabled": True,
        "content_categories": {},  # all detectors default-on
        "blocked_paths": overrides.pop("blocked_paths", ["**/fatture/**", "*.pem"]),
        "use_default_blocked_paths": False,
    }
    cfg.update(overrides)
    return EnterpriseGuard(cfg)


# --------------------------------------------------------------------------
# Fix 1 — path canonicalization
# --------------------------------------------------------------------------

def test_direct_zone_path_still_blocked():
    g = _guard()
    assert g.check_path("/home/u/work/fatture/2026/f001.pdf").blocked


def test_dotdot_traversal_into_zone_blocked():
    g = _guard()
    assert g.check_path("/home/u/work/fatture/../fatture/f001.pdf").blocked


def test_relative_path_into_zone_blocked(tmp_path, monkeypatch):
    # 'fatture/f001.pdf' has no leading segment for '**/fatture/**' to anchor
    # on; resolving it against cwd makes the match unambiguous.
    monkeypatch.chdir(tmp_path)
    g = _guard()
    assert g.check_path("fatture/f001.pdf").blocked


def test_symlink_into_zone_blocked(tmp_path):
    """The core black-box case: a symlink whose literal path is outside every
    zone glob but which resolves *into* a protected zone must be blocked."""
    zone = tmp_path / "fatture"
    zone.mkdir()
    secret = zone / "f001.pdf"
    secret.write_text("x")
    link_dir = tmp_path / "link"
    try:
        os.symlink(zone, link_dir)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/filesystem")

    g = _guard(blocked_paths=[f"{tmp_path.as_posix()}/fatture/**"])
    via_link = (link_dir / "f001.pdf").as_posix()
    # Literal path is under /link/, not /fatture/ — only canonicalization catches it.
    assert g.check_path(via_link).blocked


def test_unrelated_path_not_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    g = _guard()
    assert not g.check_path("/home/u/work/report/summary.pdf").blocked
    assert not g.check_path("notes/todo.txt").blocked


# --------------------------------------------------------------------------
# Fix 2 — full-content scan (no 64 KiB truncation)
# --------------------------------------------------------------------------

_AWS_KEY = "AKIAIOSFODNN7EXAMPLE1234"  # AWS-access-key shape


def test_secret_within_first_window_blocked():
    g = _guard()
    assert g.check_text(f"prefix {_AWS_KEY}").blocked


def test_secret_past_64kib_blocked():
    """Padding the secret past the old 64 KiB cap must no longer smuggle it out."""
    g = _guard()
    payload = ("x" * (_SCAN_CAP_BYTES + 5000)) + " " + _AWS_KEY
    assert g.check_text(payload).blocked


def test_secret_straddling_window_boundary_blocked():
    """A secret placed exactly across a window edge must still be caught by the
    overlap, not split between two windows."""
    g = _guard()
    payload = ("x" * (_SCAN_CAP_BYTES - 5)) + _AWS_KEY + ("y" * 40000)
    assert g.check_text(payload).blocked


def test_clean_large_content_not_blocked():
    g = _guard()
    assert not g.check_text("y" * (_SCAN_CAP_BYTES * 2)).blocked
