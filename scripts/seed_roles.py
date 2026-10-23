"""
Seeds system roles (org_id=NULL): owner, admin, agent, viewer. Idempotent - safe to re-run.
Run once per environment before any registration/permission check can succeed. Invoke with:
`python -m scripts.seed_roles` from backend/.

Permission strings are explicit per role rather than relying on any implied hierarchy (e.g.
"tickets:manage" does NOT automatically grant "tickets:read" - each is listed separately where
a role should have it). This was a deliberate fix made while wiring fine-grained permission
checks into routes (D026): an earlier draft assumed "manage implies read", which would have
silently locked agents out of ticket-read-gated endpoints despite having ticket-manage. Explicit
lists avoid that whole class of bug.
"""
import asyncio

from sqlalchemy import select

from app.db.base import AsyncSessionLocal
from app.models.organization import Role

SYSTEM_ROLES = {
    "owner": ["*"],
    "admin": [
        "org:manage",
        "members:manage",
        "members:read",
        "knowledge:manage",
        "knowledge:read",
        "conversations:manage",
        "conversations:read",
        "conversations:respond",
        "tickets:manage",
        "tickets:read",
        "customers:manage",
        "customers:read",
    ],
    "agent": [
        "members:read",
        "conversations:read",
        "conversations:respond",
        "tickets:read",
        "tickets:manage",
        "knowledge:read",
        "customers:read",
        "customers:manage",
    ],
    "viewer": [
        "members:read",
        "conversations:read",
        "tickets:read",
        "knowledge:read",
        "customers:read",
    ],
}


async def seed_roles() -> None:
    async with AsyncSessionLocal() as db:
        for name, permissions in SYSTEM_ROLES.items():
            existing = await db.execute(select(Role).where(Role.org_id.is_(None), Role.name == name))
            existing_role = existing.scalar_one_or_none()
            if existing_role is not None:
                # Idempotent AND self-healing: if permissions were changed here (as they were
                # this session, see docstring), re-running the seed script updates an existing
                # environment's roles rather than requiring a manual fix or a migration.
                existing_role.permissions = permissions
                continue
            db.add(Role(org_id=None, name=name, permissions=permissions))
        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed_roles())
