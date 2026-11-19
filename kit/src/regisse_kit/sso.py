"""Verify tokens issued by the platform `auth` service (SSO). Local verification with the JWKS: no call to `auth`
per request, so an auth outage does not stop products from accepting valid tokens.

    verifier = SsoVerifier(issuer="https://auth-api.regisse.com", audience="flariane",
                           jwks_fetch=lambda: httpx.get(".../.well-known/jwks.json").json())
    principal = verifier.verify(token)            # raises SsoError
    principal.require_role("operator")
    principal.require_step_up(max_age_seconds=300, amr="totp")   # money routes

Rules (from the security review of fidelia's current JWT use): the algorithm is pinned to EdDSA and the key is chosen by
`kid`; the token's own `alg` is never trusted; `aud` and `iss` are mandatory; a token without a role is rejected.
"""

import base64
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

try:
    import jwt
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError as exc:  # pragma: no cover
    raise ImportError("regisse_kit.sso needs the 'sso' extra: pip install regisse-kit[sso]") from exc

ALG = "EdDSA"


class SsoError(Exception):
    """Invalid, expired, wrongly scoped or insufficient token. Map to 401 (invalid) or 403 (insufficient) in the product."""

    def __init__(self, code: str, status: int = 401):
        super().__init__(code)
        self.code, self.status = code, status


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@dataclass
class Principal:
    sub: str  # the hidden global user id (uuid)
    phone_key: str  # `ph` claim: `tel:+225...`, the key products like fidelia keep for their own data
    role: str
    roles: list[str]
    app: str
    amr: list[str]
    auth_time: int
    sid: str
    must_change_password: bool = False
    legacy_id: str | None = None  # `lid`: the user's id in this product's own table (migrated accounts); prefer it over matching by phone
    claims: dict[str, Any] = field(default_factory=dict, repr=False)

    def require_role(self, *roles: str) -> "Principal":
        if self.role not in roles:
            raise SsoError("forbidden_role", 403)
        return self

    def require_step_up(self, max_age_seconds: int, amr: str = "totp", now: float | None = None) -> "Principal":
        """Sensitive actions: the user must have re-proved a factor recently. A stolen login alone must not pass."""
        if amr not in self.amr or (now if now is not None else time.time()) - self.auth_time > max_age_seconds:
            raise SsoError("step_up_required", 403)
        return self

    def forbid_pending_password_change(self) -> "Principal":
        if self.must_change_password:
            raise SsoError("password_change_required", 403)
        return self


class SsoVerifier:
    def __init__(self, *, issuer: str, audience: str, jwks_fetch: Callable[[], dict], cache_seconds: int = 300, clock: Callable[[], float] = time.time) -> None:
        self.issuer, self.audience, self._fetch, self._ttl, self._clock = issuer, audience, jwks_fetch, cache_seconds, clock
        self._keys: dict[str, Ed25519PublicKey] = {}
        self._loaded_at = -1e12
        self._last_refetch = -1e12

    def _load(self) -> None:
        doc = self._fetch()
        self._keys = {k["kid"]: Ed25519PublicKey.from_public_bytes(_unb64u(k["x"])) for k in doc.get("keys", []) if k.get("kty") == "OKP" and k.get("crv") == "Ed25519"}
        self._loaded_at = self._last_refetch = self._clock()

    def _key(self, kid: str) -> Ed25519PublicKey:
        now = self._clock()
        if now - self._loaded_at > self._ttl:
            self._load()
        if kid not in self._keys and now - self._last_refetch > 30:  # unknown kid: key rotated, refetch at most every 30 s
            self._load()
        if kid not in self._keys:
            raise SsoError("unknown_key")
        return self._keys[kid]

    def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != ALG or not header.get("kid"):
                raise SsoError("invalid_token")
            claims = jwt.decode(
                token, self._key(header["kid"]), algorithms=[ALG], issuer=self.issuer, audience=self.audience,
                options={"require": ["exp", "iat", "sub", "iss", "aud", "sid"]},
            )
        except jwt.PyJWTError:
            raise SsoError("invalid_token") from None
        role = claims.get("role")
        if not role:  # never default a missing role to something privileged
            raise SsoError("invalid_token")
        return Principal(sub=claims["sub"], phone_key=claims.get("ph", ""), role=role, roles=claims.get("roles", []), app=claims.get("app", ""),
                         amr=claims.get("amr", []), auth_time=int(claims.get("auth_time", 0)), sid=claims["sid"],
                         must_change_password=bool(claims.get("mcp")), legacy_id=claims.get("lid"), claims=claims)
