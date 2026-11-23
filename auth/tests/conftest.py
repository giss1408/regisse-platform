import base64
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlsplit

os.environ.update(
    {
        "AUTH_ENV": "test",
        "AUTH_ISSUER": "http://auth.test",
        "AUTH_DATABASE_URL": "postgresql+asyncpg://auth:auth@localhost:5434/auth_test",
        "AUTH_FIELD_ENCRYPTION_KEY": base64.b64encode(b"e" * 32).decode(),
        "AUTH_HASH_KEY": base64.b64encode(b"h" * 32).decode(),
        "AUTH_RATE_LIMIT_STORAGE": "memory://",
        "AUTH_METRICS_TOKEN": "metrics-test-token",
        "AUTH_SERVICE_KEYS_JSON": '{"flariane": "svc-flariane-key"}',
    }
)

import asyncpg  # noqa: E402
import pyotp  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from regisse_kit.crypto import hash_password  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import ratelimit, tokens  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Membership, User  # noqa: E402
from app.services import accounts  # noqa: E402

PASSWORD = "Correct-Horse-9"


@pytest.fixture(scope="session", autouse=True)
def _create_test_db() -> None:
    import asyncio

    async def setup() -> None:
        conn = await asyncpg.connect("postgresql://auth:auth@localhost:5434/auth")
        await conn.execute("DROP DATABASE IF EXISTS auth_test WITH (FORCE)")
        await conn.execute("CREATE DATABASE auth_test")
        await conn.close()
        async with engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)

    asyncio.run(setup())


@pytest.fixture(autouse=True)
async def _clean() -> AsyncIterator[None]:
    ratelimit.reset()
    async with engine.begin() as c:
        await c.execute(text("TRUNCATE users, memberships, refresh_tokens, code_challenges, relay_requests, signing_keys, audit_log RESTART IDENTITY CASCADE"))
    await engine.dispose()
    yield
    await engine.dispose()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def make_user(phone: str, *, role: str | None = None, product: str = "flariane", status: str = "active", name: str = "Test", totp: str | None = None) -> User:
    async with SessionLocal() as db:
        e164 = accounts.normalize_phone(phone)
        u = User(phone_enc=accounts.cipher().encrypt(e164), phone_hash=accounts.hasher().phone(e164), display_name=name, password_hash=hash_password(PASSWORD),
                 status=status, totp_secret_enc=accounts.cipher().encrypt(totp) if totp else None, phone_verified_at=tokens.utcnow())
        if role:
            u.memberships = [Membership(product=product, role=role)]
        db.add(u)
        await db.commit()
        return u


async def admin_headers(role: str = "admin", phone: str = "0700000001") -> dict:
    """A back-office session as a signed-in admin would hold it (password + authenticator code), without going through
    the TOTP login (whose replay protection allows one code per 30 s step)."""
    u = await make_user(phone, role=role, product="auth", totp=pyotp.random_base32(), name="Admin")
    async with SessionLocal() as db:
        tok = await tokens.issue_access_token(db, sub=u.id, phone_key="tel:" + accounts.normalize_phone(phone), product="auth", app="admin", role=role,
                                              roles=[role], amr=["pwd", "totp"], auth_time=tokens.utcnow(), sid=uuid.uuid4().hex,
                                              must_change_password=False, ttl=__import__("datetime").timedelta(minutes=10))
        await db.commit()
    return {"Authorization": f"Bearer {tok}"}


def code_from_url(url: str) -> str:
    """The 6-digit code inside the wa.me message (what the admin sends)."""
    text_ = parse_qs(urlsplit(url).query)["text"][0]
    return re.search(r"\*(\d{6})\*", text_).group(1)  # type: ignore[union-attr]


def temp_from_url(url: str) -> str:
    text_ = parse_qs(urlsplit(url).query)["text"][0]
    return re.search(r"\*([a-z0-9]{10})\*", text_).group(1)  # type: ignore[union-attr]


async def register_and_activate(client: AsyncClient, admin: dict, phone: str = "0701020304", product: str = "flariane") -> str:
    """Full happy path to an active customer; returns the phone."""
    r = await client.post("/v1/register", json={"phone": phone, "password": PASSWORD, "display_name": "Fatou Test", "product": product, "consent": True})
    assert r.status_code == 201, r.text
    queue = (await client.get("/v1/admin/relay-queue", headers=admin)).json()
    item = next(i for i in queue if i["purpose"] == "activation")
    sent = await client.post(f"/v1/admin/relay/{item['id']}/send", headers=admin)
    assert sent.status_code == 200, sent.text
    r = await client.post("/v1/activate", json={"phone": phone, "code": code_from_url(sent.json()["url"])})
    assert r.status_code == 200, r.text
    return phone
