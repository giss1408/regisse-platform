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
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AUTH_", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    issuer: str = "http://auth-api.localhost:8080"
    database_url: str = "postgresql+asyncpg://auth:auth@localhost:5434/auth"
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_storage: str = "memory://"
    trusted_proxy_hops: int = 0

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

    # Rate limits
    register_per_ip_hour: int = 10
    login_fail_limit: str = "5/15minute"
    # Per client IP, loose on purpose (carriers share IPs): stops one machine trying many accounts, not one account.
    login_ip_limit: str = "60/15minute"
    activate_ip_limit: str = "30/15minute"
    refresh_ip_limit: str = "120/minute"
    reset_request_limit: str = "3/hour"

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
    def service_keys(self) -> dict[str, str]:
        return json.loads(self.service_keys_json) if self.service_keys_json else {}


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
