#!/bin/sh
set -eu

mkdir -p "${DATA_DIR:-/data}" "${DATA_DIR:-/data}/assets" "${DATA_DIR:-/data}/tmp"
cd /app/server
python -m alembic upgrade head
python -m app.seed
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
