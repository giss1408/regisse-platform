import base64
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from regisse_kit import codes, crypto, db_url, passwords, phone

KEY = base64.b64encode(b"k" * 32).decode()
NOW = datetime(2026, 11, 19, 12, 0, 0)


# --- keys --------------------------------------------------------------------
def test_bad_key_length_rejected():
    with pytest.raises(ValueError):
        crypto.FieldCipher(base64.b64encode(b"short").decode())
    with pytest.raises(ValueError):
        crypto.KeyedHasher(base64.b64encode(b"short").decode())


def test_hash_purpose_separates_domains():
    h = crypto.KeyedHasher(KEY)
    assert h.hash("1234", "otp") != h.hash("1234", "pickup")


def test_random_helpers():
    assert len(crypto.random_digits(6)) == 6 and crypto.random_digits(6).isdigit()
    assert crypto.random_token() != crypto.random_token()
    assert crypto.constant_time_equals("a", "a") and not crypto.constant_time_equals("a", "b")


def test_password_roundtrip_and_rehash_flag():
    h = crypto.hash_password("Correct-Horse-9")
    assert crypto.verify_password(h, "Correct-Horse-9") and not crypto.verify_password(h, "nope")
    assert crypto.needs_rehash(h) is False


# --- phone ---------------------------------------------------------------------
@pytest.mark.parametrize("raw", ["0701020345", "07 01 02 03 45", "+225 0701020345", "00225 07 01 02 03 45", "2250701020345", "07.01.02.03.45"])
def test_ci_numbers_normalise(raw):
    assert phone.normalize_ci_phone(raw) == "+2250701020345"


@pytest.mark.parametrize("raw", ["", "0901020345", "070102034", "abc", "+33612345678", "+4915112345678"])
def test_invalid_numbers_rejected(raw):
    with pytest.raises(phone.InvalidPhone):
        phone.normalize_ci_phone(raw)


def test_german_test_numbers_only_when_allowed():
    assert phone.normalize_ci_phone("+49 151 12345678", allow_test_numbers=True) == "+4915112345678"
    with pytest.raises(phone.InvalidPhone):
        phone.normalize_ci_phone("+49 151 12345678")


def test_phone_key_and_wa_link():
    assert phone.phone_key("+2250701020345") == "tel:+2250701020345"
    url = phone.wa_link("+2250701020345", "Bonjour & merci: *123456*")
    parts = urlsplit(url)
    assert parts.netloc == "wa.me" and parts.path == "/2250701020345"
    assert parse_qs(parts.query)["text"] == ["Bonjour & merci: *123456*"]


# --- passwords -------------------------------------------------------------------
@pytest.mark.parametrize("pw", ["short", "1111111", "11112222"[:7]])
def test_weak_passwords(pw):
    with pytest.raises(passwords.WeakPassword):
        passwords.check_password_strength(pw)


def test_acceptable_passwords():
    passwords.check_password_strength("Abcdefg1")
    passwords.check_password_strength("12345678")  # more than two distinct digits is accepted, as in flariane


def test_all_same_digit_password_rejected():
    with pytest.raises(passwords.WeakPassword):
        passwords.check_password_strength("11111111")


# --- db url ----------------------------------------------------------------------
def test_asyncpg_url_conversion():
    assert db_url.to_asyncpg_url("postgres://u:p@h:5432/d?sslmode=require&channel_binding=require") == "postgresql+asyncpg://u:p@h:5432/d?ssl=require"
    assert db_url.to_asyncpg_url("postgresql://u:p@postgres:5432/db_x") == "postgresql+asyncpg://u:p@postgres:5432/db_x"
    assert db_url.to_asyncpg_url("postgresql+asyncpg://u:p@h/d") == "postgresql+asyncpg://u:p@h/d"
    assert db_url.to_asyncpg_url("sqlite:///x.db") == "sqlite:///x.db"


# --- one-time codes ----------------------------------------------------------------
H = crypto.KeyedHasher(KEY)
TTL = timedelta(hours=48)


def test_code_roundtrip():
    ch, code = codes.issue(H, "customer:1", "phone-verify", NOW, TTL)
    assert len(code) == 6 and code.isdigit() and code not in ch.code_hash
    codes.check(H, ch, "customer:1", "phone-verify", code, NOW + timedelta(hours=1))


def test_wrong_code_counts_attempts_then_locks():
    ch, code = codes.issue(H, "customer:1", "phone-verify", NOW, TTL)
    bad = "000000" if code != "000000" else "111111"
    for i in range(5):
        with pytest.raises(codes.WrongCode):
            codes.check(H, ch, "customer:1", "phone-verify", bad, NOW)
        assert ch.attempts == i + 1
    with pytest.raises(codes.TooManyAttempts):
        codes.check(H, ch, "customer:1", "phone-verify", code, NOW)  # even the right code, once locked


def test_expired_or_missing_code():
    ch, code = codes.issue(H, "customer:1", "phone-verify", NOW, TTL)
    with pytest.raises(codes.NoActiveCode):
        codes.check(H, ch, "customer:1", "phone-verify", code, NOW + TTL)
    with pytest.raises(codes.NoActiveCode):
        codes.check(H, None, "customer:1", "phone-verify", code, NOW)


def test_code_bound_to_subject_and_purpose():
    ch, code = codes.issue(H, "customer:1", "phone-verify", NOW, TTL)
    with pytest.raises(codes.WrongCode):
        codes.check(H, ch, "customer:2", "phone-verify", code, NOW)
    ch.attempts = 0
    with pytest.raises(codes.WrongCode):
        codes.check(H, ch, "customer:1", "wallet-change", code, NOW)
