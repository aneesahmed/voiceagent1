FROM python:3.12-slim

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

# Copy dependency files from backend/
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source and knowledge base
COPY backend/app/ app/
COPY backend/kb/ kb/

# Ensure directory for SQLite database exists
RUN mkdir -p /app/data

EXPOSE 8080

# Cloud Run dynamically assigns $PORT at container start
CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
