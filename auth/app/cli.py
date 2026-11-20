"""Bootstrap commands (run inside the container).

    AUTH_ADMIN_PASSWORD=... python -m app.cli create-admin --phone 0701020304 --name "Régis" --role super_admin
    python -m app.cli rotate-key
    FLARIANE_DATABASE_URL=... FLARIANE_FIELD_ENCRYPTION_KEY=... python -m app.cli import-flariane [--apply] [--refresh] [--cutover]
    FIDELIA_DATABASE_URL=... python -m app.cli import-fidelia [--apply] [--refresh] [--cutover]
    python -m app.cli gen-keys
"""

import argparse
import asyncio
import base64
import getpass
import os

import pyotp
from regisse_kit.crypto import hash_password
from regisse_kit.phone import phone_key

from app import tokens
from app.db import SessionLocal
from app.models import Membership, User
from app.services import accounts


async def create_admin(phone: str, name: str, role: str) -> None:
    password = os.environ.get("AUTH_ADMIN_PASSWORD") or getpass.getpass("Password (12+ characters): ")
    if len(password) < 12:
        raise SystemExit("password too short")
    e164 = accounts.normalize_phone(phone)
    secret = pyotp.random_base32()
    async with SessionLocal() as db:
        if await accounts.user_by_phone(db, e164):
            raise SystemExit("phone already exists")
        user = User(phone_enc=accounts.cipher().encrypt(e164), phone_hash=accounts.hasher().phone(e164), display_name=name,
                    password_hash=hash_password(password), status="active", totp_secret_enc=accounts.cipher().encrypt(secret),
                    phone_verified_at=tokens.utcnow())
        user.memberships = [Membership(product="auth", role=role)]
        db.add(user)
        await db.commit()
    print(f"{role} created. Add this to your authenticator app:")
    print(pyotp.TOTP(secret).provisioning_uri(name=phone_key(e164), issuer_name="regisse auth"))
    print(f"secret={secret}")


async def rotate() -> None:
    async with SessionLocal() as db:
        key = await tokens.rotate_key(db)
        await db.commit()
    print(f"new signing key {key.kid}; the previous key stays in the JWKS until its tokens expire")


def _naive_utc(dt):  # flariane stores timezone-aware timestamps; auth stores naive UTC
    from datetime import timezone

    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt is not None else None


async def import_flariane(apply: bool, refresh: bool = False, do_cutover: bool = False) -> None:
    """Dry-run by default. Reads flariane's `customers` table READ-ONLY with FLARIANE_DATABASE_URL and decrypts phones in memory
    with FLARIANE_FIELD_ENCRYPTION_KEY (neither is stored in auth)."""
    import json

    from regisse_kit.crypto import FieldCipher
    from regisse_kit.db_url import to_asyncpg_url
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.services.legacy_import import import_rows

    src_url, key = os.environ.get("FLARIANE_DATABASE_URL"), os.environ.get("FLARIANE_FIELD_ENCRYPTION_KEY")
    if not src_url or not key:
        raise SystemExit("set FLARIANE_DATABASE_URL and FLARIANE_FIELD_ENCRYPTION_KEY")
    flr_cipher = FieldCipher(key)
    engine = create_async_engine(to_asyncpg_url(src_url))
    rows: list[dict] = []
    async with engine.connect() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        result = await conn.execute(text(
            "select id, display_name, phone_enc, password_hash, must_change_password, temp_password_expires_at, phone_verified_at, consent_at, "
            "consent_version, blocked_at, anonymised_at, password_reset_requested_at, created_at from customers order by id"))
        for r in result.mappings():
            phone = None
            if r["phone_enc"] is not None and r["anonymised_at"] is None:
                try:
                    phone = flr_cipher.decrypt(r["phone_enc"])
                except Exception:  # wrong key or corrupt row: reported by the importer as an invalid phone, never printed
                    phone = "decrypt-failed"
            status = "disabled" if r["blocked_at"] else ("pending" if r["phone_verified_at"] is None else "active")
            rows.append({"id": r["id"], "phone": phone, "display_name": r["display_name"], "password_hash": r["password_hash"], "status": status,
                         "must_change_password": r["must_change_password"], "temp_password_expires_at": _naive_utc(r["temp_password_expires_at"]),
                         "phone_verified_at": _naive_utc(r["phone_verified_at"]), "consent_at": _naive_utc(r["consent_at"]),
                         "consent_version": r["consent_version"], "created_at": _naive_utc(r["created_at"]),
                         "reset_requested": r["password_reset_requested_at"] is not None})
    await engine.dispose()
    async with SessionLocal() as db:
        stats = await import_rows(db, rows, product="flariane", role="customer", apply=apply, refresh=refresh or do_cutover)
        if do_cutover:
            if not apply:
                stats["cutover"] = "would freeze credentials (add --apply)"
            elif stats["errors"]:
                raise SystemExit("refusing to cut over with errors; fix them and rerun:\n" + json.dumps(stats["errors"]))
            else:
                from app.services.legacy_import import cutover

                stats["cutover_frozen_accounts"] = await cutover(db, product="flariane")
        if apply:
            await db.commit()
    stats["mode"] = "APPLIED" if apply else "DRY-RUN (nothing written; add --apply)"
    print(json.dumps(stats, indent=2))


async def import_flariane_admins(apply: bool, refresh: bool = False, do_cutover: bool = False) -> None:
    """Back-office users of flariane (`admin_users`), read-only. The authenticator secret is decrypted with flariane's key in
    memory and re-encrypted with auth's key: the same authenticator app keeps working. Devices stay in flariane."""
    import json

    from regisse_kit.crypto import FieldCipher
    from regisse_kit.db_url import to_asyncpg_url
    from sqlalchemy import text, update
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.models import LegacyIdentity
    from app.services.legacy_import import import_admin_rows

    src_url, key = os.environ.get("FLARIANE_DATABASE_URL"), os.environ.get("FLARIANE_FIELD_ENCRYPTION_KEY")
    if not src_url or not key:
        raise SystemExit("set FLARIANE_DATABASE_URL and FLARIANE_FIELD_ENCRYPTION_KEY")
    flr = FieldCipher(key)
    engine = create_async_engine(to_asyncpg_url(src_url))
    rows: list[dict] = []
    async with engine.connect() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        for r in (await conn.execute(text("select id, email, name, password_hash, totp_secret_enc, totp_required, role, active from admin_users order by id"))).mappings():
            secret = None
            if r["totp_secret_enc"]:
                try:
                    secret = flr.decrypt(r["totp_secret_enc"])
                except Exception:  # wrong key: reported as no_totp_secret, never printed
                    secret = None
            rows.append({"id": r["id"], "email": r["email"], "name": r["name"], "password_hash": r["password_hash"], "totp_secret": secret,
                         "totp_required": r["totp_required"], "role": r["role"], "active": r["active"]})
    await engine.dispose()
    async with SessionLocal() as db:
        stats = await import_admin_rows(db, rows, product="flariane", apply=apply, refresh=refresh or do_cutover)
        if do_cutover:
            if not apply:
                stats["cutover"] = "would freeze credentials (add --apply)"
            elif stats["errors"]:
                raise SystemExit("refusing to cut over with errors:\n" + json.dumps(stats["errors"]))
            else:
                res = await db.execute(update(LegacyIdentity).where(LegacyIdentity.product == "flariane.admin", LegacyIdentity.credentials_authority != "auth").values(credentials_authority="auth"))
                stats["cutover_frozen_accounts"] = res.rowcount or 0
        if apply:
            await db.commit()
    stats["mode"] = "APPLIED" if apply else "DRY-RUN (nothing written; add --apply)"
    print(json.dumps(stats, indent=2))


async def import_fidelia(apply: bool, refresh: bool = False, do_cutover: bool = False) -> None:
    """Fidelia's `users` table, read-only (FIDELIA_DATABASE_URL). Phones are stored in clear there and password hashes are argon2id, so
    nothing needs decrypting and every password keeps working. A person with several roles gets one membership per role.
    Admins land WITHOUT an authenticator secret: a super admin gives each one a new password and a code with the staff reset
    (`POST /v1/admin/staff/{id}/reset` with reset_totp). Privileged accounts (agent, admin) are imported once and then managed in auth."""
    import json

    from regisse_kit.db_url import to_asyncpg_url
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.services.legacy_import import cutover, import_rows

    src_url = os.environ.get("FIDELIA_DATABASE_URL")
    if not src_url:
        raise SystemExit("set FIDELIA_DATABASE_URL")
    known = {"customer", "merchant", "cashier", "agent", "admin"}
    engine = create_async_engine(to_asyncpg_url(src_url))
    rows: list[dict] = []
    async with engine.connect() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        for r in (await conn.execute(text("select * from users order by id"))).mappings():
            roles = sorted({x for x in (r["roles"] or "").split(",") if x in known}) or ["customer"]
            pw, verified = r.get("password_hash"), r.get("phone_verified_at")
            # no password + not proven = an SMS-era account (the code proved the number): active, reset the password later;
            # a password but a number never proven: it waits for the activation code like a new sign-up
            staff = bool({"agent", "admin"} & set(roles))  # the relay never serves staff: they are activated in auth by a super admin
            status = "disabled" if r["disabled"] else ("pending" if (pw and verified is None and not staff) else "active")
            rows.append({"id": r["id"], "phone": r["phone_e164"], "display_name": None, "password_hash": pw, "status": status, "roles": roles,
                         "must_change_password": bool(r.get("must_change_password")), "temp_password_expires_at": None, "phone_verified_at": verified,
                         "consent_at": None, "consent_version": None, "created_at": r["created_at"], "reset_requested": False})
    await engine.dispose()
    async with SessionLocal() as db:
        stats = await import_rows(db, rows, product="fidelia", role="customer", apply=apply, refresh=refresh or do_cutover)
        stats["admins_needing_totp_setup"] = sum(1 for x in rows if "admin" in x["roles"] and x["status"] != "disabled")
        if do_cutover:
            if not apply:
                stats["cutover"] = "would freeze credentials (add --apply)"
            elif stats["errors"]:
                raise SystemExit("refusing to cut over with errors:\n" + json.dumps(stats["errors"]))
            else:
                stats["cutover_frozen_accounts"] = await cutover(db, product="fidelia")
        if apply:
            await db.commit()
    stats["mode"] = "APPLIED" if apply else "DRY-RUN (nothing written; add --apply)"
    print(json.dumps(stats, indent=2))


async def apply_phone_changes() -> None:
    """Cron (every 10 minutes): number changes whose security delay has passed take effect. Prints counts only."""
    import json

    from app.services import accounts

    async with SessionLocal() as db:
        out = await accounts.apply_due_phone_changes(db)
        await db.commit()
    print(json.dumps(out))


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--phone", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--role", choices=["super_admin", "admin"], default="super_admin")
    sub.add_parser("rotate-key")
    sub.add_parser("apply-phone-changes")
    fid = sub.add_parser("import-fidelia")
    fid.add_argument("--apply", action="store_true")
    fid.add_argument("--refresh", action="store_true")
    fid.add_argument("--cutover", action="store_true")
    adm = sub.add_parser("import-flariane-admins")
    adm.add_argument("--apply", action="store_true")
    adm.add_argument("--refresh", action="store_true")
    adm.add_argument("--cutover", action="store_true")
    imp = sub.add_parser("import-flariane")
    imp.add_argument("--apply", action="store_true")
    imp.add_argument("--refresh", action="store_true", help="also sync phone/password/name/blocking of already-migrated accounts (legacy authority only)")
    imp.add_argument("--cutover", action="store_true", help="final refresh, then freeze: auth becomes the credential authority (needs --apply)")
    sub.add_parser("gen-keys")
    args = p.parse_args()
    if args.cmd == "create-admin":
        asyncio.run(create_admin(args.phone, args.name, args.role))
    elif args.cmd == "rotate-key":
        asyncio.run(rotate())
    elif args.cmd == "apply-phone-changes":
        asyncio.run(apply_phone_changes())
    elif args.cmd == "import-fidelia":
        asyncio.run(import_fidelia(args.apply, args.refresh, args.cutover))
    elif args.cmd == "import-flariane-admins":
        asyncio.run(import_flariane_admins(args.apply, args.refresh, args.cutover))
    elif args.cmd == "import-flariane":
        asyncio.run(import_flariane(args.apply, args.refresh, args.cutover))
    else:
        print("AUTH_FIELD_ENCRYPTION_KEY=" + base64.b64encode(os.urandom(32)).decode())
        print("AUTH_HASH_KEY=" + base64.b64encode(os.urandom(32)).decode())


if __name__ == "__main__":
    main()
