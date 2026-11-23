"""Fail-closed configuration: a prod/staging process refuses to start on dev defaults or weak secrets."""

import base64

import pytest
from pydantic import ValidationError

from app.config import Settings

GOOD = {
    "env": "prod",
    "issuer": "https://auth-api.regisse.com",
    "database_url": "postgresql://role_auth:" + "p" * 30 + "@postgres:5432/db_auth",
    "redis_url": "redis://auth:" + "r" * 30 + "@redis:6379/0",
    "rate_limit_storage": "redis://auth:" + "r" * 30 + "@redis:6379/1",
    "trusted_proxy_hops": 2,
    "field_encryption_key": base64.b64encode(bytes(range(32))).decode(),
    "hash_key": base64.b64encode(bytes(range(32, 64))).decode(),
    "metrics_token": "m" * 40,
    "service_keys_json": '{"flariane": "' + "k" * 40 + '"}',
}


def make(**over):
    return Settings(_env_file=None, **{**GOOD, **over})


def test_a_correct_prod_config_starts():
    assert make().env == "prod"


@pytest.mark.parametrize("over,why", [
    ({"database_url": "postgresql://auth:auth@postgres/db"}, "database"),
    ({"rate_limit_storage": "memory://"}, "rate_limit_storage"),
    ({"trusted_proxy_hops": 0}, "trusted_proxy_hops"),
    ({"hash_key": base64.b64encode(b"h" * 32).decode()}, "hash_key"),
    ({"field_encryption_key": base64.b64encode(b"e" * 31).decode()}, "field_encryption_key"),
    ({"hash_key": GOOD["field_encryption_key"]}, "hash_key"),
    ({"metrics_token": "short"}, "metrics_token"),
    ({"service_keys_json": '{"flariane": "short"}'}, "service key"),
])
def test_weak_or_default_prod_config_is_refused(over, why):
    for env in ("prod", "staging"):
        with pytest.raises(ValidationError) as e:
            make(env=env, **over)
        assert why in str(e.value)


@pytest.mark.parametrize("over,why", [
    ({"issuer": "http://auth-api.regisse.com"}, "issuer"),
    ({"issuer": "https://auth-api.localhost"}, "issuer"),
    ({"allow_test_numbers": True}, "allow_test_numbers"),
])
def test_prod_only_rules(over, why):
    with pytest.raises(ValidationError) as e:
        make(**over)
    assert why in str(e.value)
    assert make(env="staging", **over).env == "staging"  # the local rehearsal stack is staging on http://localhost


def test_dev_and_test_stay_permissive():
    s = Settings(_env_file=None, env="dev", field_encryption_key=GOOD["field_encryption_key"], hash_key=GOOD["hash_key"])
    assert s.rate_limit_storage == "memory://"
