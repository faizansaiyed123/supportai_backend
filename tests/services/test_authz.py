import pytest

from app.services.authz import actor_has_permission


@pytest.mark.asyncio
async def test_owner_wildcard_grants_everything(db_session, seeded_roles):
    assert await actor_has_permission(
        db_session, actor_role="owner", org_id=None, permission="knowledge:manage"
    )
    assert await actor_has_permission(
        db_session, actor_role="owner", org_id=None, permission="anything:at_all"
    )


@pytest.mark.asyncio
async def test_viewer_cannot_manage_knowledge(db_session, seeded_roles):
    assert not await actor_has_permission(
        db_session, actor_role="viewer", org_id=None, permission="knowledge:manage"
    )
    assert await actor_has_permission(
        db_session, actor_role="viewer", org_id=None, permission="tickets:read"
    )


@pytest.mark.asyncio
async def test_unknown_role_has_no_permissions(db_session):
    assert not await actor_has_permission(
        db_session, actor_role="nonexistent_role", org_id=None, permission="knowledge:manage"
    )

