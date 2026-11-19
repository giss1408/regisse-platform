# Product template

1. `cd platform/infra && make new-product NAME=<name> [POSTGIS=1] [UPSTREAM=<name>-web:<port>]`
2. Copy `template/compose.yaml` to the product repo as `compose.platform.yaml`, replace `__NAME__`, `__PORT__`.
3. Create the product's `.env.platform` from `infra/secrets/<name>.env` + the product's own settings (see its `.env.example`).
4. Product must expose `GET /healthz` returning 200 over plain HTTP when called with `X-Forwarded-Proto: https` and `Host: <name>-api.<domain>` (Caddy health check sends both).
5. `docker compose -f compose.platform.yaml up -d --build --wait`
6. Verify: `curl -H "Host: <name>-api.localhost" http://127.0.0.1:8080/healthz`

Rules: no postgres/redis in product compose, no published ports, `mem_limit`/`cpus` set, non-root user, secrets only in `.env.platform` (gitignored).
