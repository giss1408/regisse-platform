import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ts(nullable: bool = True) -> Mapped[datetime | None]:
    return mapped_column(DateTime, nullable=nullable)


class User(Base):
    """One person, one global id (`id`, the SSO `sub`). The phone is encrypted; lookups use its keyed hash."""

    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    phone_enc: Mapped[bytes | None] = mapped_column(LargeBinary)  # None once rejected/anonymised
    phone_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(80))
    password_hash: Mapped[str | None] = mapped_column(String(200))
    # pending_activation | active | disabled | rejected
    status: Mapped[str] = mapped_column(String(24), default="pending_activation", index=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    temp_password_expires_at: Mapped[datetime | None] = _ts()
    phone_verified_at: Mapped[datetime | None] = _ts()
    consent_at: Mapped[datetime | None] = _ts()
    consent_version: Mapped[str | None] = mapped_column(String(20))
    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    totp_last_step: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_login_at: Mapped[datetime | None] = _ts()
    memberships: Mapped[list["Membership"]] = relationship(back_populates="user", lazy="selectin")


class Membership(Base):
    """Access to a product with a role. Roles are per product; products keep their own domain data."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "product", "role"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user: Mapped[User] = relationship(back_populates="memberships")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    family: Mapped[str] = mapped_column(String(32), index=True)  # = the `sid` claim
    product: Mapped[str] = mapped_column(String(32))
    app: Mapped[str] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(32))
    amr: Mapped[str] = mapped_column(String(32), default="pwd")
    auth_time: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    rotated_at: Mapped[datetime | None] = _ts()
    revoked_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CodeChallenge(Base):
    """One active one-time code per (user, purpose). Only a keyed hash is stored (regisse_kit.codes)."""

    __tablename__ = "code_challenges"
    __table_args__ = (UniqueConstraint("user_id", "purpose"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(24))  # activation
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class RelayRequest(Base):
    """Work for the admin relay queue: someone must send a WhatsApp message (wa.me) to this user."""

    __tablename__ = "relay_requests"
    __table_args__ = (Index("ix_relay_status_created", "status", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product: Mapped[str] = mapped_column(String(32))
    purpose: Mapped[str] = mapped_column(String(24))  # activation | password_reset
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | sent | rejected | done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    handled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    handled_at: Mapped[datetime | None] = _ts()


class SigningKey(Base):
    """Ed25519 signing key. Private half encrypted at rest. Retired keys stay in the JWKS until their tokens expire."""

    __tablename__ = "signing_keys"
    kid: Mapped[str] = mapped_column(String(32), primary_key=True)
    public_b64: Mapped[str] = mapped_column(String(64))  # raw 32-byte public key, base64url (JWK `x`)
    private_enc: Mapped[bytes] = mapped_column(LargeBinary)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    retired_at: Mapped[datetime | None] = _ts()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_type: Mapped[str] = mapped_column(String(16))  # user | admin | system | service
    action: Mapped[str] = mapped_column(String(48), index=True)
    entity: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON)
