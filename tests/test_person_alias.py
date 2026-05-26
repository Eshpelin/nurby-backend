from types import SimpleNamespace

import pytest

from shared.person_alias import (
    alias_names,
    build_alias_map,
    display_name_for,
    resolve_name_to_canonical,
)


def _p(name, nick=None):
    return SimpleNamespace(display_name=name, nickname=nick)


def test_display_name_for_prefers_nickname():
    assert display_name_for(_p("Salma Bekom", "Mommy")) == "Mommy"


def test_display_name_for_falls_back_to_canonical():
    assert display_name_for(_p("Salma Bekom", None)) == "Salma Bekom"
    assert display_name_for(_p("Salma Bekom", "   ")) == "Salma Bekom"


def test_display_name_for_strips_whitespace():
    assert display_name_for(_p("Leah", "  Lee  ")) == "Lee"


def test_build_alias_map_only_includes_nicknamed():
    persons = [_p("Salma Bekom", "Mommy"), _p("Leah", "Lee"), _p("Bob", None)]
    m = build_alias_map(persons)
    assert m == {"Salma Bekom": "Mommy", "Leah": "Lee"}
    assert "Bob" not in m


def test_alias_names_rewrites_known_and_passes_through_unknown():
    m = {"Salma Bekom": "Mommy"}
    assert alias_names(["Salma Bekom", "Bob"], m) == ["Mommy", "Bob"]


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    """Returns canonical names for any of these inputs, mimicking the
    OR(lower(display_name)==t, lower(nickname)==t) match."""

    def __init__(self, people):
        # list of (display_name, nickname)
        self.people = people

    async def execute(self, stmt):
        # We cannot introspect the bound param cheaply, so the test wraps
        # this DB per-query by stashing the expected matches.
        return _FakeResult(self._matches)

    def expect(self, text):
        t = text.strip().lower()
        self._matches = [
            dn for (dn, nk) in self.people
            if dn.lower() == t or (nk and nk.lower() == t)
        ]
        return self


@pytest.mark.asyncio
async def test_resolve_name_to_canonical_matches_nickname():
    db = _FakeDB([("Salma Bekom", "Mommy"), ("Leah", "Lee")])
    out = await resolve_name_to_canonical(db.expect("mommy"), "mommy")
    assert out == ["Salma Bekom"]


@pytest.mark.asyncio
async def test_resolve_name_to_canonical_matches_canonical():
    db = _FakeDB([("Salma Bekom", "Mommy")])
    out = await resolve_name_to_canonical(db.expect("Salma Bekom"), "Salma Bekom")
    assert out == ["Salma Bekom"]


@pytest.mark.asyncio
async def test_resolve_name_to_canonical_empty_on_blank():
    db = _FakeDB([("Salma Bekom", "Mommy")])
    assert await resolve_name_to_canonical(db, "") == []
    assert await resolve_name_to_canonical(db, None) == []
