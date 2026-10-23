import pytest
from sqlalchemy import select

from app.models.organization import Membership, Organization, Role
from app.models.user import User


@pytest.mark.asyncio
async def test_create_org_role_user_membership_round_trip(db_session):
    org = Organization(name="Acme Inc", slug="acme")
    role = Role(org_id=None, name="owner", permissions=["*"])
    user = User(email="owner@acme.com", password_hash="not-a-real-hash-for-this-test", full_name="Owner User")
    db_session.add_all([org, role, user])
    await db_session.flush()

    membership = Membership(org_id=org.id, user_id=user.id, role_id=role.id, status="active")
    db_session.add(membership)
    await db_session.commit()

    result = await db_session.execute(select(Membership).where(Membership.org_id == org.id))
    fetched = result.scalar_one()
    assert fetched.user_id == user.id
    assert fetched.status == "active"


@pytest.mark.asyncio
async def test_membership_unique_constraint_prevents_duplicate(db_session):
    org = Organization(name="Acme Inc", slug="acme2")
    role = Role(org_id=None, name="owner", permissions=["*"])
    user = User(email="dupe@acme.com", password_hash="x", full_name="Dupe User")
    db_session.add_all([org, role, user])
    await db_session.flush()

    db_session.add(Membership(org_id=org.id, user_id=user.id, role_id=role.id))
    await db_session.commit()

    db_session.add(Membership(org_id=org.id, user_id=user.id, role_id=role.id))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()

