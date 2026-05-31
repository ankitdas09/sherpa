"""Seed a KB + two users for end-to-end testing.

  * a KB named 'default'
  * alice  -> granted access to the KB
  * bob    -> NO access (used to prove the access filter returns [])

Idempotent: re-running reuses existing rows by name/email. Prints the IDs you
need for curl-driven verification.
"""

from __future__ import annotations

from sqlalchemy import select

from sherpa.db.models import KBAccess, KnowledgeBase, User
from sherpa.db.session import session_scope


def _get_or_create_kb(s, name: str) -> KnowledgeBase:
    kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.name == name))
    if kb is None:
        kb = KnowledgeBase(name=name, description="Seeded default knowledge base")
        s.add(kb)
        s.flush()
    return kb


def _get_or_create_user(s, email: str) -> User:
    u = s.scalar(select(User).where(User.email == email))
    if u is None:
        u = User(email=email)
        s.add(u)
        s.flush()
    return u


def _grant(s, kb: KnowledgeBase, user: User) -> None:
    exists = s.scalar(
        select(KBAccess).where(KBAccess.kb_id == kb.kb_id, KBAccess.user_id == user.user_id)
    )
    if exists is None:
        s.add(KBAccess(kb_id=kb.kb_id, user_id=user.user_id))


def main() -> None:
    with session_scope() as s:
        kb = _get_or_create_kb(s, "default")
        alice = _get_or_create_user(s, "alice@example.com")
        bob = _get_or_create_user(s, "bob@example.com")
        _grant(s, kb, alice)  # alice can see the KB; bob cannot
        s.flush()

        print("Seed complete:")
        print(f"  KB   'default'  kb_id   = {kb.kb_id}")
        print(f"  USER alice      user_id = {alice.user_id}   (HAS access)")
        print(f"  USER bob        user_id = {bob.user_id}   (NO access)")


if __name__ == "__main__":
    main()
