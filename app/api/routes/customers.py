"""Customer endpoints (Phase 5 gap closed - tickets/conversations need real customer records)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, require_permission
from app.db.base import get_db
from app.schemas.customer import CustomerCreateRequest, CustomerListResponse, CustomerPublic
from app.services import customer as customer_service

router = APIRouter()


@router.post("/orgs/{org_id}/customers", response_model=CustomerPublic, status_code=status.HTTP_201_CREATED)
async def create_customer(
    org_id: uuid.UUID,
    payload: CustomerCreateRequest,
    actor: Actor = Depends(require_permission("customers:manage")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    return await customer_service.create_customer(
        db, actor=actor, external_id=payload.external_id, email=payload.email, name=payload.name
    )


@router.get("/orgs/{org_id}/customers", response_model=CustomerListResponse)
async def list_customers(
    org_id: uuid.UUID,
    actor: Actor = Depends(require_permission("customers:read")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    customers = await customer_service.list_customers(db, actor=actor)
    return CustomerListResponse(customers=customers)  # type: ignore[arg-type]


@router.get("/orgs/{org_id}/customers/{customer_id}", response_model=CustomerPublic)
async def get_customer(
    org_id: uuid.UUID,
    customer_id: uuid.UUID,
    actor: Actor = Depends(require_permission("customers:read")),
    db: AsyncSession = Depends(get_db),
):
    if org_id != actor.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization mismatch")
    try:
        return await customer_service.get_customer(db, actor=actor, customer_id=customer_id)
    except customer_service.CustomerNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found") from None
