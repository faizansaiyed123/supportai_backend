"""
Shared pytest fixtures. Uses a real Postgres test database (not sqlite - pgvector and
Postgres-specific features like tsvector aren't sqlite-compatible). Point TEST_DATABASE_URL at
a disposable Postgres+pgvector database.
"""
import asyncio
import os

import pytest
import pytest_asyncio
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.rate_limit import (
    _ask_question_limiter,
    _login_limiter,
    _password_reset_limiter,
    _register_limiter,
    _ticket_write_limiter,
    _upload_limiter,
)
from app.db.base import Base, get_db
from app.main import app as fastapi_app
from app.models.organization import Role

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://supportai:supportai_dev_password@db:5432/supportai_test",
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def reset_rate_limits():
    # The limiters are built lazily from settings but memoized, so calling the accessors here
    # returns the same instances the request handlers use - clearing their buckets is what
    # keeps one test's traffic from tripping the next test's limiter.
    for limiter in (
        _login_limiter(),
        _register_limiter(),
        _password_reset_limiter(),
        _upload_limiter(),
        _ask_question_limiter(),
        _ticket_write_limiter(),
    ):
        limiter._buckets.clear()
    yield


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(sqlalchemy.text("DROP SCHEMA public CASCADE"))
        await conn.execute(sqlalchemy.text("CREATE SCHEMA public"))
        await conn.execute(sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.execute(sqlalchemy.text("DROP SCHEMA public CASCADE"))
        await conn.execute(sqlalchemy.text("CREATE SCHEMA public"))
        await conn.execute(sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS vector"))
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_roles(db_session):
    """Seed the four system roles (owner/admin/agent/viewer) if not already present.
    Many tests need these roles to exist for register/login to work; this fixture
    is NOT autouse so that tests that need a fresh, empty roles table (e.g.
    role-permission audits) opt in explicitly and control their own seeding."""
    from sqlalchemy import select

    existing = await db_session.execute(
        select(Role).where(Role.org_id.is_(None))
    )
    roles_present = {r.name: r for r in existing.scalars().all()}

    system_roles = {
        "owner": ["*"],
        "admin": ["memberships:manage", "audit_logs:read", "knowledge:create", "knowledge:manage", "customers:manage", "tickets:manage", "tickets:read_all"],
        "agent": ["knowledge:read", "tickets:read", "tickets:manage", "customers:read", "members:read"],
        "viewer": ["knowledge:read", "customers:read", "tickets:read"],
    }

    for name, perms in system_roles.items():
        if name not in roles_present:
            db_session.add(Role(org_id=None, name=name, permissions=perms))
    await db_session.commit()
    return roles_present


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()

