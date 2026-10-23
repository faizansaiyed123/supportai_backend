"""
Verifies the append-only trigger defined in
alembic/versions/0002_audit_log_append_only_trigger.py actually works. tests/conftest.py builds
the test schema from SQLAlchemy metadata (Base.metadata.create_all), which does NOT run
migrations, so this test applies the same trigger SQL directly against the test DB rather than
relying on `alembic upgrade head` having been run - keeping this test self-contained and not
dependent on migration state, while still exercising the real trigger logic byte-for-byte
copied from the migration.
"""
import pytest
from sqlalchemy import text

from app.models.audit import AuditLog

_TRIGGER_FUNC_SQL = """
CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs rows are append-only and cannot be updated or deleted';
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER_UPDATE_SQL = """
CREATE TRIGGER audit_logs_no_update
BEFORE UPDATE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();
"""

_TRIGGER_DELETE_SQL = """
CREATE TRIGGER audit_logs_no_delete
BEFORE DELETE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();
"""


@pytest.mark.asyncio
async def test_audit_log_cannot_be_updated_or_deleted(db_session):
    await db_session.execute(text(_TRIGGER_FUNC_SQL))
    await db_session.execute(text(_TRIGGER_UPDATE_SQL))
    await db_session.execute(text(_TRIGGER_DELETE_SQL))
    await db_session.commit()

    log = AuditLog(org_id=None, actor_user_id=None, action="test.action", event_metadata={})
    db_session.add(log)
    await db_session.commit()

    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(text("UPDATE audit_logs SET action = 'tampered' WHERE id = :id"), {"id": log.id})
        await db_session.commit()
    await db_session.rollback()

    log2 = AuditLog(org_id=None, actor_user_id=None, action="test.action2", event_metadata={})
    db_session.add(log2)
    await db_session.commit()

    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": log2.id})
        await db_session.commit()
    await db_session.rollback()

