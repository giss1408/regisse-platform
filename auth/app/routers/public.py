import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from app import tokens
from app.audit import client_ip
from app.config import get_settings
from app.db import get_session
from app.deps import Principal, current_principal, service_caller
from app.errors import BadRequest, Forbidden, NotFound, Unauthorized
from app.models import User
from app.services import accounts

router = APIRouter(prefix="/v1")

Phone = Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=32)]
Product = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,31}$")]


class RegisterIn(BaseModel):
    phone: Phone
    password: str = Field(min_length=8, max_length=200)
    display_name: str | None = Field(default=None, max_length=80)
    product: Product
    consent: bool


class RegisterOut(BaseModel):
    status: str = "pending_activation"
    message: str = "Un code de vérification va t’être envoyé sur WhatsApp."


class ActivateIn(BaseModel):
    phone: Phone
    code: str = Field(pattern=r"^\d{6}$")
    password: str | None = Field(default=None, min_length=8, max_length=200)  # only for an account the team enrolled without one


class LoginIn(BaseModel):
    phone: Phone | None = None
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=254)] | None = None  # back-office users
    password: str = Field(min_length=1, max_length=200)
    product: Product
    app: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,31}$")]
    totp: str | None = Field(default=None, pattern=r"^\d{6}$")


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=128)


class ResetRequestIn(BaseModel):
    phone: Phone
    product: Product


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class StepUpIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    totp: str | None = Field(default=None, pattern=r"^\d{6}$")


class DeleteMeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    check_only: bool = False  # verify the password and the right to delete, change nothing (lets the app check before it deletes elsewhere)


class IntrospectIn(BaseModel):
    token: str = Field(min_length=20, max_length=4096)


@router.post("/register", status_code=201, response_model=RegisterOut)
async def register(data: RegisterIn, request: Request, db: AsyncSession = Depends(get_session)) -> RegisterOut:
    await accounts.register(db, raw_phone=data.phone, password=data.password, display_name=data.display_name, product=data.product,
                            consent=data.consent, ip=client_ip(request))
    await db.commit()
    return RegisterOut()


@router.post("/activate")
async def activate(data: ActivateIn, request: Request, db: AsyncSession = Depends(get_session)) -> dict:
    accounts.ip_limit(get_settings().activate_ip_limit, "activate", client_ip(request))
    await accounts.activate(db, raw_phone=data.phone, code=data.code, password=data.password)
    await db.commit()
    return {"status": "active"}


@router.post("/login")
async def login(data: LoginIn, request: Request, db: AsyncSession = Depends(get_session)) -> dict:
    out = await accounts.login(db, raw_phone=data.phone, raw_email=data.email, password=data.password, product=data.product, app=data.app, totp=data.totp, ip=client_ip(request))
    await db.commit()
    return out


@router.post("/refresh")
async def refresh(data: RefreshIn, request: Request, db: AsyncSession = Depends(get_session)) -> dict:
    accounts.ip_limit(get_settings().refresh_ip_limit, "refresh", client_ip(request))
    out = await accounts.refresh(db, data.refresh_token)
    await db.commit()
    return out


@router.post("/logout", status_code=204)
async def logout(data: RefreshIn, db: AsyncSession = Depends(get_session)) -> None:
    await accounts.logout(db, data.refresh_token)
    await db.commit()


@router.post("/password/reset-request", status_code=202)
async def reset_request(data: ResetRequestIn, request: Request, db: AsyncSession = Depends(get_session)) -> dict:
    accounts.product_config(data.product)
    await accounts.request_password_reset(db, raw_phone=data.phone, product=data.product, ip=client_ip(request))
    await db.commit()
    return {"message": "Si ce numéro a un compte, un message WhatsApp va t’être envoyé."}  # same answer for every number


@router.post("/password/change", status_code=204)
async def change_password(data: ChangePasswordIn, p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_session)) -> None:
    await accounts.change_password(db, p.user_id, data.current_password, data.new_password, p.sid)
    await db.commit()


@router.post("/step-up")
async def step_up(data: StepUpIn, request: Request, p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_session)) -> dict:
    """Re-prove who you are before a sensitive action (wallet, staff, exports...). Returns a NEW access token for the same session
    with a fresh `auth_time`, and `amr` that says what was just proved (password, plus the authenticator code for roles that need
    one). Products ask for a recent `auth_time` on their sensitive routes; a stolen access token alone cannot pass."""
    out = await accounts.step_up(db, p, data.password, data.totp, client_ip(request))
    await db.commit()
    return out


@router.post("/me/delete", status_code=204)
async def delete_me(data: DeleteMeIn, request: Request, p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_session)) -> None:
    """Erase my identity in auth (the product deletes its own data first: auth is the last step). Password required."""
    await accounts.erase_self(db, p.user_id, data.current_password, client_ip(request), check_only=data.check_only)
    await db.commit()


class EraseForProductIn(BaseModel):
    phone: str | None = Field(default=None, min_length=6, max_length=32)
    user_id: uuid.UUID | None = None  # a product that keeps no phone number erases by the auth user id


@router.post("/service/erase")
async def service_erase(data: EraseForProductIn, request: Request, caller: str = Depends(service_caller), db: AsyncSession = Depends(get_session)) -> dict:
    """A product tells auth it erased a person (the self-service deletion or an admin acting on a request): her access to THAT product
    ends, and her identity is erased when nothing else is left. Service key, own product only; staff are refused; idempotent."""
    if (data.phone is None) == (data.user_id is None):
        raise BadRequest("phone_or_user_id")
    e164 = None
    if data.phone is not None:
        try:
            e164 = accounts.normalize_phone(data.phone)
        except Exception:
            return {"result": "unknown"}
    result = await accounts.erase_for_product(db, caller, e164, client_ip(request), user_id=data.user_id)
    await db.commit()
    return {"result": result}


class PhoneChangeIn(BaseModel):
    new_phone: Phone
    current_password: str = Field(min_length=1, max_length=200)


class PhoneConfirmIn(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


@router.post("/me/phone/request", status_code=202)
async def phone_change_request(data: PhoneChangeIn, request: Request, p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_session)) -> dict:
    """Move my account to another number. Password required; the code for the NEW number is sent by the team on WhatsApp."""
    masked = await accounts.request_phone_change(db, p, data.new_phone, data.current_password, client_ip(request))
    await db.commit()
    return {"new_phone_masked": masked, "status": "waiting_for_code"}


@router.post("/me/phone/confirm")
async def phone_change_confirm(data: PhoneConfirmIn, request: Request, p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_session)) -> dict:
    """The code from the new number. By default the change is SCHEDULED for `effective_at` (a security delay; the old number keeps working and was
    warned). Without a delay it takes effect now: every other session ends and this one renews (POST /v1/refresh) with the new number."""
    out = await accounts.confirm_phone_change(db, p, data.code, client_ip(request))
    await db.commit()
    return out


class ServiceStaffIn(BaseModel):
    phone: Phone
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
    role: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z_]{1,31}$")]
    # False: a number that already has an account is refused (a product with ONE role per person cannot turn a customer into staff)
    allow_existing: bool = True


@router.post("/service/staff", status_code=201)
async def service_staff_create(data: ServiceStaffIn, request: Request, caller: str = Depends(service_caller), db: AsyncSession = Depends(get_session)) -> dict:
    """The product's backend enrols one of its staff (roles listed in the product's `service_staff_roles` only) by phone: a pending account, an
    activation code for the team to send on WhatsApp, a password chosen by the person. The product decides which of its own admins may call this."""
    out = await accounts.service_create_staff(db, caller, raw_phone=data.phone, raw_email=None, display_name=data.display_name, role=data.role, ip=client_ip(request), allow_existing=data.allow_existing)
    await db.commit()
    return out


@router.get("/service/users/{user_id}")
async def service_user(user_id: uuid.UUID, caller: str = Depends(service_caller), db: AsyncSession = Depends(get_session)) -> dict:
    """What a product may know about one of ITS people: the name she gave when she signed up (so a profile opened from a token is not blank).
    Never the phone number. A person of another product does not exist for this key."""
    user = await db.get(User, user_id)
    if user is None or user.status != "active" or not any(m.product == caller for m in user.memberships):
        raise NotFound("user_not_found")
    return {"id": str(user.id), "display_name": user.display_name}


@router.delete("/service/staff/{user_id}")
async def service_staff_remove(user_id: uuid.UUID, request: Request, caller: str = Depends(service_caller), db: AsyncSession = Depends(get_session)) -> dict:
    result = await accounts.service_remove_staff(db, caller, user_id, client_ip(request))
    await db.commit()
    return {"result": result}


@router.get("/me")
async def me(p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_session)) -> dict:
    user = await db.get(User, p.user_id)
    if user is None or user.status != "active":
        raise Unauthorized("invalid_token")
    phone = accounts.user_phone(user)
    return {"id": str(user.id), "display_name": user.display_name, "phone_masked": accounts.kit_phone.mask_phone(phone) if phone else None,
            "product": p.product, "role": p.role, "roles": p.roles, "must_change_password": user.must_change_password}


@router.post("/introspect")
async def introspect(data: IntrospectIn, caller: str = Depends(service_caller), db: AsyncSession = Depends(get_session)) -> dict:
    """Online check for sensitive routes (money): is this token's session still alive and the user still active?
    Called by a product with its service key, and only about tokens issued FOR that product; products verify signatures
    locally for everything else."""
    try:
        claims = await tokens.decode_token(db, data.token)
    except Unauthorized:
        return {"active": False}
    if claims["aud"] != caller:
        return {"active": False}  # a product's key says nothing about another product's tokens
    user = await db.get(User, uuid.UUID(claims["sub"]))
    held = accounts._roles_for(user, claims["aud"]) if user else []
    alive = user is not None and user.status == "active" and claims["role"] in held and await accounts.session_active(db, claims["sid"])
    return {"active": bool(alive), "sub": claims["sub"], "aud": claims["aud"], "role": claims["role"]} if alive else {"active": False}


class SessionCheckIn(BaseModel):
    sid: str = Field(min_length=8, max_length=64)


@router.post("/sessions/check")
async def sessions_check(data: SessionCheckIn, caller: str = Depends(service_caller), db: AsyncSession = Depends(get_session)) -> dict:
    """For a product that keeps its own session next to an auth login (flariane back office): is the auth session `sid` still
    alive (not logged out, not revoked, user not disabled, role still held)? Service key required, for the key's own product only."""
    from sqlalchemy import select as _select

    from app.models import RefreshToken

    row = await db.scalar(_select(RefreshToken).where(RefreshToken.family == data.sid, RefreshToken.revoked_at.is_(None)).limit(1))
    if row is None or row.product != caller:
        return {"active": False}
    user = await db.get(User, row.user_id)
    alive = user is not None and user.status == "active" and row.role in accounts._roles_for(user, row.product)
    return {"active": bool(alive)}
