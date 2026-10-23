"""
Audit logging (Sec. 50, closes a gap flagged in TODO.md/docs/DATABASE.md - the table existed
but wasn't written to). Plain function per D004. Call this from any service function that
performs a security-sensitive or administratively-significant action.

Deliberately does not raise on failure to write an audit row beyond letting the caller's own
transaction handling apply - an audit-log write failure should not silently swallow the
primary action's success, but it also shouldn't be allowed to corrupt a successful primary
action's state. Callers add the AuditLog row to the same session/transaction as the action
being audited, so both commit or roll back together (atomicity of "the action happened" and
"the action was logged").
"""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def record_audit_event(
    db: AsyncSession,
    *,
    org_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    metadata: dict | None = None,
) -> None:
    """Adds an AuditLog row to the session WITHOUT committing - caller commits as part of its
    own transaction, so the audited action and its log entry are atomic together."""
    db.add(
        AuditLog(
            org_id=org_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            event_metadata=metadata or {},
        )
    )
