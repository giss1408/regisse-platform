"""Ed25519 signing keys (private half encrypted at rest), access tokens, JWKS.

Consumers verify tokens locally with the JWKS and pin the algorithm (EdDSA): they never accept the token's own `alg`.
"""

import base64
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from app.keys import cipher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import Unauthorized
from app.models import SigningKey

ALG = "EdDSA"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


async def ensure_signing_key(db: AsyncSession) -> SigningKey:
    """The active key, created on first start. Rotation: `app.cli rotate-key` (old key stays in the JWKS until its tokens expire)."""
    key = await db.scalar(select(SigningKey).where(SigningKey.active.is_(True)).order_by(SigningKey.created_at.desc()))
    if key is not None:
        return key
    return await create_key(db)


async def create_key(db: AsyncSession) -> SigningKey:
    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    row = SigningKey(kid=uuid.uuid4().hex[:16], public_b64=_b64u(pub), private_enc=cipher().encrypt(pem.decode()))
    db.add(row)
    await db.flush()
    return row


async def rotate_key(db: AsyncSession) -> SigningKey:
    now = utcnow()
    for old in (await db.scalars(select(SigningKey).where(SigningKey.active.is_(True)))).all():
        old.active, old.retired_at = False, now
    return await create_key(db)


def _private(key: SigningKey) -> Ed25519PrivateKey:
    pem = cipher().decrypt(key.private_enc).encode()
    return serialization.load_pem_private_key(pem, password=None)  # type: ignore[return-value]


async def issue_access_token(db: AsyncSession, *, sub: uuid.UUID, phone_key: str, product: str, app: str, role: str, roles: list[str],
                             amr: list[str], auth_time: datetime, sid: str, must_change_password: bool, ttl: timedelta,
                             legacy_id: str | None = None) -> str:
    s = get_settings()
    key = await ensure_signing_key(db)
    now = utcnow()
    claims = {
        "iss": s.issuer, "aud": product, "sub": str(sub), "ph": phone_key,
        "role": role, "roles": roles, "app": app, "amr": amr,
        "auth_time": int(auth_time.replace(tzinfo=timezone.utc).timestamp()),
        "iat": int(now.replace(tzinfo=timezone.utc).timestamp()),
        "exp": int((now + ttl).replace(tzinfo=timezone.utc).timestamp()),
        "jti": uuid.uuid4().hex, "sid": sid,
    }
    if must_change_password:
        claims["mcp"] = True
    if legacy_id:
        claims["lid"] = legacy_id  # the user's id in THIS product's own table (migrated accounts), so the product need not match by phone
    return jwt.encode(claims, _private(key), algorithm=ALG, headers={"kid": key.kid})


async def jwks(db: AsyncSession) -> dict:
    s = get_settings()
    horizon = utcnow() - timedelta(minutes=s.access_token_minutes + 5)
    rows = (await db.scalars(select(SigningKey).where((SigningKey.active.is_(True)) | (SigningKey.retired_at > horizon)))).all()
    return {"keys": [{"kty": "OKP", "crv": "Ed25519", "alg": ALG, "use": "sig", "kid": k.kid, "x": k.public_b64} for k in rows]}


async def decode_token(db: AsyncSession, token: str, *, audience: str | list[str] | None = None) -> dict:
    """Verifies signature (EdDSA only, key by `kid`), issuer, expiry and, when given, audience."""
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        row = await db.get(SigningKey, kid) if kid else None
        if row is None or header.get("alg") != ALG:
            raise Unauthorized("invalid_token")
        public = Ed25519PublicKey.from_public_bytes(_unb64u(row.public_b64))
        return jwt.decode(
            token, public, algorithms=[ALG], issuer=get_settings().issuer, audience=audience,
            options={"require": ["exp", "iat", "sub", "iss", "aud"], "verify_aud": audience is not None},
        )
    except jwt.PyJWTError:
        raise Unauthorized("invalid_token") from None
