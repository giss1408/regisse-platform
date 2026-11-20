import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit, client_ip
from app.config import get_settings
from app.db import get_session
from app.deps import current_relay_admin, require_fresh, Principal, current_admin, current_super_admin
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
async def relay_queue(status: Literal["open", "sent", "pending", "all"] = "open", admin: Principal = Depends(current_relay_admin), db: AsyncSession = Depends(get_session)):
    q = select(RelayRequest).order_by(RelayRequest.created_at)
    if admin.scope:
        q = q.where(RelayRequest.product == admin.scope)
    if status == "pending":  # still to send, or sent and waiting for the person to type it: can be sent again
        q = q.where(RelayRequest.status.in_(["open", "sent"]))
    elif status != "all":
        q = q.where(RelayRequest.status == status)
    out = []
    for r in (await db.scalars(q.limit(200))).all():
        u = await db.get(User, r.user_id)
        phone = accounts.user_phone(u) if u else None
        out.append(QueueItem(id=r.id, ref_id=r.ref_id, purpose=r.purpose, product=r.product, status=r.status, display_name=u.display_name if u else None,
                             phone_masked=accounts.kit_phone.mask_phone(phone) if phone else None, created_at=r.created_at.isoformat()))
    return out


async def _req(db: AsyncSession, request_id: int, admin: Principal | None = None) -> RelayRequest:
    r = await db.get(RelayRequest, request_id)
    if r is None or (admin is not None and admin.scope and r.product != admin.scope):
        raise NotFound("request_not_found")  # another product's request does not exist for a product admin
    return r


@router.post("/relay/{request_id}/send")
async def relay_send(request_id: int, request: Request, admin: Principal = Depends(current_relay_admin), db: AsyncSession = Depends(get_session)) -> dict:
    """New code (or temporary password) + wa.me link: the admin sends it from their WhatsApp, the user types it in the app."""
    req = await _req(db, request_id, admin)
    if req.purpose == "password_reset":
        require_fresh(admin)  # a temporary password lets whoever types it into the account: password + a recent authenticator code, again
    url = await accounts.relay_send(db, req, admin.user_id, client_ip(request))
    await db.commit()
    return {"url": url}


@router.post("/relay/{request_id}/reject", status_code=204)
async def relay_reject(request_id: int, request: Request, admin: Principal = Depends(current_relay_admin), db: AsyncSession = Depends(get_session)) -> None:
    await accounts.relay_reject(db, await _req(db, request_id, admin), admin.user_id, client_ip(request))
    await db.commit()


@router.get("/users/search")
async def search_user(phone: str | None = None, email: str | None = None, admin: Principal = Depends(current_relay_admin), db: AsyncSession = Depends(get_session)) -> dict:
    if bool(phone) == bool(email):
        raise BadRequest("phone_or_email")
    if admin.scope and not phone:
        raise Forbidden("auth_admin_only")  # product admins look people up by phone, in their own product
    user = await accounts.user_by_phone(db, accounts.normalize_phone(phone)) if phone else await accounts.user_by_email(db, accounts.normalize_email(email or ""))
    if user is None or (admin.scope and not any(m.product == admin.scope for m in user.memberships)):
        raise NotFound("user_not_found")
    phone_masked = accounts.kit_phone.mask_phone(accounts.user_phone(user) or "") if user.phone_enc else None
    return {"id": str(user.id), "display_name": user.display_name, "status": user.status, "phone_masked": phone_masked,
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


def _guard_scoped_target(admin: Principal, user: User) -> None:
    """Acting on a user: an auth back-office admin follows `_guard_staff_target`; a product admin only touches people of her own product, never
    auth staff, and only a product super admin touches that product's staff (agents, admins)."""
    if not admin.scope:
        _guard_staff_target(admin, user)
        return
    if not any(m.product == admin.scope for m in user.memberships):
        raise NotFound("user_not_found")
    if any(m.product == "auth" for m in user.memberships):
        raise Forbidden("auth_admin_only")
    if accounts.is_privileged(user) and admin.role not in accounts.top_admin_roles(admin.scope):
        raise Forbidden("super_admin_only")


class EnableIn(BaseModel):
    note: str | None = Field(default=None, max_length=300)  # who was called and what was checked: required (10+) for a product's own admins


@router.get("/frozen-users")
async def frozen_users(admin: Principal = Depends(current_relay_admin), db: AsyncSession = Depends(get_session)) -> list[dict]:
    """Accounts that are switched off (frozen after "this wasn't me", or disabled by an admin) and can still be given back: erased accounts
    (no number any more) are not listed. For each, why and when, from the audit trail."""
    from app.models import AuditLog, Membership

    q = select(User).where(User.status == "disabled", User.phone_enc.is_not(None)).order_by(User.created_at.desc())
    if admin.scope:
        q = q.where(User.id.in_(select(Membership.user_id).where(Membership.product == admin.scope)))
    out = []
    for u in (await db.scalars(q.limit(200))).all():
        last = await db.scalar(select(AuditLog).where(AuditLog.entity == "user", AuditLog.entity_id == str(u.id),
                                                      AuditLog.action.in_(["user.phone_change_not_me", "user.disabled"])).order_by(AuditLog.id.desc()).limit(1))
        out.append({"id": str(u.id), "display_name": u.display_name, "phone_masked": accounts.kit_phone.mask_phone(accounts.user_phone(u) or ""),
                    "products": sorted({m.product for m in u.memberships}),
                    "reason": {"user.phone_change_not_me": "not_me", "user.disabled": "disabled"}.get(last.action if last else "", "unknown"),
                    "since": last.at.isoformat() + "Z" if last else None})
    return out


class ActivateNowIn(BaseModel):
    note: str = Field(min_length=10, max_length=300)  # who was called, on which number, what was checked


async def _scoped_change(db: AsyncSession, change_id: int, admin: Principal):
    from app.models import PhoneChange

    pc = await db.get(PhoneChange, change_id)
    if pc is None or (admin.scope and pc.product != admin.scope):
        raise NotFound("phone_change_not_found")
    return pc


@router.get("/phone-changes")
async def phone_changes(status: Literal["open", "scheduled", "requested", "all"] = "open", admin: Principal = Depends(current_relay_admin), db: AsyncSession = Depends(get_session)) -> list[dict]:
    """Number changes to follow: `open` = waiting for the code or for their delay, or done with a notice still to send. For each, the relay
    messages that can still be sent (`relays`: code to the NEW number, warning / notice to the OLD number)."""
    from app.models import PhoneChange

    q = select(PhoneChange).order_by(PhoneChange.created_at)
    if admin.scope:
        q = q.where(PhoneChange.product == admin.scope)
    if status in ("scheduled", "requested"):
        q = q.where(PhoneChange.status == status)
    out = []
    for pc in (await db.scalars(q.limit(300))).all():
        relays = {}
        for r in (await db.scalars(select(RelayRequest).where(RelayRequest.ref_id == pc.id, RelayRequest.status.in_(["open", "sent"]),
                                                              RelayRequest.purpose.in_(["phone_change", "phone_change_alert", "phone_changed_notice"])))).all():
            relays[{"phone_change": "code", "phone_change_alert": "alert", "phone_changed_notice": "notice"}[r.purpose]] = {"id": r.id, "status": r.status}
        if status == "open" and pc.status not in ("requested", "scheduled") and not (pc.status == "done" and "notice" in relays):
            continue
        u = await db.get(User, pc.user_id)
        out.append({"id": pc.id, "product": pc.product, "status": pc.status, "display_name": u.display_name if u else None,
                    "new_phone_masked": accounts.kit_phone.mask_phone(accounts.cipher().decrypt(pc.new_phone_enc)),
                    "old_phone_masked": accounts.kit_phone.mask_phone(accounts.cipher().decrypt(pc.old_phone_enc)) if pc.old_phone_enc else None,
                    "created_at": pc.created_at.isoformat() + "Z", "effective_at": pc.effective_at.isoformat() + "Z" if pc.effective_at else None,
                    "relays": relays, "user_id": str(pc.user_id)})
    return out


@router.post("/phone-changes/{change_id}/activate-now")
async def phone_change_activate_now(change_id: int, data: ActivateNowIn, request: Request, admin: Principal = Depends(current_relay_admin), db: AsyncSession = Depends(get_session)) -> dict:
    """After calling the person on the number she was using: the scheduled change takes effect now. A written note is required and kept in the audit."""
    require_fresh(admin)  # password + a recent authenticator code, again: this changes who owns an account
    await _scoped_change(db, change_id, admin)
    masked = await accounts.phone_change_activate_now(db, change_id, admin.user_id, data.note, client_ip(request))
    await db.commit()
    return {"status": "done", "phone_masked": masked}


@router.post("/phone-changes/{change_id}/not-me")
async def phone_change_not_me(change_id: int, request: Request, admin: Principal = Depends(current_relay_admin), db: AsyncSession = Depends(get_session)) -> dict:
    """"This wasn't me" on a number change (reported by WhatsApp reply or a call): cancels a pending request or puts a confirmed change back,
    freezes the account and ends its sessions. Re-enable it (`/users/{id}/enable`) after calling the person."""
    require_fresh(admin)
    await _scoped_change(db, change_id, admin)
    out = await accounts.phone_change_not_me(db, change_id, admin.user_id, client_ip(request))
    await db.commit()
    return out


@router.post("/users/{user_id}/disable", status_code=204)
async def disable_user(user_id: uuid.UUID, request: Request, data: EnableIn | None = None, admin: Principal = Depends(current_relay_admin), db: AsyncSession = Depends(get_session)) -> None:
    """Switches an account off and ends its sessions (a stolen phone, fraud, a request). A product's own admin acts on her product's people
    only, needs a recent proof and a written note (10+ characters, kept in the audit); the account is given back from `/frozen-users`."""
    user = await _user(db, user_id)
    _guard_scoped_target(admin, user)
    if user.id == admin.user_id:
        raise Conflict("cannot_disable_self")
    note = (data.note or "").strip() if data else ""
    if admin.scope:
        require_fresh(admin)
        if len(note) < 10:
            raise BadRequest("note_required")
    user.status = "disabled"
    await accounts.revoke_all_sessions(db, user.id)
    await audit(db, actor_type="admin", actor_id=admin.user_id, action="user.disabled", entity="user", entity_id=user.id, ip=client_ip(request), detail={"note": note} if note else None)
    await db.commit()


@router.post("/users/{user_id}/enable", status_code=204)
async def enable_user(user_id: uuid.UUID, request: Request, data: EnableIn | None = None, admin: Principal = Depends(current_relay_admin), db: AsyncSession = Depends(get_session)) -> None:
    """Gives a frozen or disabled account back (after calling the person). A product's own admin needs a recent proof and a written note
    (10+ characters, kept in the audit); an erased account (no number left) cannot be re-enabled."""
    user = await _user(db, user_id)
    _guard_scoped_target(admin, user)
    note = (data.note or "").strip() if data else ""
    if admin.scope:
        require_fresh(admin)
        if len(note) < 10:
            raise BadRequest("note_required")
    if user.status == "disabled":
        if user.phone_enc is None:
            raise Conflict("erased_account")
        user.status = "active"
    await audit(db, actor_type="admin", actor_id=admin.user_id, action="user.enabled", entity="user", entity_id=user.id, ip=client_ip(request), detail={"note": note} if note else None)
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
