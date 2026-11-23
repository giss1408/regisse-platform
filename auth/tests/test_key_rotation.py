"""Key rotation: add a key, make it current, rotate the data online, retire the old one (docs/KEY_ROTATION.md)."""

import base64
import os

import pyotp
import pytest
from sqlalchemy import select

from app import cli, tokens
from app.config import get_settings
from app.db import SessionLocal
from app.keys import cipher, hasher
from app.models import SigningKey, User
from tests.conftest import PASSWORD, make_user

LOGIN = {"password": PASSWORD, "product": "flariane", "app": "customer"}


def new_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def rotate_to_key_1(monkeypatch):
    """Switches the running settings to a ring with a new current key (what a deploy with new env vars does)."""
    def go():
        monkeypatch.setenv("AUTH_FIELD_KEYS_JSON", '{"1": "%s"}' % new_key())
        monkeypatch.setenv("AUTH_FIELD_KEY_CURRENT", "1")
        monkeypatch.setenv("AUTH_HASH_KEYS_JSON", '{"1": "%s"}' % new_key())
        monkeypatch.setenv("AUTH_HASH_KEY_CURRENT", "1")
        get_settings.cache_clear()
    yield go
    monkeypatch.undo()
    get_settings.cache_clear()


async def test_old_data_stays_usable_the_moment_a_new_key_is_current(client, rotate_to_key_1):
    await make_user("0701020304", role="customer")
    rotate_to_key_1()
    r = await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})
    assert r.status_code == 200, r.text  # the stored hash is under key 0, the lookup tries the whole ring


async def test_rotate_data_keys_moves_everything_and_verify_gates_retirement(client, rotate_to_key_1, capsys):
    await make_user("0701020304", role="customer")
    await make_user("0700000009", role="super_admin", product="auth", totp=pyotp.random_base32())
    await client.get("/healthz")
    rotate_to_key_1()
    assert await cli.rotate_data_keys(apply=False, verify=True) == 1  # dry run: things are left, nothing changed
    async with SessionLocal() as db:
        assert all(cipher().kid_of(u.phone_enc) == "0" for u in (await db.scalars(select(User))).all())
    assert await cli.rotate_data_keys(apply=True, verify=False) == 0
    assert await cli.rotate_data_keys(apply=False, verify=True) == 0  # nothing left: the old key may be retired
    async with SessionLocal() as db:
        for u in (await db.scalars(select(User))).all():
            assert cipher().kid_of(u.phone_enc) == "1"
            assert u.phone_hash == hasher().phone(cipher().decrypt(u.phone_enc))
            if u.totp_secret_enc:
                assert cipher().kid_of(u.totp_secret_enc) == "1"
        assert all(cipher().is_current(k.private_enc) for k in (await db.scalars(select(SigningKey))).all())
    out = capsys.readouterr().out
    assert "0701020304" not in out and "701020304" not in out  # counts only
    r = await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})
    assert r.status_code == 200, r.text
    async with SessionLocal() as db:
        assert (await tokens.jwks(db))["keys"]  # signing keys still readable


async def test_signing_key_is_readable_after_rotation(client, rotate_to_key_1):
    await make_user("0701020304", role="customer")
    await client.get("/healthz")
    rotate_to_key_1()
    await cli.rotate_data_keys(apply=True, verify=False)
    r = await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})
    assert r.status_code == 200 and r.json()["access_token"]


async def test_retiring_the_old_key_after_rotation_keeps_working(client, rotate_to_key_1, monkeypatch):
    await make_user("0701020304", role="customer")
    await client.get("/healthz")
    rotate_to_key_1()
    await cli.rotate_data_keys(apply=True, verify=False)
    # retire: key "0" is replaced by a fresh tombstone nothing is encrypted under
    monkeypatch.setenv("AUTH_FIELD_ENCRYPTION_KEY", new_key())
    monkeypatch.setenv("AUTH_HASH_KEY", new_key())
    get_settings.cache_clear()
    r = await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})
    assert r.status_code == 200, r.text
