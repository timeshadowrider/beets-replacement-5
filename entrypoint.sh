#!/bin/sh
set -e
mkdir -p /app/data
python3 /app/scripts/regenerate_albums.py || true
exec uvicorn backend.app:app --host 0.0.0.0 --port ${APP_PORT:-7080} --log-level info
