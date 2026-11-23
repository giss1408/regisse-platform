SHELL := /bin/bash
.PHONY: nets isolation-test init up down ps logs new-product backup restore-test smoke clean-demo monitoring-init monitoring-up monitoring-down

init:            ## create .env with generated secrets (idempotent)
	@scripts/init.sh

up:              ## start shared infra
	docker compose up -d --wait
	@scripts/harden-redis.sh
	@scripts/connect-nets.sh

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f --tail=100

new-product:     ## make new-product NAME=tourism [POSTGIS=1] [UPSTREAM=tourism-web:8000] [ISOLATED=1]
	@scripts/new-product.sh $(NAME) $(if $(POSTGIS),--postgis,) $(if $(UPSTREAM),--upstream $(UPSTREAM),) $(if $(ISOLATED),--isolated,)

nets:            ## re-attach the shared services to every product network (after recreating infra containers)
	@scripts/connect-nets.sh

isolation-test:  ## network isolation check with stand-in containers (does not touch the running stack)
	@tests/isolation.sh

backup:
	@scripts/backup.sh

restore-test:
	@scripts/restore-test.sh

smoke:           ## end-to-end smoke test of the infra (uses a throwaway demo product)
	@tests/smoke.sh

monitoring-init: ## create the read-only monitor DB/Redis identities and monitoring/.env
	@scripts/make-monitoring.sh

monitoring-up:   ## start the agent + exporters (ships to Grafana Cloud)
	docker compose -f monitoring/compose.yaml --env-file monitoring/.env up -d --wait

monitoring-down:
	docker compose -f monitoring/compose.yaml --env-file monitoring/.env down
