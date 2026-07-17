FROM node:22.22.0-bookworm-slim AS web-build

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM python:3.12.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/server/.venv/bin:$PATH" \
    DATA_DIR=/data \
    DATABASE_URL=sqlite:////data/app.db \
    JOB_WORKER_ENABLED=1 \
    PORT=8000

RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::Timeout=30 update \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::Timeout=30 install -y --no-install-recommends ffmpeg fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir uv==0.10.6 \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app

WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-dev
COPY server ./
COPY --from=web-build /build/dist ./app/static
COPY scripts /app/scripts
RUN mkdir -p /data \
    && chown -R app:app /app /data

USER app
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=45s --retries=6 \
  CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)); raise SystemExit(0 if data['data']['status']=='ready' else 1)"

ENTRYPOINT ["sh", "/app/scripts/entrypoint.sh"]
