"""Vectors produced by flariane's own functions (app/security/crypto.py, integrations/whatsapp.py) with
test keys b"e"*32 (field encryption) and b"h"*32 (hash). The kit must read and reproduce them exactly,
so flariane can adopt the kit without touching stored data."""

import base64

from regisse_kit import crypto, phone, webhooks

ENC_KEY = base64.b64encode(b"e" * 32).decode()
HASH_KEY = base64.b64encode(b"h" * 32).decode()

FLARIANE_CIPHER = bytes.fromhex("5bcb86b6890960449967502bc9117345a9552224cfbc3f9ad5a3ec074ecefa4c03f6682c2acaca14625a")
FLARIANE_PHONEHASH = "6b8c8097593625d2bb44d44b8a0908c560353c517f4d70a6676f585f5e46e20e"
FLARIANE_KEYED = "88cd82d67404d02a0ed4b2a55205bdfeed790fe390bf4d527b10de63ee1b717b"
FLARIANE_ARGON2 = "$argon2id$v=19$m=65536,t=3,p=4$F/KYCvBPPV8rOHDu27ldjA$huKon30uJdOaHECkznd/Syl9jY8P5xdeoJSJnorpFeI"


def test_kit_decrypts_what_flariane_encrypted():
    assert crypto.FieldCipher(ENC_KEY).decrypt(FLARIANE_CIPHER) == "+2250701020345"


def test_kit_encrypt_roundtrip_and_format():
    c = crypto.FieldCipher(ENC_KEY)
    blob = c.encrypt("hello")
    assert len(blob) == 12 + len("hello") + 16  # nonce || ciphertext || tag
    assert c.decrypt(blob) == "hello"
    assert c.encrypt("hello") != blob  # fresh nonce each time


def test_keyed_hashes_match_flariane():
    h = crypto.KeyedHasher(HASH_KEY)
    assert h.phone("+2250701020345") == FLARIANE_PHONEHASH
    assert h.hash("123456", "pickup") == FLARIANE_KEYED


def test_token_hash_is_plain_sha256():
    assert crypto.token_hash("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_kit_verifies_flariane_argon2_hash():
    assert crypto.verify_password(FLARIANE_ARGON2, "Correct-Horse-9")
    assert not crypto.verify_password(FLARIANE_ARGON2, "wrong")
    assert not crypto.verify_password("not-a-hash", "x")


def test_mask_phone_matches_flariane():
    assert phone.mask_phone("+2250701020345") == "+225 07 •• •• 45"
    assert phone.mask_phone("+4915112345678") == "+491 •• 78"


def test_webhook_signature_matches_flariane():
    body = b'{"a":1}'
    sig = "sha256=195e6fab29525daa95957c5468b9eb981e884f6d3f96ebd2285ec51e80a05912"
    assert webhooks.verify_hmac_sha256(sig, body, "wa-secret")
    assert not webhooks.verify_hmac_sha256(sig, body + b" ", "wa-secret")
    assert not webhooks.verify_hmac_sha256(sig, body, "other")
    assert not webhooks.verify_hmac_sha256(None, body, "wa-secret")
    assert not webhooks.verify_hmac_sha256("sha256=zz", body, "wa-secret")
    assert not webhooks.verify_hmac_sha256(sig, body, "")
