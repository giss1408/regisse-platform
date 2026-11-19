"""Bootstrap commands (run inside the container).

    AUTH_ADMIN_PASSWORD=... python -m app.cli create-admin --phone 0701020304 --name "Régis" --role super_admin
    python -m app.cli rotate-key
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


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--phone", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--role", choices=["super_admin", "admin"], default="super_admin")
    sub.add_parser("rotate-key")
    sub.add_parser("gen-keys")
    args = p.parse_args()
    if args.cmd == "create-admin":
        asyncio.run(create_admin(args.phone, args.name, args.role))
    elif args.cmd == "rotate-key":
        asyncio.run(rotate())
    else:
        print("AUTH_FIELD_ENCRYPTION_KEY=" + base64.b64encode(os.urandom(32)).decode())
        print("AUTH_HASH_KEY=" + base64.b64encode(os.urandom(32)).decode())


if __name__ == "__main__":
    main()
