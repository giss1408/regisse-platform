import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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
