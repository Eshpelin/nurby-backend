import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from shared.auth import (
    API_KEY_PREFIX,
    _user_from_api_key,
    generate_api_key,
    hash_api_key,
)


def test_generate_api_key_shape():
    plaintext, key_hash, prefix = generate_api_key()
    assert plaintext.startswith(API_KEY_PREFIX)
    assert len(key_hash) == 64  # sha256 hex
    assert prefix == plaintext[:12]
    assert hash_api_key(plaintext) == key_hash


def test_hash_is_deterministic_and_distinct():
    a, _, _ = generate_api_key()
    b, _, _ = generate_api_key()
    assert hash_api_key(a) == hash_api_key(a)
    assert hash_api_key(a) != hash_api_key(b)


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeDB:
    def __init__(self, key_row, user):
        self._key = key_row
        self._user = user
        self.committed = False

    async def execute(self, stmt):
        return _FakeResult(self._key)

    async def get(self, model, ident):
        return self._user

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass


def _key(**over):
    base = dict(
        key_hash="x",
        revoked_at=None,
        expires_at=None,
        last_used_at=None,
        user_id=uuid.uuid4(),
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_api_key_resolves_to_user():
    user = SimpleNamespace(id=uuid.uuid4(), is_active=True)
    krow = _key(user_id=user.id)
    db = _FakeDB(krow, user)
    out = await _user_from_api_key("nrb_whatever", db)
    assert out is user
    assert db.committed  # stamped last_used_at


@pytest.mark.asyncio
async def test_api_key_revoked_rejected():
    user = SimpleNamespace(id=uuid.uuid4(), is_active=True)
    krow = _key(revoked_at=datetime.now(timezone.utc))
    db = _FakeDB(krow, user)
    assert await _user_from_api_key("nrb_x", db) is None


@pytest.mark.asyncio
async def test_api_key_expired_rejected():
    user = SimpleNamespace(id=uuid.uuid4(), is_active=True)
    krow = _key(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    db = _FakeDB(krow, user)
    assert await _user_from_api_key("nrb_x", db) is None


@pytest.mark.asyncio
async def test_api_key_unknown_rejected():
    db = _FakeDB(None, None)
    assert await _user_from_api_key("nrb_x", db) is None


@pytest.mark.asyncio
async def test_api_key_inactive_user_rejected():
    user = SimpleNamespace(id=uuid.uuid4(), is_active=False)
    krow = _key(user_id=user.id)
    db = _FakeDB(krow, user)
    assert await _user_from_api_key("nrb_x", db) is None
