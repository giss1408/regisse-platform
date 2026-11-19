import hmac
import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import tokens
from app.config import get_settings
from app.db import get_session
from app.errors import Forbidden, Unauthorized
from app.models import User


@dataclass
class Principal:
    user_id: uuid.UUID
    product: str
    role: str
    roles: list[str]
    sid: str
    amr: list[str]
    must_change_password: bool


async def current_principal(request: Request, db: AsyncSession = Depends(get_session)) -> Principal:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise Unauthorized("missing_token")
    claims = await tokens.decode_token(db, auth[7:], audience=list(get_settings().products))
    # An access token lives 10 minutes, but a user disabled meanwhile must not keep acting with it.
    user = await db.get(User, uuid.UUID(claims["sub"]))
    if user is None or user.status != "active":
        raise Unauthorized("invalid_token")
    return Principal(
        user_id=uuid.UUID(claims["sub"]), product=claims["aud"], role=claims["role"], roles=claims.get("roles", []),
        sid=claims["sid"], amr=claims.get("amr", []), must_change_password=bool(claims.get("mcp")),
    )


async def current_admin(p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_session)) -> Principal:
    """An auth back-office session: audience `auth`, role super_admin or admin, signed in with an authenticator code.
    The role is re-checked against the database on every call: a demoted admin loses power at once, not at token expiry."""
    if p.product != "auth" or p.role not in ("super_admin", "admin") or "totp" not in p.amr:
        raise Forbidden("admin_only")
    user = await db.get(User, p.user_id)
    if user is None or not any(m.product == "auth" and m.role == p.role for m in user.memberships):
        raise Forbidden("admin_only")
    return p


async def current_super_admin(p: Principal = Depends(current_admin)) -> Principal:
    if p.role != "super_admin":
        raise Forbidden("super_admin_only")
    return p



def service_caller(x_service_key: str | None = Header(default=None)) -> str:
    """The product a service key belongs to (constant-time comparison). A key only speaks for its own product."""
    if x_service_key:
        for product, key in get_settings().service_keys.items():
            if hmac.compare_digest(key.encode(), x_service_key.encode()):
                return product
    raise Forbidden("service_key_required")
