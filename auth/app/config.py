import base64
import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from regisse_kit.db_url import to_asyncpg_url

# product -> app -> roles that may open a session in that app (best role first).
# `self_register`: (app, role) a self-registered user gets, or None when accounts are created by staff only.
# `totp_roles`: roles that must present an authenticator code at login.
DEFAULT_PRODUCTS: dict = {
    "auth": {
        "apps": {"admin": ["super_admin", "admin"]},
        "self_register": None,
        "totp_roles": ["super_admin", "admin"],
    },
    "flariane": {
        "apps": {"customer": ["customer"], "admin": ["super_admin", "operator", "viewer", "pickup"]},
        "self_register": {"app": "customer", "role": "customer"},
        "totp_roles": ["super_admin", "operator", "viewer", "pickup"],
    },
    "fidelia": {
        "apps": {
            "customer": ["customer"],
            "merchant": ["merchant", "cashier"],
            "agent": ["agent"],
            "admin": ["super_admin", "admin"],
        },
        "self_register": {"app": "customer", "role": "customer"},
        "totp_roles": ["super_admin", "admin"],
    },
    # A supermarket platform: customers (phone-first, like the others), a retailer's staff (created by the platform's own backend through
    # /v1/service/staff when a platform admin adds them), and the platform admin (authenticator code required).
    "supermarket": {
        "apps": {"customer": ["customer"], "retailer": ["retailer_staff"], "admin": ["platform_admin"]},
        "self_register": {"app": "customer", "role": "customer"},
        "totp_roles": ["platform_admin"],
        # who runs this product's WhatsApp relay and reviews (see deps.current_relay_admin), and who among them may act on the product's staff
        "admin_roles": ["platform_admin"],
        "top_admin_roles": ["platform_admin"],
        # roles the product's own backend may create, reset and remove through the service endpoints (never its admins)
        "service_staff_roles": ["retailer_staff"],
    },
    # School management (Côte d'Ivoire). A school's staff (director, secretary, accountant/cashier) are enrolled by the product's backend
    # through /v1/service/staff; parents sign up themselves (phone-first); the platform admin runs the relay and creates the first director.
    "scolaris": {
        "apps": {"staff": ["director", "accountant", "secretary", "teacher"], "parent": ["parent"], "admin": ["platform_admin"]},
        "self_register": {"app": "parent", "role": "parent"},
        "totp_roles": ["platform_admin"],
        "admin_roles": ["platform_admin"],
        "top_admin_roles": ["platform_admin"],
        "service_staff_roles": ["director", "accountant", "secretary", "teacher"],
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AUTH_", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    issuer: str = "http://auth-api.localhost:8080"
    database_url: str = "postgresql+asyncpg://auth:auth@localhost:5434/auth"
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_storage: str = "memory://"
    trusted_proxy_hops: int = 0
    # Bearer token for GET /metrics (Prometheus/Alloy agent on infra_net). Empty = no /metrics endpoint. Never route it through Caddy.
    metrics_token: str = ""

    # base64, 32 bytes each
    field_encryption_key: str
    hash_key: str
    # Key rotation (see regisse_kit.keyring). `field_encryption_key` / `hash_key` above are key id "0" (the legacy, unversioned key).
    # A new key is added here as {"1": "<base64>"} and made current; the old one stays until `rotate-data-keys --verify` reports nothing left.
    field_keys_json: str = ""
    field_key_current: str = "0"
    hash_keys_json: str = ""
    hash_key_current: str = "0"

    access_token_minutes: int = 10
    refresh_days: int = 30
    activation_code_hours: int = 48
    temp_password_hours: int = 24

    # Accept +49 test numbers outside production (or when explicitly allowed).
    allow_test_numbers: bool | None = None

    # JSON: {"flariane": {...}} merged over DEFAULT_PRODUCTS
    products_json: str = ""
    # JSON: {"flariane": "<secret>"}: service keys for /v1/introspect
    service_keys_json: str = ""
    # JSON: {"fidelia": {"url": "http://fidelia-api:8000/api/internal/phone-changed", "key": "<secret>"}}: products that keep data keyed
    # by phone and must move it BEFORE a number change takes effect. Only these configured URLs are ever called.
    product_hooks_json: str = ""

    # Rate limits
    register_per_ip_hour: int = 10
    login_fail_limit: str = "5/15minute"
    # Per client IP, loose on purpose (carriers share IPs): stops one machine trying many accounts, not one account.
    login_ip_limit: str = "60/15minute"
    activate_ip_limit: str = "30/15minute"
    refresh_ip_limit: str = "120/minute"
    reset_request_limit: str = "3/hour"
    # Security delay between the confirmed code and a number change taking effect (the old number is warned meanwhile). 0 = immediate.
    phone_change_delay_hours: int = 24

    @field_validator("database_url")
    @classmethod
    def _asyncpg(cls, v: str) -> str:
        return to_asyncpg_url(v)

    @model_validator(mode="after")
    def _fail_closed_outside_dev(self) -> "Settings":
        """prod and staging refuse to start on dev defaults or weak secrets (all problems reported at once). The issuer and test-number rules are
        prod only: the local rehearsal stack runs as staging on http://localhost."""
        if self.env not in ("prod", "staging"):
            return self
        bad: list[str] = []
        if self.env == "prod" and (not self.issuer.startswith("https://") or "localhost" in self.issuer):
            bad.append("issuer must be a public https URL")
        if "auth:auth@" in self.database_url or "localhost" in self.database_url:
            bad.append("database_url still has dev defaults")
        if not self.rate_limit_storage.startswith("redis"):
            bad.append("rate_limit_storage must be redis (memory is per process and lost on restart)")
        if self.trusted_proxy_hops < 1:
            bad.append("trusted_proxy_hops must be >= 1 (behind Caddy/Cloudflare); 0 trusts the proxy address as the client")
        rings: dict[str, set[bytes]] = {}
        for label, ring, current in (("field", self.field_keyring, self.field_key_current), ("hash", self.hash_keyring, self.hash_key_current)):
            raws: list[bytes] = []
            for kid, b64 in ring.items():
                try:
                    raw = base64.b64decode(b64, validate=True)
                except Exception:  # noqa: BLE001
                    raw = b""
                if len(raw) != 32 or len(set(raw)) < 8:
                    bad.append(f"{label} key {kid!r} must be 32 random bytes in base64")
                raws.append(raw)
            if len(set(raws)) != len(raws):
                bad.append(f"{label} keys must all differ")
            if current not in ring:
                bad.append(f"{label}_key_current {current!r} is not in the {label} key ring")
            rings[label] = set(raws)
        if rings["field"] & rings["hash"]:
            bad.append("hash keys must differ from field encryption keys")
        if self.metrics_token and len(self.metrics_token) < 32:
            bad.append("metrics_token must be at least 32 characters")
        if any(len(k) < 32 for k in self.service_keys.values()):
            bad.append("every service key (service_keys_json) must be at least 32 characters")
        if self.env == "prod" and self.allow_test_numbers:
            bad.append("allow_test_numbers must not be on")
        if bad:
            raise ValueError("unsafe " + self.env + " configuration: " + "; ".join(bad))
        return self

    @property
    def field_keyring(self) -> dict[str, str]:
        return {**(json.loads(self.field_keys_json) if self.field_keys_json else {}), "0": self.field_encryption_key}

    @property
    def hash_keyring(self) -> dict[str, str]:
        return {**(json.loads(self.hash_keys_json) if self.hash_keys_json else {}), "0": self.hash_key}

    @property
    def test_numbers_ok(self) -> bool:
        return self.allow_test_numbers if self.allow_test_numbers is not None else self.env != "prod"

    @property
    def products(self) -> dict:
        merged = dict(DEFAULT_PRODUCTS)
        if self.products_json:
            merged.update(json.loads(self.products_json))
        return merged

    @property
    def product_hooks(self) -> dict[str, dict]:
        return json.loads(self.product_hooks_json) if self.product_hooks_json else {}

    @property
    def service_keys(self) -> dict[str, str]:
        return json.loads(self.service_keys_json) if self.service_keys_json else {}


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
