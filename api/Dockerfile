# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser
COPY requirements.txt .
RUN pip install -r requirements.txt

# Default image of the compose stack (docker-compose.yml: api)
FROM base AS runtime
COPY app ./app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]

# Image used by api/docker-compose.test.yml
FROM base AS test
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY app ./app
COPY tests ./tests
COPY pytest.ini .
USER appuser
CMD ["pytest", "-q"]
