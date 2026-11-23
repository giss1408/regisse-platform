FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
# Security fixes for the base image that landed after the python image was built (Trivy gates the build on HIGH/CRITICAL with a fix).
RUN apt-get update && apt-get -y --no-install-recommends upgrade && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
# Shared platform kit (build context `kit` is declared in compose.platform.yaml)
COPY --from=kit . /kit
RUN pip install /kit
COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
RUN useradd --create-home --uid 10001 auth
USER auth
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips='*'"]
