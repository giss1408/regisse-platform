# Run the kit's tests in a clean Python 3.12 container (no local Python setup needed).
.PHONY: test
test:
	docker run --rm -v "$$PWD:/src:ro" -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim sh -c \
	  "cp -r /src /work && cd /work && pip install -q --no-cache-dir -e '.[dev]' 2>&1 | grep -v notice; python -m pytest -q -p no:cacheprovider"
