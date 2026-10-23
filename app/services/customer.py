"""Customer management business logic (Phase 5 gap closed). Plain functions per D004."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor
from app.models.ticket import Customer
from app.services.audit import record_audit_event


class CustomerNotFoundError(Exception):
    pass


async def create_customer(
    db: AsyncSession,
    *,
    actor: Actor,
    external_id: str | None = None,
    email: str | None = None,
    name: str | None = None,
) -> Customer:
    customer = Customer(org_id=actor.org_id, external_id=external_id, email=email, name=name)
    db.add(customer)
    await db.flush()
    await record_audit_event(
        db, org_id=actor.org_id, actor_user_id=actor.user_id, action="customer.created",
        target_type="customer", target_id=customer.id,
    )
    await db.commit()
    await db.refresh(customer)
    return customer


async def get_customer(db: AsyncSession, *, actor: Actor, customer_id: uuid.UUID) -> Customer:
    result = await db.execute(
        select(Customer).where(Customer.id == customer_id, Customer.org_id == actor.org_id)
    )
    customer = result.scalar_one_or_none()
    if customer is None:
        raise CustomerNotFoundError()
    return customer


async def list_customers(db: AsyncSession, *, actor: Actor, limit: int = 50, offset: int = 0) -> list[Customer]:
    result = await db.execute(
        select(Customer)
        .where(Customer.org_id == actor.org_id)
        .order_by(Customer.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())
