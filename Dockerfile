FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project

COPY src/ src/
RUN uv sync --no-dev --no-editable

FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    mktorrent \
    ffmpeg \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -u 99 -g 100 -s /bin/bash -M podcastetl \
 && echo 'umask 0002' >> /etc/profile \
 && echo 'umask 0002' >> /etc/bash.bashrc

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

FROM base AS test

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/
RUN uv pip install "pytest>=8.0" "pytest-asyncio>=0.25"
COPY tests/ tests/
CMD ["pytest", "tests/", "-v", "-m", ""]

FROM base

VOLUME ["/config", "/output", "/torrent-data"]

ENV CONFIG_PATH="/config/feeds.yaml"
ENV HF_HOME="/config/hf-cache"
COPY --chmod=0755 entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
EXPOSE 8000
CMD ["sh", "-c", "podcast-etl -c \"$CONFIG_PATH\" serve"]
