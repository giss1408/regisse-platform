"""Registration, activation, login, sessions, relay (flariane's wa.me model; reuses regisse_kit)."""

import re
import secrets
import uuid
from datetime import datetime, timedelta
from functools import lru_cache

import pyotp
from anyio import to_thread
from regisse_kit import codes as kit_codes
from regisse_kit import passwords as kit_passwords
from regisse_kit import phone as kit_phone
from regisse_kit.crypto import FieldCipher, KeyedHasher, hash_password, needs_rehash, random_token, token_hash, verify_password
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import messages, tokens
from app.audit import audit
from app.config import get_settings
from app.errors import BadRequest, Conflict, Forbidden, NotFound, TooManyRequests, Unauthorized
from app.models import CodeChallenge, LegacyIdentity, Membership, RefreshToken, RelayRequest, User
from app.ratelimit import item, limiter
from app.tokens import utcnow

# A hash to check against when the phone is unknown, so response time does not reveal which numbers have accounts.
_DUMMY_HASH = hash_password("dummy-password-for-timing")
PRIVILEGED_ROLES = {"super_admin", "admin", "operator", "viewer", "pickup", "agent"}
CONSENT_VERSION = "2026-11"


@lru_cache
def _hasher(key: str) -> KeyedHasher:
    return KeyedHasher(key)


@lru_cache
def _cipher(key: str) -> FieldCipher:
    return FieldCipher(key)


def hasher() -> KeyedHasher:
    return _hasher(get_settings().hash_key)


def cipher() -> FieldCipher:
    return _cipher(get_settings().field_encryption_key)


def normalize_phone(raw: str) -> str:
    try:
        return kit_phone.normalize_ci_phone(raw, allow_test_numbers=get_settings().test_numbers_ok)
    except kit_phone.InvalidPhone:
        raise BadRequest("invalid_phone", "Numéro ivoirien à 10 chiffres attendu") from None


def check_password(password: str) -> None:
    try:
        kit_passwords.check_password_strength(password)
    except kit_passwords.WeakPassword as exc:
        raise BadRequest("weak_password", str(exc)) from None


_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,200}\.[^@\s.]{2,}$")


def normalize_email(raw: str) -> str:
    e = (raw or "").strip().lower()
    if not _EMAIL.match(e) or len(e) > 254:
        raise BadRequest("invalid_email")
    return e


async def user_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email_hash == hasher().hash(email, "email")))


async def user_by_phone(db: AsyncSession, e164: str) -> User | None:
    return await db.scalar(select(User).where(User.phone_hash == hasher().phone(e164)))


def user_phone(user: User) -> str | None:
    return cipher().decrypt(user.phone_enc) if user.phone_enc else None


def product_config(product: str) -> dict:
    cfg = get_settings().products.get(product)
    if cfg is None:
        raise BadRequest("unknown_product")
    return cfg


def is_privileged(user: User) -> bool:
    return any(m.role in PRIVILEGED_ROLES for m in user.memberships)


# --- registration and activation ----------------------------------------------------------------


async def register(db: AsyncSession, *, raw_phone: str, password: str, display_name: str | None, product: str, consent: bool, ip: str | None) -> User:
    cfg = product_config(product)
    reg = cfg.get("self_register")
    if not reg:
        raise Forbidden("registration_closed")
    if not consent:
        raise BadRequest("consent_required")
    s = get_settings()
    if not limiter().hit(item(f"{s.register_per_ip_hour}/hour"), f"register-ip:{ip}"):
        raise TooManyRequests("rate_limited")
    phone = normalize_phone(raw_phone)
    check_password(password)
    if await user_by_phone(db, phone) is not None:
        raise Conflict("phone_taken")
    pw_hash = await to_thread.run_sync(hash_password, password)  # argon2 is CPU-bound
    user = User(phone_enc=cipher().encrypt(phone), phone_hash=hasher().phone(phone), display_name=(display_name or None) and display_name.strip()[:80],
                password_hash=pw_hash, status="pending_activation", consent_at=utcnow(), consent_version=CONSENT_VERSION)
    user.memberships = [Membership(product=product, role=reg["role"])]
    db.add(user)
    await db.flush()
    db.add(RelayRequest(user_id=user.id, product=product, purpose="activation"))
    await audit(db, actor_type="user", actor_id=user.id, action="user.registered", entity="user", entity_id=user.id, ip=ip, detail={"product": product})
    return user


async def activate(db: AsyncSession, *, raw_phone: str, code: str) -> User:
    user = await user_by_phone(db, normalize_phone(raw_phone))
    if user is None or user.status == "rejected":
        raise BadRequest("wrong_activation_code")
    if user.status == "active":
        return user
    if user.status != "pending_activation":
        raise Forbidden("account_blocked")
    ch = await db.scalar(select(CodeChallenge).where(CodeChallenge.user_id == user.id, CodeChallenge.purpose == "activation"))
    now = utcnow()
    kit_ch = kit_codes.CodeChallenge(code_hash=ch.code_hash, expires_at=ch.expires_at, attempts=ch.attempts) if ch else None
    try:
        kit_codes.check(hasher(), kit_ch, str(user.id), "activation", code, now)
    except kit_codes.WrongCode:
        ch.attempts = kit_ch.attempts  # type: ignore[union-attr]
        await db.commit()  # the failed guess counts even though the request fails
        raise BadRequest("wrong_activation_code") from None
    except kit_codes.TooManyAttempts:
        raise TooManyRequests("activation_locked") from None
    except kit_codes.NoActiveCode:
        raise BadRequest("no_activation_code") from None
    user.status, user.phone_verified_at = "active", now
    await db.delete(ch)
    await db.execute(update(RelayRequest).where(RelayRequest.user_id == user.id, RelayRequest.purpose == "activation", RelayRequest.status.in_(["open", "sent"])).values(status="done"))
    await audit(db, actor_type="user", actor_id=user.id, action="user.activated", entity="user", entity_id=user.id)
    return user


# --- relay (admin sends a WhatsApp message via wa.me) --------------------------------------------


async def relay_send(db: AsyncSession, req: RelayRequest, admin_id: uuid.UUID, ip: str | None) -> str:
    """Issues what the user needs (activation code or temporary password) and returns the wa.me link. The secret exists
    only inside that link, returned once to the admin's device; only a hash is stored."""
    user = await db.get(User, req.user_id)
    if user is None or user.phone_enc is None:
        raise NotFound("user_not_found")
    if req.status not in ("open", "sent"):
        raise Conflict("request_closed")
    if is_privileged(user):
        raise Forbidden("relay_not_allowed_for_privileged")
    s = get_settings()
    now = utcnow()
    phone = user_phone(user)
    assert phone is not None
    if req.purpose == "activation":
        if user.status != "pending_activation":
            raise Conflict("already_active")
        kit_ch, code = kit_codes.issue(hasher(), str(user.id), "activation", now, timedelta(hours=s.activation_code_hours))
        await db.execute(delete(CodeChallenge).where(CodeChallenge.user_id == user.id, CodeChallenge.purpose == "activation"))
        db.add(CodeChallenge(user_id=user.id, purpose="activation", code_hash=kit_ch.code_hash, expires_at=kit_ch.expires_at))
        text = messages.activation(req.product, user.display_name, code)
        req.status = "sent"
    elif req.purpose == "password_reset":
        if user.status != "active":
            raise Conflict("not_active")
        temp = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))
        user.password_hash = await to_thread.run_sync(hash_password, temp)
        user.must_change_password, user.temp_password_expires_at = True, now + timedelta(hours=s.temp_password_hours)
        await claim_credentials(db, user.id)
        await revoke_all_sessions(db, user.id)
        text = messages.temp_password(req.product, user.display_name, temp)
        req.status = "done"
    else:
        raise BadRequest("unknown_purpose")
    req.handled_by, req.handled_at = admin_id, now
    await audit(db, actor_type="admin", actor_id=admin_id, action=f"relay.{req.purpose}.sent", entity="user", entity_id=user.id, ip=ip)
    return kit_phone.wa_link(phone, text)


async def relay_reject(db: AsyncSession, req: RelayRequest, admin_id: uuid.UUID, ip: str | None) -> None:
    """The number could not be verified (not on WhatsApp, wrong, no answer): release it so it can be re-registered."""
    user = await db.get(User, req.user_id)
    if user is None:
        raise NotFound("user_not_found")
    if req.purpose != "activation" or user.status != "pending_activation":
        raise Conflict("cannot_reject")
    user.phone_enc = user.phone_hash = None
    user.status = "rejected"
    req.status, req.handled_by, req.handled_at = "rejected", admin_id, utcnow()
    await audit(db, actor_type="admin", actor_id=admin_id, action="relay.activation.rejected", entity="user", entity_id=user.id, ip=ip)


async def request_password_reset(db: AsyncSession, *, raw_phone: str, product: str, ip: str | None) -> None:
    """Same answer for every number (no enumeration). Privileged accounts are never reset through the relay."""
    s = get_settings()
    phone = normalize_phone(raw_phone)
    ph = hasher().phone(phone)
    if not limiter().hit(item(s.reset_request_limit), f"reset-phone:{ph}") or not limiter().hit(item("30/hour"), f"reset-ip:{ip}"):
        raise TooManyRequests("reset_rate_limited")
    user = await user_by_phone(db, phone)
    if user is None or user.status != "active" or is_privileged(user):
        return
    open_req = await db.scalar(select(RelayRequest).where(RelayRequest.user_id == user.id, RelayRequest.purpose == "password_reset", RelayRequest.status == "open"))
    if open_req is None:
        db.add(RelayRequest(user_id=user.id, product=product, purpose="password_reset"))
        await audit(db, actor_type="user", actor_id=user.id, action="user.password_reset_requested", entity="user", entity_id=user.id, ip=ip)


# --- login and sessions ------------------------------------------------------------------------


def _roles_for(user: User, product: str) -> list[str]:
    return [m.role for m in user.memberships if m.product == product]


def pick_role(user: User, product: str, app: str) -> tuple[str, list[str]]:
    cfg = product_config(product)
    allowed = cfg["apps"].get(app)
    if allowed is None:
        raise BadRequest("unknown_app")
    held = _roles_for(user, product)
    role = next((r for r in allowed if r in held), None)
    if role is None:
        raise Forbidden("no_access_to_app")
    return role, held


async def login(db: AsyncSession, *, raw_phone: str | None = None, raw_email: str | None = None, password: str, product: str, app: str, totp: str | None, ip: str | None) -> dict:
    s = get_settings()
    cfg = product_config(product)
    if bool(raw_phone) == bool(raw_email):
        raise BadRequest("phone_or_email")
    if raw_email:
        email = normalize_email(raw_email)
        key_id, user = hasher().hash(email, "email"), None
        fail_key = f"login-fail:{key_id}"
        if not limiter().test(item(s.login_fail_limit), fail_key):
            raise TooManyRequests("login_locked")
        user = await user_by_email(db, email)
    else:
        phone = normalize_phone(raw_phone or "")
        fail_key = f"login-fail:{hasher().phone(phone)}"
        if not limiter().test(item(s.login_fail_limit), fail_key):
            raise TooManyRequests("login_locked")
        user = await user_by_phone(db, phone)
    ok = await to_thread.run_sync(verify_password, user.password_hash if user and user.password_hash else _DUMMY_HASH, password)
    if user is None or not user.password_hash or not ok:
        limiter().hit(item(s.login_fail_limit), fail_key)
        raise Unauthorized("invalid_credentials")
    if user.status == "pending_activation":
        raise Forbidden("not_activated")
    if user.status != "active":
        raise Forbidden("account_blocked")
    now = utcnow()
    if user.must_change_password and user.temp_password_expires_at and user.temp_password_expires_at < now:
        raise Unauthorized("temp_password_expired")  # said only once the password itself was right
    role, held = pick_role(user, product, app)
    amr = ["pwd"]
    if role in cfg.get("totp_roles", []) and not (user.totp_exempt and role not in ("super_admin", "admin")):
        if not user.totp_secret_enc:
            raise Forbidden("totp_not_set_up")
        code = (totp or "").strip()
        secret = cipher().decrypt(user.totp_secret_enc)
        step = int(now.timestamp()) // 30
        valid_step = next((st for st in (step - 1, step, step + 1) if pyotp.TOTP(secret).at(st * 30) == code), None) if code.isdigit() else None
        if valid_step is None or valid_step <= user.totp_last_step:  # a used code cannot be replayed
            limiter().hit(item(s.login_fail_limit), fail_key)
            raise Unauthorized("invalid_totp")
        user.totp_last_step = valid_step
        amr.append("totp")
    if await to_thread.run_sync(needs_rehash, user.password_hash):
        user.password_hash = await to_thread.run_sync(hash_password, password)
    user.last_login_at = now
    pair = await issue_pair(db, user, product, app, role, held, amr, now, new_family())
    await audit(db, actor_type="user", actor_id=user.id, action="user.login", entity="user", entity_id=user.id, ip=ip, detail={"product": product, "app": app, "role": role})
    return pair


def new_family() -> str:
    return uuid.uuid4().hex


async def issue_pair(db: AsyncSession, user: User, product: str, app: str, role: str, held: list[str], amr: list[str], auth_time: datetime, family: str) -> dict:
    s = get_settings()
    refresh = random_token(32)
    now = utcnow()
    db.add(RefreshToken(user_id=user.id, token_hash=token_hash(refresh), family=family, product=product, app=app, role=role,
                        amr=",".join(amr), auth_time=auth_time, expires_at=now + timedelta(days=s.refresh_days)))
    phone = user_phone(user)
    ttl = timedelta(minutes=s.access_token_minutes)
    ns = product if app == "customer" else f"{product}.{app}"  # legacy ids of staff live in their own namespace (flariane.admin)
    lid = await db.scalar(select(LegacyIdentity.legacy_id).where(LegacyIdentity.product == ns, LegacyIdentity.user_id == user.id))
    access = await tokens.issue_access_token(
        db, sub=user.id, phone_key=kit_phone.phone_key(phone) if phone else "", product=product, app=app, role=role, roles=held, amr=amr,
        auth_time=auth_time, sid=family, must_change_password=user.must_change_password, ttl=ttl, legacy_id=lid,
    )
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "expires_in": int(ttl.total_seconds()), "role": role,
            "must_change_password": user.must_change_password, "user_id": str(user.id)}


async def refresh(db: AsyncSession, refresh_token: str) -> dict:
    now = utcnow()
    stored = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(refresh_token)))
    if stored is None or stored.revoked_at is not None or stored.expires_at <= now:
        raise Unauthorized("session_expired")
    if stored.rotated_at is not None:
        # Already exchanged once: whoever holds it now copied it. End the whole session on every copy.
        await db.execute(update(RefreshToken).where(RefreshToken.family == stored.family, RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
        await db.commit()
        raise Unauthorized("session_expired")
    user = await db.get(User, stored.user_id)
    held = _roles_for(user, stored.product) if user else []
    if user is None or user.status != "active" or stored.role not in held:
        stored.revoked_at = now
        await db.commit()
        raise Unauthorized("session_expired")
    stored.rotated_at = now
    return await issue_pair(db, user, stored.product, stored.app, stored.role, held, stored.amr.split(","), stored.auth_time, stored.family)


async def logout(db: AsyncSession, refresh_token: str) -> None:
    stored = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(refresh_token)))
    if stored is not None:
        await db.execute(update(RefreshToken).where(RefreshToken.family == stored.family, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))


async def claim_credentials(db: AsyncSession, user_id: uuid.UUID) -> None:
    """auth now holds the newest credentials of this user: a later `import --refresh` must not overwrite them."""
    await db.execute(update(LegacyIdentity).where(LegacyIdentity.user_id == user_id).values(credentials_authority="auth"))


async def revoke_all_sessions(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))


async def change_password(db: AsyncSession, user_id: uuid.UUID, current: str, new: str, keep_sid: str | None) -> None:
    user = await db.get(User, user_id)
    if user is None or not user.password_hash or not await to_thread.run_sync(verify_password, user.password_hash, current):
        raise Unauthorized("invalid_credentials")
    check_password(new)
    user.password_hash = await to_thread.run_sync(hash_password, new)
    user.must_change_password, user.temp_password_expires_at = False, None
    await claim_credentials(db, user.id)
    # end every other session; the current one keeps working until its access token expires
    await db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None), RefreshToken.family != (keep_sid or "")).values(revoked_at=utcnow()))
    await audit(db, actor_type="user", actor_id=user.id, action="user.password_changed", entity="user", entity_id=user.id)


async def session_active(db: AsyncSession, sid: str) -> bool:
    row = await db.scalar(select(RefreshToken).where(RefreshToken.family == sid, RefreshToken.revoked_at.is_(None)).limit(1))
    return row is not None


# --- staff accounts (created in person by a super admin; never through the relay) -------------------


def _temp_password(n: int = 12) -> str:
    return "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(n))


async def create_staff(db: AsyncSession, *, raw_phone: str | None, raw_email: str | None = None, display_name: str, product: str, role: str, admin_id: uuid.UUID, ip: str | None) -> dict:
    """Creates an ACTIVE staff account with a one-time temporary password (and an authenticator secret for roles that need one).
    Both are returned once to the super admin, who hands them over in person: they never travel by WhatsApp.
    A staff account has a phone, an email, or both (the login identifier)."""
    cfg = product_config(product)
    known = {r for roles in cfg["apps"].values() for r in roles}
    if role not in known or role not in PRIVILEGED_ROLES:
        raise BadRequest("not_a_staff_role")
    if not raw_phone and not raw_email:
        raise BadRequest("phone_or_email")
    phone = normalize_phone(raw_phone) if raw_phone else None
    email = normalize_email(raw_email) if raw_email else None
    if phone and await user_by_phone(db, phone) is not None:
        raise Conflict("phone_taken")
    if email and await user_by_email(db, email) is not None:
        raise Conflict("email_taken")
    now = utcnow()
    temp = _temp_password()
    secret = pyotp.random_base32() if role in cfg.get("totp_roles", []) else None
    user = User(phone_enc=cipher().encrypt(phone) if phone else None, phone_hash=hasher().phone(phone) if phone else None,
                email_enc=cipher().encrypt(email) if email else None, email_hash=hasher().hash(email, "email") if email else None,
                display_name=display_name.strip()[:80],
                password_hash=await to_thread.run_sync(hash_password, temp), status="active", must_change_password=True,
                temp_password_expires_at=now + timedelta(hours=72), totp_secret_enc=cipher().encrypt(secret) if secret else None)
    user.memberships = [Membership(product=product, role=role, created_by=admin_id)]
    db.add(user)
    await db.flush()
    await audit(db, actor_type="admin", actor_id=admin_id, action="staff.created", entity="user", entity_id=user.id, ip=ip, detail={"product": product, "role": role})
    return _staff_secrets(user, phone or email or "", temp, secret)


def _staff_secrets(user: User, ident: str, temp: str, secret: str | None) -> dict:
    out: dict = {"user_id": str(user.id), "temporary_password": temp, "temporary_password_valid_hours": 72, "totp": None}
    if secret:
        out["totp"] = {"secret": secret, "uri": pyotp.TOTP(secret).provisioning_uri(name=ident, issuer_name="regisse auth")}
    return out


async def reset_staff(db: AsyncSession, user_id: uuid.UUID, *, reset_totp: bool, admin_id: uuid.UUID, ip: str | None) -> dict:
    user = await db.get(User, user_id)
    if user is None or (user.phone_enc is None and user.email_enc is None):
        raise NotFound("user_not_found")
    if admin_id == user.id:
        raise Conflict("cannot_reset_self")
    if not is_privileged(user):
        raise BadRequest("not_a_staff_account")
    now = utcnow()
    temp = _temp_password()
    user.password_hash = await to_thread.run_sync(hash_password, temp)
    user.must_change_password, user.temp_password_expires_at = True, now + timedelta(hours=72)
    secret = None
    if reset_totp:
        secret = pyotp.random_base32()
        user.totp_secret_enc, user.totp_last_step = cipher().encrypt(secret), 0
    await revoke_all_sessions(db, user.id)
    await audit(db, actor_type="admin", actor_id=admin_id, action="staff.reset", entity="user", entity_id=user.id, ip=ip, detail={"totp_reset": reset_totp})
    return _staff_secrets(user, user_phone(user) or (cipher().decrypt(user.email_enc) if user.email_enc else ""), temp, secret)


async def erase_self(db: AsyncSession, user_id: uuid.UUID, current_password: str, ip: str | None, *, check_only: bool = False) -> None:
    """Account deletion by the user. The product has already removed or anonymised its own data. Staff accounts cannot erase
    themselves (a super admin does that); the password is required so a stolen access token alone cannot delete an account."""
    user = await db.get(User, user_id)
    if user is None or user.status != "active" or not user.password_hash:
        raise Unauthorized("invalid_credentials")
    if is_privileged(user):
        raise Forbidden("staff_cannot_self_delete")
    if not await to_thread.run_sync(verify_password, user.password_hash, current_password):
        raise Unauthorized("invalid_credentials")
    if check_only:
        return
    user.phone_enc = user.phone_hash = user.display_name = user.password_hash = user.totp_secret_enc = None
    user.status = "disabled"
    await revoke_all_sessions(db, user.id)
    await audit(db, actor_type="user", actor_id=user.id, action="user.erased", entity="user", entity_id=user.id, ip=ip)
