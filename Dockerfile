# syntax=docker/dockerfile:1

FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependency files first so this layer is cached across app-code-only changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app/ ./app/
COPY eval/ ./eval/
RUN uv sync --frozen --no-dev


FROM python:3.11-slim AS runtime

# CPU-only OCR engine (DECISIONS.md E10), needed at runtime by pytesseract.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/app /app/app

# Placeholder static mount: real content lands here in Phase 8 (DECISIONS.md R11).
RUN mkdir -p /app/frontend/dist && chown -R appuser:appuser /app/frontend

ENV PATH="/app/.venv/bin:${PATH}"

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
