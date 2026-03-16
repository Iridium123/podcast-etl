FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project

COPY src/ src/
RUN uv sync --no-dev

FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    mktorrent \
    ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app
VOLUME ["/config", "/output", "/torrent-data"]

ENV CONFIG_PATH="/config/feeds.yaml"
CMD ["sh", "-c", "podcast-etl -c \"$CONFIG_PATH\" poll"]
