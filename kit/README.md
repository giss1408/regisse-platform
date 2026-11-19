# regisse_kit

Shared, framework-neutral helpers for the com_regisse backends. **No tables, no global settings:** every function takes its keys and config as arguments. Breaking change = major version; products pin exact versions.

| Module | What |
|---|---|
| `crypto` | `FieldCipher` (AES-256-GCM), `KeyedHasher` (HMAC-SHA256 with purpose), argon2id passwords, `token_hash`, random helpers |
| `phone` | Côte d'Ivoire normalisation to E.164, `mask_phone`, `phone_key` (`tel:+225...`, fidelia's key / `ph` claim), `wa_link` (wa.me) |
| `passwords` | password policy |
| `codes` | one-time codes as pure logic (`issue` / `check`, attempts, expiry, bound to subject+purpose) |
| `webhooks` | `verify_hmac_sha256` (Meta `X-Hub-Signature-256`) |
| `db_url` | `to_asyncpg_url` |
| `health` | FastAPI `/healthz` (liveness) and `/readyz` (dependency checks, 503 when degraded) — needs extra `[fastapi]` |

Wire formats equal flariane's (`tests/test_flariane_parity.py` holds vectors produced by flariane's own code), so stored data stays readable.

```
make test        # runs the suite in a clean python:3.12 container
```

Install in a product (until the kit has a git remote and tags): Docker `additional_contexts: kit: ../platform/kit` then `COPY --from=kit . /kit && pip install /kit` (see `flariane/backend/Dockerfile`). Later: `pip install git+ssh://.../regisse-kit@v0.1.0`.

Adoption rule: a product replaces its own copy with a thin wrapper that keeps its API, and its own tests must stay green.

Not in v0.1 (planned with the `auth` service): JWT with JWKS verification (`sso`), `OtpChannel` interface and channels, relay queue helpers.
