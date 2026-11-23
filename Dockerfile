# One image: NestJS API (serves the built Vue UI) + Python worker + Hermes Agent.

FROM node:22-bookworm-slim AS ui
WORKDIR /ui
COPY ui/package*.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM node:22-bookworm-slim AS api
WORKDIR /api
COPY api/package*.json ./
RUN npm ci
COPY api/ ./
RUN npm run build && npm prune --omit=dev

FROM python:3.14-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
RUN pip install --no-cache-dir "uv==0.11.32"

# Hermes Agent pinned to one commit so results are reproducible. Installed from its own lockfile.
ARG HERMES_COMMIT=611f0ab757b307774c4a63c7b19f67d1f3aa6f6e
WORKDIR /opt/hermes
RUN git init -q && git remote add origin https://github.com/NousResearch/hermes-agent.git \
    && git fetch -q --depth 1 origin ${HERMES_COMMIT} && git checkout -q FETCH_HEAD \
    && UV_PYTHON=/usr/local/bin/python3 uv sync --frozen --no-dev \
    && uv pip install --python .venv/bin/python "pyyaml>=6"

WORKDIR /app
COPY --from=api /api /app/api
COPY --from=ui /ui/dist /app/ui/dist
COPY worker /app/worker
COPY tasks /app/tasks
COPY skills /app/skills
COPY judge_validation /app/judge_validation
COPY models.yaml /app/models.yaml

ENV EVALS_ROOT=/app \
    PYTHONPATH=/opt/hermes \
    WORKER_CMD="/opt/hermes/.venv/bin/python /app/worker/run_trial.py" \
    DB_PATH=/app/data/evals.db \
    UI_DIR=/app/ui/dist \
    PORT=3000
WORKDIR /app/api
EXPOSE 3000
CMD ["node", "dist/main.js"]
