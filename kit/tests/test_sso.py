import base64
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regisse_kit.sso import Principal, SsoError, SsoVerifier

ISS, AUD = "https://auth.example", "flariane"


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Keys:
    def __init__(self):
        self.priv = Ed25519PrivateKey.generate()
        self.kid = "k1"

    def jwk(self):
        pub = self.priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return {"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "kid": self.kid, "x": b64u(pub)}

    def token(self, **over):
        now = int(time.time())
        claims = {"iss": ISS, "aud": AUD, "sub": "u-1", "ph": "tel:+2250701020304", "role": "customer", "roles": ["customer"], "app": "customer",
                  "amr": ["pwd"], "auth_time": now, "iat": now, "exp": now + 600, "sid": "s1", **over}
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.priv, algorithm="EdDSA", headers={"kid": self.kid})


def make(keys, **kw):
    calls = []

    def fetch():
        calls.append(1)
        return {"keys": [keys.jwk()]}

    return SsoVerifier(issuer=ISS, audience=AUD, jwks_fetch=fetch, **kw), calls


def test_valid_token_gives_a_principal():
    k = Keys(); v, _ = make(k)
    p = v.verify(k.token())
    assert (p.sub, p.phone_key, p.role, p.app, p.amr, p.sid) == ("u-1", "tel:+2250701020304", "customer", "customer", ["pwd"], "s1")


@pytest.mark.parametrize("over", [{"aud": "fidelia"}, {"iss": "https://evil"}, {"exp": 1, "iat": 0}, {"role": None}, {"sid": None}, {"sub": None}])
def test_bad_claims_are_rejected(over):
    k = Keys(); v, _ = make(k)
    with pytest.raises(SsoError) as e:
        v.verify(k.token(**over))
    assert e.value.status == 401


def test_algorithm_is_pinned_never_taken_from_the_token():
    k = Keys(); v, _ = make(k)
    payload = jwt.decode(k.token(), options={"verify_signature": False})
    for forged in (jwt.encode(payload, "x" * 40, algorithm="HS256", headers={"kid": "k1"}), jwt.encode(payload, None, algorithm="none", headers={"kid": "k1"})):
        with pytest.raises(SsoError):
            v.verify(forged)
    other = Ed25519PrivateKey.generate()  # signed by someone else, same kid
    with pytest.raises(SsoError):
        v.verify(jwt.encode(payload, other, algorithm="EdDSA", headers={"kid": "k1"}))
    with pytest.raises(SsoError):
        v.verify("garbage")


def test_jwks_is_cached_and_refetched_on_unknown_kid_with_a_floor():
    t = [1000.0]
    k = Keys(); v, calls = make(k, clock=lambda: t[0])
    v.verify(k.token()); v.verify(k.token())
    assert len(calls) == 1  # cached
    k2 = Keys(); k2.kid = "k2"  # rotation: new key id the cache has not seen
    keys = {"keys": [k.jwk(), k2.jwk()]}
    v._fetch = lambda: (calls.append(1), keys)[1]
    t[0] += 31
    assert v.verify(k2.token()).sub == "u-1" and len(calls) == 2
    t[0] += 1
    k3 = Keys(); k3.kid = "k3"
    with pytest.raises(SsoError):
        v.verify(k3.token())  # unknown kid: refetch only every 30 s, so a flood of bad kids cannot hammer auth
    assert len(calls) == 2


def test_role_and_step_up_guards():
    k = Keys(); v, _ = make(k)
    now = int(time.time())
    p = v.verify(k.token(role="merchant", roles=["merchant"], amr=["pwd"], auth_time=now))
    p.require_role("merchant", "cashier")
    with pytest.raises(SsoError) as e:
        p.require_role("admin")
    assert e.value.status == 403
    with pytest.raises(SsoError) as e:
        p.require_step_up(300, amr="totp")  # logged in with a password only
    assert e.value.code == "step_up_required"
    strong = v.verify(k.token(amr=["pwd", "totp"], auth_time=now - 100)).require_step_up(300)
    assert isinstance(strong, Principal)
    with pytest.raises(SsoError):
        v.verify(k.token(amr=["pwd", "totp"], auth_time=now - 1000)).require_step_up(300)  # proved too long ago


def test_pending_password_change_can_be_forbidden():
    k = Keys(); v, _ = make(k)
    with pytest.raises(SsoError):
        v.verify(k.token(mcp=True)).forbid_pending_password_change()
    v.verify(k.token()).forbid_pending_password_change()
