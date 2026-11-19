# Run the auth tests in clean containers against Postgres 17 (no local Python/DB setup).
.PHONY: test
test:
	-docker rm -f auth-testdb >/dev/null 2>&1
	docker run -d --name auth-testdb -e POSTGRES_USER=auth -e POSTGRES_PASSWORD=auth -e POSTGRES_DB=auth postgres:17-alpine -c port=5434 >/dev/null
	sleep 6
	docker run --rm --network container:auth-testdb -v "$$PWD:/src:ro" -v "$$PWD/../kit:/kit:ro" -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim sh -c \
	  "cp -r /src /work && cp -r /kit /kitwork && cd /work && pip install -q --no-cache-dir -r requirements.txt -r requirements-dev.txt /kitwork 2>&1 | grep -v -e notice -e WARNING; python -m pytest -q -p no:cacheprovider $(ARGS) 2>&1 | tail -40"
	-docker rm -f auth-testdb >/dev/null 2>&1
