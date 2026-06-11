"""Security regression tests for media-serving primitives.

Covers the ?token= query auth used by <img>/<video> endpoints and the
filesystem containment helper used before serving any file off disk.
"""

import os
import uuid

import pytest
from fastapi import HTTPException

from shared.auth import create_access_token, require_query_token
from shared.paths import escape_like, resolve_inside

# ── require_query_token ──────────────────────────────────────────────


def test_missing_token_rejected():
    with pytest.raises(HTTPException) as ei:
        require_query_token(None)
    assert ei.value.status_code == 401


def test_empty_token_rejected():
    with pytest.raises(HTTPException) as ei:
        require_query_token("")
    assert ei.value.status_code == 401


def test_garbage_token_rejected():
    # decode_access_token returns None instead of raising, so a try/except
    # wrapper around it would let this through. The helper must reject it.
    with pytest.raises(HTTPException) as ei:
        require_query_token("not-a-jwt")
    assert ei.value.status_code == 401


def test_valid_token_returns_user_id():
    user_id = uuid.uuid4()
    token = create_access_token(user_id)
    assert require_query_token(token) == user_id


# ── resolve_inside ───────────────────────────────────────────────────


def test_file_inside_allowed_dir(tmp_path):
    base = tmp_path / "thumbs"
    base.mkdir()
    f = base / "a.jpg"
    f.write_bytes(b"x")
    assert resolve_inside(str(f), str(base)) == os.path.realpath(str(f))


def test_sibling_directory_rejected(tmp_path):
    # /data/thumbs-evil must not pass a check for /data/thumbs. A plain
    # startswith(allowed_dir) admits it.
    base = tmp_path / "thumbs"
    evil = tmp_path / "thumbs-evil"
    base.mkdir()
    evil.mkdir()
    f = evil / "a.jpg"
    f.write_bytes(b"x")
    assert resolve_inside(str(f), str(base)) is None


def test_dotdot_traversal_rejected(tmp_path):
    base = tmp_path / "thumbs"
    base.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"x")
    assert resolve_inside(str(base / ".." / "secret.txt"), str(base)) is None


def test_symlink_escaping_dir_rejected(tmp_path):
    base = tmp_path / "thumbs"
    base.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"x")
    link = base / "innocent.jpg"
    os.symlink(str(secret), str(link))
    assert resolve_inside(str(link), str(base)) is None


def test_none_and_empty_path_rejected(tmp_path):
    assert resolve_inside(None, str(tmp_path)) is None
    assert resolve_inside("", str(tmp_path)) is None


# ── escape_like ──────────────────────────────────────────────────────


def test_escape_like_escapes_metacharacters():
    assert escape_like("100%") == "100\\%"
    assert escape_like("a_b") == "a\\_b"
    assert escape_like("back\\slash") == "back\\\\slash"
    assert escape_like("plain") == "plain"
