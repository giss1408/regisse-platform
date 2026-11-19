# Standard product Makefile (copy as Makefile.platform or merge)
COMPOSE = docker compose -f compose.platform.yaml
.PHONY: up down logs migrate test backup-db
up:        ; $(COMPOSE) up -d --build --wait
down:      ; $(COMPOSE) down
logs:      ; $(COMPOSE) logs -f --tail=100
migrate:   ; $(COMPOSE) exec web python manage.py migrate --noinput
test:      ; $(COMPOSE) run --rm web python manage.py test
backup-db: ; cd ../platform/infra && scripts/backup.sh
