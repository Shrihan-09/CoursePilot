# Pinned to 3.12: several dependencies (psycopg binary, pgvector) do not yet
# ship wheels for 3.14. See backend/pyproject.toml.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependency layer first, so source edits don't invalidate the install cache.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install ".[dev]" || true

COPY . .

# Installed again after source is present, so the `app` package resolves.
RUN pip install -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
