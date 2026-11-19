import uuid
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import tokens
from app.config import get_settings
from app.db import get_session
from app.errors import Forbidden, Unauthorized


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
    return Principal(
        user_id=uuid.UUID(claims["sub"]), product=claims["aud"], role=claims["role"], roles=claims.get("roles", []),
        sid=claims["sid"], amr=claims.get("amr", []), must_change_password=bool(claims.get("mcp")),
    )


async def current_admin(p: Principal = Depends(current_principal)) -> Principal:
    """An auth back-office session: audience `auth`, role super_admin or admin, signed in with an authenticator code."""
    if p.product != "auth" or p.role not in ("super_admin", "admin") or "totp" not in p.amr:
        raise Forbidden("admin_only")
    return p


async def current_super_admin(p: Principal = Depends(current_admin)) -> Principal:
    if p.role != "super_admin":
        raise Forbidden("super_admin_only")
    return p
