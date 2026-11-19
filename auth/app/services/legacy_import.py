"""Import a product's existing users into `auth` (first use: flariane customers).

`import_rows` is pure logic over plain dict rows; the CLI reads the product's database and decrypts phones in memory
(the product's encryption key is passed at run time and never stored in auth). Rules:
  * idempotent: a legacy id already mapped is skipped; running twice changes nothing
  * a phone that already has an auth account is LINKED (membership + mapping added); its password is never overwritten
  * password hashes are imported as they are (argon2id verifies as-is; rehashed on first login if parameters differ)
  * blocked -> disabled, unverified -> pending_activation (+ open activation request), anonymised -> skipped
  * sessions/refresh tokens are NOT migrated: users sign in again
  * one failing row never stops the others (savepoint per row); stats never contain phones or secrets
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import BadRequest
from app.models import LegacyIdentity, Membership, RelayRequest, User
from app.services import accounts


async def import_rows(db: AsyncSession, rows: Iterable[dict[str, Any]], *, product: str, role: str = "customer", apply: bool) -> dict:
    """Row keys: id, phone (E.164 or None), display_name, password_hash, status ('active'|'pending'|'disabled'),
    must_change_password, temp_password_expires_at, phone_verified_at, consent_at, consent_version, created_at, reset_requested.
    Datetimes are naive UTC. With apply=False nothing is written (same counters, what WOULD happen)."""
    stats = {"total": 0, "created": 0, "linked_existing": 0, "skipped_already_imported": 0, "skipped_anonymised": 0, "errors": [], "activation_requests": 0, "reset_requests": 0}
    for row in rows:
        stats["total"] += 1
        legacy_id = str(row["id"])
        if not row.get("phone"):
            stats["skipped_anonymised"] += 1
            continue
        if await db.scalar(select(LegacyIdentity.id).where(LegacyIdentity.product == product, LegacyIdentity.legacy_id == legacy_id)):
            stats["skipped_already_imported"] += 1
            continue
        try:
            phone = accounts.normalize_phone(row["phone"])
        except BadRequest:
            stats["errors"].append({"legacy_id": legacy_id, "reason": "invalid_phone"})
            continue
        try:
            async with db.begin_nested():  # a bad row rolls back alone
                user = await accounts.user_by_phone(db, phone)
                if user is None:
                    status = {"active": "active", "pending": "pending_activation", "disabled": "disabled"}[row["status"]]
                    user = User(
                        phone_enc=accounts.cipher().encrypt(phone), phone_hash=accounts.hasher().phone(phone),
                        display_name=(row.get("display_name") or None) and row["display_name"].strip()[:80], password_hash=row.get("password_hash"),
                        status=status, must_change_password=bool(row.get("must_change_password")), temp_password_expires_at=row.get("temp_password_expires_at"),
                        phone_verified_at=row.get("phone_verified_at"), consent_at=row.get("consent_at"), consent_version=row.get("consent_version"),
                        created_at=row.get("created_at") or datetime.utcnow(),
                    )
                    user.memberships = [Membership(product=product, role=role)]
                    stats["created"] += 1
                    if apply:
                        db.add(user)
                        await db.flush()
                    if row["status"] == "pending":
                        stats["activation_requests"] += 1
                        if apply:
                            db.add(RelayRequest(user_id=user.id, product=product, purpose="activation"))
                    elif row["status"] == "active" and row.get("reset_requested"):
                        stats["reset_requests"] += 1
                        if apply:
                            db.add(RelayRequest(user_id=user.id, product=product, purpose="password_reset"))
                else:
                    # the person already has an auth account (maybe from another product): add access, keep her credentials
                    stats["linked_existing"] += 1
                    if apply and not any(m.product == product and m.role == role for m in user.memberships):
                        db.add(Membership(user_id=user.id, product=product, role=role))
                if apply:
                    db.add(LegacyIdentity(product=product, legacy_id=legacy_id, user_id=user.id))
                    await db.flush()
        except Exception as exc:  # noqa: BLE001: report the row, never its content
            stats["errors"].append({"legacy_id": legacy_id, "reason": type(exc).__name__})
    return stats
