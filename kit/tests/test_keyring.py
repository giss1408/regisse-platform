import base64
import os
from datetime import datetime, timedelta

import pytest
from cryptography.exceptions import InvalidTag

from regisse_kit import codes
from regisse_kit.crypto import FieldCipher, KeyedHasher
from regisse_kit.keyring import UnknownKey, VersionedCipher, VersionedHasher


def key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


K0, K1, K2 = key(), key(), key()


def test_envelope_roundtrip_and_kid_is_visible():
    c = VersionedCipher({"0": K0, "1": K1}, current="1")
    blob = c.encrypt("+2250701020304")
    assert c.kid_of(blob) == "1" and c.decrypt(blob) == "+2250701020304"
    assert b"+2250701020304" not in blob


def test_legacy_unversioned_blobs_stay_readable_with_the_legacy_key():
    legacy = FieldCipher(K0).encrypt("secret")  # what is in the database today
    c = VersionedCipher({"0": K0, "1": K1}, current="1", legacy_kid="0")
    assert c.kid_of(legacy) is None and c.decrypt(legacy) == "secret"


def test_old_key_blobs_stay_readable_after_the_current_key_changes():
    old = VersionedCipher({"0": K0, "1": K1}, current="1").encrypt("x")
    new = VersionedCipher({"0": K0, "1": K1, "2": K2}, current="2")
    assert new.decrypt(old) == "x" and new.kid_of(new.reencrypt(old)) == "2"


def test_a_retired_key_is_refused_not_guessed():
    blob = VersionedCipher({"0": K0, "1": K1}, current="1").encrypt("x")
    with pytest.raises((UnknownKey, InvalidTag)):
        VersionedCipher({"0": K0, "2": K2}, current="2").decrypt(blob)


def test_header_is_authenticated_so_the_kid_cannot_be_swapped():
    c = VersionedCipher({"0": K0, "1": K1}, current="1")
    blob = bytearray(c.encrypt("x"))
    blob[4] = ord("0")  # 'kid 1' -> 'kid 0'
    with pytest.raises((UnknownKey, InvalidTag)):
        c.decrypt(bytes(blob))


def test_current_must_be_in_the_ring():
    with pytest.raises(ValueError):
        VersionedCipher({"0": K0}, current="9")


def test_hasher_current_first_and_legacy_value_unchanged():
    h = VersionedHasher({"0": K0, "1": K1}, current="1")
    assert h.phone("+225") == h.phone_all("+225")[0] != KeyedHasher(K0).phone("+225")
    assert h.phone_all("+225")[1] == KeyedHasher(K0).phone("+225")  # the stored legacy hash is still found
    assert h.hash("a@b.c", "email") in h.hash_all("a@b.c", "email")


def test_codes_issued_before_a_hash_key_rotation_still_verify():
    now = datetime(2026, 1, 1)
    before = VersionedHasher({"0": K0}, current="0")
    ch, code = codes.issue(before, "u1", "activation", now, timedelta(hours=1))
    after = VersionedHasher({"0": K0, "1": K1}, current="1")
    codes.check(after, ch, "u1", "activation", code, now)  # no raise
    with pytest.raises(codes.WrongCode):
        codes.check(after, ch, "u1", "activation", "000000" if code != "000000" else "111111", now)
