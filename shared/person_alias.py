"""Household-wide person nickname resolution.

Nicknames are a pure view-layer concern. Stored data, Journey
``subject_key`` signatures, and face/body cluster naming all key persons
by their canonical ``display_name``. These helpers translate canonical
names into the household nickname at render time, and translate a
user-typed name (possibly a nickname) back to canonical names before a
query touches stored data.

Keep all nickname logic here so the view/query boundary stays in one
place instead of scattering ``person.nickname or person.display_name``
across every surface.
"""
from __future__ import annotations

from sqlalchemy import func, or_, select

from shared.models import Person


def display_name_for(person) -> str:
    """The name to show for a person. Nickname when set, else canonical.

    Accepts any object exposing ``nickname`` and ``display_name``
    (ORM row or a lightweight namespace).
    """
    nick = getattr(person, "nickname", None)
    name = getattr(person, "display_name", None) or ""
    if isinstance(nick, str) and nick.strip():
        return nick.strip()
    return name


def build_alias_map(persons) -> dict[str, str]:
    """Map canonical display_name -> nickname for persons that have one.

    Used to rewrite names pulled out of a Journey ``subject_key`` (which
    is always canonical) into household nicknames. Persons without a
    nickname are omitted so callers fall back to the canonical name.
    """
    out: dict[str, str] = {}
    for p in persons:
        nick = getattr(p, "nickname", None)
        name = getattr(p, "display_name", None)
        if name and isinstance(nick, str) and nick.strip():
            out[name] = nick.strip()
    return out


def alias_names(names, alias_map: dict[str, str]) -> list[str]:
    """Rewrite a list of canonical names through an alias map."""
    return [alias_map.get(n, n) for n in names]


async def resolve_name_to_canonical(db, text: str | None) -> list[str]:
    """Reverse map. Given a name the user typed, return canonical
    display_names of matching persons.

    Matches case-insensitively on either the nickname or the canonical
    display_name, so "mommy", "Salma", and "salma" all resolve to the
    canonical "Salma Bekom". Returns an empty list when nothing matches,
    letting callers fall back to a literal name match.
    """
    t = (text or "").strip()
    if not t:
        return []
    stmt = select(Person.display_name).where(
        or_(
            func.lower(Person.display_name) == t.lower(),
            func.lower(Person.nickname) == t.lower(),
        )
    )
    rows = (await db.execute(stmt)).scalars().all()
    # de-dupe, preserve order
    return list(dict.fromkeys(r for r in rows if r))
