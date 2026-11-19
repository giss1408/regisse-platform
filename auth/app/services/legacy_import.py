"""Import a product's existing users into `auth` (first use: flariane customers).

`import_rows` is pure logic over plain dict rows; the CLI reads the product's database and decrypts phones in memory
(the product's encryption key is passed at run time and never stored in auth). Rules:
  * idempotent: a legacy id already mapped is skipped; running twice changes nothing
  * a phone that already has an auth account is LINKED (membership + mapping added); its password is never overwritten
  * password hashes are imported as they are (argon2id verifies as-is; rehashed on first login if parameters differ)
  * blocked -> disabled, unverified -> pending_activation (+ open activation request), anonymised -> skipped
  * sessions/refresh tokens are NOT migrated: users sign in again
  * one failing row never stops the others (savepoint per row); stats never contain phones or secrets

Refresh (`refresh=True`) keeps already-migrated accounts in step with the product while the product is still the credential
authority: phone, password hash (+ forced change / temporary password), name, blocking and late verification are pulled from
the product row. It NEVER touches an account whose `credentials_authority` is "auth" (the user changed her password in auth,
or the cutover froze it), and it never re-enables a disabled account (an auth admin may have disabled it on purpose).
`cutover()` is the last step before the product's app logs in through auth: final refresh, then freeze.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import BadRequest
from app.models import LegacyIdentity, Membership, RelayRequest, User
from app.services import accounts


async def import_rows(db: AsyncSession, rows: Iterable[dict[str, Any]], *, product: str, role: str = "customer", apply: bool, refresh: bool = False) -> dict:
    """Row keys: id, phone (E.164 or None), display_name, password_hash, status ('active'|'pending'|'disabled'),
    must_change_password, temp_password_expires_at, phone_verified_at, consent_at, consent_version, created_at, reset_requested.
    Datetimes are naive UTC. With apply=False nothing is written (same counters, what WOULD happen)."""
    stats = {"total": 0, "created": 0, "linked_existing": 0, "skipped_already_imported": 0, "skipped_anonymised": 0, "errors": [], "activation_requests": 0, "reset_requests": 0,
             "refresh": {"phone": 0, "password": 0, "name": 0, "disabled": 0, "activated": 0, "erased": 0, "frozen_skipped": 0, "unchanged": 0}}
    for row in rows:
        stats["total"] += 1
        legacy_id = str(row["id"])
        mapping = await db.scalar(select(LegacyIdentity).where(LegacyIdentity.product == product, LegacyIdentity.legacy_id == legacy_id))
        if not row.get("phone"):
            stats["skipped_anonymised"] += 1
            if refresh and mapping is not None:
                try:
                    async with db.begin_nested():
                        await _erase(db, mapping, stats["refresh"], apply)
                except Exception as exc:  # noqa: BLE001
                    stats["errors"].append({"legacy_id": legacy_id, "reason": type(exc).__name__})
            continue
        if mapping is not None:
            stats["skipped_already_imported"] += 1
            if refresh:
                try:
                    async with db.begin_nested():
                        await _refresh_one(db, mapping, row, stats["refresh"], apply)
                except Exception as exc:  # noqa: BLE001
                    stats["errors"].append({"legacy_id": legacy_id, "reason": type(exc).__name__})
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


async def _refresh_one(db: AsyncSession, mapping: LegacyIdentity, row: dict[str, Any], r: dict, apply: bool) -> None:
    if mapping.credentials_authority == "auth":
        r["frozen_skipped"] += 1
        return
    user = await db.get(User, mapping.user_id)
    if user is None or accounts.is_privileged(user):
        r["frozen_skipped"] += 1
        return
    changed = False
    phone = accounts.normalize_phone(row["phone"])
    if phone != accounts.user_phone(user):
        owner = await accounts.user_by_phone(db, phone)
        if owner is not None and owner.id != user.id:
            raise ValueError("phone_conflict")  # another auth account already uses her new number: needs a human
        r["phone"] += 1
        changed = True
        if apply:
            user.phone_enc, user.phone_hash = accounts.cipher().encrypt(phone), accounts.hasher().phone(phone)
    if row.get("password_hash") != user.password_hash:
        r["password"] += 1
        changed = True
        if apply:
            user.password_hash = row.get("password_hash")
            await accounts.revoke_all_sessions(db, user.id)  # the old password no longer opens anything
    if apply:
        user.must_change_password, user.temp_password_expires_at = bool(row.get("must_change_password")), row.get("temp_password_expires_at")
    name = (row.get("display_name") or None) and row["display_name"].strip()[:80]
    if name != user.display_name:
        r["name"] += 1
        changed = True
        if apply:
            user.display_name = name
    if row["status"] == "disabled" and user.status != "disabled":
        r["disabled"] += 1
        changed = True
        if apply:
            user.status = "disabled"
            await accounts.revoke_all_sessions(db, user.id)
    elif row["status"] == "active" and user.status == "pending_activation":
        r["activated"] += 1
        changed = True
        if apply:
            user.status, user.phone_verified_at = "active", row.get("phone_verified_at")
            await db.execute(update(RelayRequest).where(RelayRequest.user_id == user.id, RelayRequest.purpose == "activation", RelayRequest.status.in_(["open", "sent"])).values(status="done"))
    if not changed:
        r["unchanged"] += 1
    if apply:
        await db.flush()


async def cutover(db: AsyncSession, *, product: str) -> int:
    """Freeze: from now on auth is the credential authority for every migrated account of this product. Run `refresh` first."""
    res = await db.execute(update(LegacyIdentity).where(LegacyIdentity.product == product, LegacyIdentity.credentials_authority != "auth").values(credentials_authority="auth"))
    return res.rowcount or 0


async def _erase(db: AsyncSession, mapping: LegacyIdentity, r: dict, apply: bool) -> None:
    """The product anonymised this person (account deletion): erase her identity in auth too, and end her sessions."""
    if mapping.credentials_authority == "auth":
        r["frozen_skipped"] += 1  # after cutover auth owns the account: its own deletion flow applies
        return
    user = await db.get(User, mapping.user_id)
    if user is None or user.phone_enc is None or accounts.is_privileged(user):
        return
    r["erased"] += 1
    if apply:
        user.phone_enc = user.phone_hash = user.display_name = user.password_hash = user.totp_secret_enc = None
        user.status = "disabled"
        await accounts.revoke_all_sessions(db, user.id)
        await db.flush()
