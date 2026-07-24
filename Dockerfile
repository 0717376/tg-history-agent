FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Node.js для Claude CLI (Agent SDK управляет им под капотом)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl git tzdata && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev || uv sync

COPY app ./app
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

CMD ["./entrypoint.sh"]
