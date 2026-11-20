import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit, client_ip
from app.config import get_settings
from app.db import get_session
from app.deps import Principal, current_admin, current_super_admin
from app.errors import BadRequest, Conflict, Forbidden, NotFound
from app.models import Membership, RelayRequest, User
from app.services import accounts
from app.tokens import utcnow

router = APIRouter(prefix="/v1/admin")

STAFF_ROLES = {"super_admin", "admin"}


class QueueItem(BaseModel):
    id: int
    ref_id: int | None = None
    purpose: str
    product: str
    status: str
    display_name: str | None
    phone_masked: str | None
    created_at: str


@router.get("/relay-queue", response_model=list[QueueItem])
async def relay_queue(status: Literal["open", "sent", "all"] = "open", _: Principal = Depends(current_admin), db: AsyncSession = Depends(get_session)):
    q = select(RelayRequest).order_by(RelayRequest.created_at)
    if status != "all":
        q = q.where(RelayRequest.status == status)
    out = []
    for r in (await db.scalars(q.limit(200))).all():
        u = await db.get(User, r.user_id)
        phone = accounts.user_phone(u) if u else None
        out.append(QueueItem(id=r.id, ref_id=r.ref_id, purpose=r.purpose, product=r.product, status=r.status, display_name=u.display_name if u else None,
                             phone_masked=accounts.kit_phone.mask_phone(phone) if phone else None, created_at=r.created_at.isoformat()))
    return out


async def _req(db: AsyncSession, request_id: int) -> RelayRequest:
    r = await db.get(RelayRequest, request_id)
    if r is None:
        raise NotFound("request_not_found")
    return r


@router.post("/relay/{request_id}/send")
async def relay_send(request_id: int, request: Request, admin: Principal = Depends(current_admin), db: AsyncSession = Depends(get_session)) -> dict:
    """New code (or temporary password) + wa.me link: the admin sends it from their WhatsApp, the user types it in the app."""
    url = await accounts.relay_send(db, await _req(db, request_id), admin.user_id, client_ip(request))
    await db.commit()
    return {"url": url}


@router.post("/relay/{request_id}/reject", status_code=204)
async def relay_reject(request_id: int, request: Request, admin: Principal = Depends(current_admin), db: AsyncSession = Depends(get_session)) -> None:
    await accounts.relay_reject(db, await _req(db, request_id), admin.user_id, client_ip(request))
    await db.commit()


@router.get("/users/search")
async def search_user(phone: str | None = None, email: str | None = None, _: Principal = Depends(current_admin), db: AsyncSession = Depends(get_session)) -> dict:
    if bool(phone) == bool(email):
        raise BadRequest("phone_or_email")
    user = await accounts.user_by_phone(db, accounts.normalize_phone(phone)) if phone else await accounts.user_by_email(db, accounts.normalize_email(email or ""))
    if user is None:
        raise NotFound("user_not_found")
    return {"id": str(user.id), "display_name": user.display_name, "status": user.status,
            "memberships": [{"product": m.product, "role": m.role} for m in user.memberships]}


async def _user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFound("user_not_found")
    return user


def _guard_staff_target(admin: Principal, user: User) -> None:
    """Only a super admin acts on staff accounts; an admin cannot touch another admin or a super admin."""
    if admin.role != "super_admin" and any(m.product == "auth" and m.role in STAFF_ROLES for m in user.memberships):
        raise Forbidden("super_admin_only")


@router.post("/phone-changes/{change_id}/not-me")
async def phone_change_not_me(change_id: int, request: Request, admin: Principal = Depends(current_admin), db: AsyncSession = Depends(get_session)) -> dict:
    """"This wasn't me" on a number change (reported by WhatsApp reply or a call): cancels a pending request or puts a confirmed change back,
    freezes the account and ends its sessions. Re-enable it (`/users/{id}/enable`) after calling the person."""
    out = await accounts.phone_change_not_me(db, change_id, admin.user_id, client_ip(request))
    await db.commit()
    return out


@router.post("/users/{user_id}/disable", status_code=204)
async def disable_user(user_id: uuid.UUID, request: Request, admin: Principal = Depends(current_admin), db: AsyncSession = Depends(get_session)) -> None:
    user = await _user(db, user_id)
    _guard_staff_target(admin, user)
    if user.id == admin.user_id:
        raise Conflict("cannot_disable_self")
    user.status = "disabled"
    await accounts.revoke_all_sessions(db, user.id)
    await audit(db, actor_type="admin", actor_id=admin.user_id, action="user.disabled", entity="user", entity_id=user.id, ip=client_ip(request))
    await db.commit()


@router.post("/users/{user_id}/enable", status_code=204)
async def enable_user(user_id: uuid.UUID, request: Request, admin: Principal = Depends(current_admin), db: AsyncSession = Depends(get_session)) -> None:
    user = await _user(db, user_id)
    _guard_staff_target(admin, user)
    if user.status == "disabled":
        user.status = "active"
    await audit(db, actor_type="admin", actor_id=admin.user_id, action="user.enabled", entity="user", entity_id=user.id, ip=client_ip(request))
    await db.commit()


@router.post("/users/{user_id}/revoke-sessions", status_code=204)
async def revoke_sessions(user_id: uuid.UUID, request: Request, admin: Principal = Depends(current_admin), db: AsyncSession = Depends(get_session)) -> None:
    user = await _user(db, user_id)
    _guard_staff_target(admin, user)
    await accounts.revoke_all_sessions(db, user.id)
    await audit(db, actor_type="admin", actor_id=admin.user_id, action="user.sessions_revoked", entity="user", entity_id=user.id, ip=client_ip(request))
    await db.commit()


class MembershipIn(BaseModel):
    user_id: uuid.UUID
    product: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,31}$")]
    role: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z_]{1,31}$")]


@router.post("/memberships", status_code=201)
async def grant_membership(data: MembershipIn, request: Request, admin: Principal = Depends(current_admin), db: AsyncSession = Depends(get_session)) -> dict:
    """Grants a role in a product. Roles on the `auth` product (staff) and privileged roles need a super admin."""
    cfg = accounts.product_config(data.product)
    known = {r for roles in cfg["apps"].values() for r in roles}
    if data.role not in known:
        raise NotFound("unknown_role")
    if admin.role != "super_admin" and (data.product == "auth" or data.role in accounts.PRIVILEGED_ROLES):
        raise Forbidden("super_admin_only")
    user = await _user(db, data.user_id)
    if any(m.product == data.product and m.role == data.role for m in user.memberships):
        raise Conflict("already_member")
    db.add(Membership(user_id=user.id, product=data.product, role=data.role, created_by=admin.user_id))
    await audit(db, actor_type="admin", actor_id=admin.user_id, action="membership.granted", entity="user", entity_id=user.id, ip=client_ip(request),
                detail={"product": data.product, "role": data.role})
    await db.commit()
    return {"status": "granted"}


@router.delete("/memberships", status_code=204)
async def revoke_membership(data: MembershipIn, request: Request, admin: Principal = Depends(current_admin), db: AsyncSession = Depends(get_session)) -> None:
    if admin.role != "super_admin" and (data.product == "auth" or data.role in accounts.PRIVILEGED_ROLES):
        raise Forbidden("super_admin_only")
    m = await db.scalar(select(Membership).where(Membership.user_id == data.user_id, Membership.product == data.product, Membership.role == data.role))
    if m is None:
        raise NotFound("membership_not_found")
    await db.delete(m)
    await audit(db, actor_type="admin", actor_id=admin.user_id, action="membership.revoked", entity="user", entity_id=data.user_id, ip=client_ip(request),
                detail={"product": data.product, "role": data.role})
    await db.commit()


class StaffIn(BaseModel):
    phone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=32)] | None = None
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=254)] | None = None
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
    product: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,31}$")]
    role: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z_]{1,31}$")]


class StaffResetIn(BaseModel):
    reset_totp: bool = False


@router.post("/staff", status_code=201)
async def create_staff(data: StaffIn, request: Request, admin: Principal = Depends(current_super_admin), db: AsyncSession = Depends(get_session)) -> dict:
    """Super admin only. Returns the temporary password (and authenticator secret when the role needs one) ONCE, to hand over in person."""
    out = await accounts.create_staff(db, raw_phone=data.phone, raw_email=data.email, display_name=data.display_name, product=data.product, role=data.role,
                                      admin_id=admin.user_id, ip=client_ip(request))
    await db.commit()
    return out


@router.post("/staff/{user_id}/reset")
async def reset_staff(user_id: uuid.UUID, data: StaffResetIn, request: Request, admin: Principal = Depends(current_super_admin), db: AsyncSession = Depends(get_session)) -> dict:
    out = await accounts.reset_staff(db, user_id, reset_totp=data.reset_totp, admin_id=admin.user_id, ip=client_ip(request))
    await db.commit()
    return out
