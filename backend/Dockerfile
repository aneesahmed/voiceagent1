FROM python:3.12-slim

# Install uv binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Environment defaults for Cloud Run
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

# Install dependencies using cache mount
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source and knowledge base
COPY app/ app/
COPY kb/ kb/

# Ensure directory for SQLite database exists
RUN mkdir -p /app/data

EXPOSE 8080

# Cloud Run injects $PORT at runtime; sh -c ensures ${PORT:-8080} evaluates dynamically
CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
