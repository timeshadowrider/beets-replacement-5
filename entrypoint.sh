#!/bin/sh
set -e

# Create data directory
mkdir -p /app/data

# Create empty albums.json if it doesn't exist (so app doesn't crash)
if [ ! -f /data/albums.json ]; then
    echo "[]" > /data/albums.json
    echo "Created empty albums.json"
fi

# Start smart regeneration service in background
# This runs on a timer and only processes changed albums
python3 /app/scripts/smart_regenerate.py &
REGEN_PID=$!
echo "Started smart regeneration service (PID: $REGEN_PID)"

# Start the API server
exec uvicorn backend.app:app --host 0.0.0.0 --port ${APP_PORT:-7080} --log-level info
