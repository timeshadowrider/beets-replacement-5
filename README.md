# Beets Replacement 5 — Deterministic, Fingerprint-Driven Music Library Management

A modern, reproducible, fingerprint-aware replacement for the Beets Web API — designed for large libraries, home-lab reliability, and complete auditability.  
This project wraps Beets inside a FastAPI backend, adds a structured JSON frontend layer, and introduces watchers for imports, covers, lyrics, and library changes.

Built for environments where determinism, safety, and transparency matter more than “magic.”

## Features

### Music Management
- Automatic import system watching /music/inbox
- Real-time library updates with incremental regeneration
- Smart cover art fetching (background watcher)
- Lyrics Watcher (minimal): queues missing lyrics from Genius and updates metadata
- Full metadata display including bitrate, format, duration, fingerprints

### Modern Web Interface
- Three-tab layout: Dashboard, Albums, Watcher Activity
- Built-in audio player (Plyr.js)
- Responsive neon-themed dark UI
- Real-time search across albums and artists
- Album grid view with cover art

### Monitoring & Debugging
- Watcher activity dashboard
- Live logs with color-coded entries
- Debug console (Ctrl+D / Cmd+D)
- Cached inbox statistics

### Performance
- Incremental regeneration
- Cached stats
- Debounced watchers
- Efficient JSON storage

## Fingerprint-Driven Duplicate Detection

### Fingerprint Matching
- Uses Beets chroma plugin
- Stores fingerprints in acoustid_fingerprint
- Detects:
  - re-rips
  - alternate masters
  - streaming replacements
  - compilations
  - multi-disc overlaps

### Dedupe Commands

Preview duplicates:
docker exec -it beets-single-5 beet -c /config/config.yaml duplicates -f -t

Delete duplicates:
docker exec -it beets-single-5 beet -c /config/config.yaml duplicates -f -t -d

Fingerprint entire library:
docker exec -it beets-single-5 beet -c /config/config.yaml fingerprint

## Architecture Overview

Inbox ? Beets Import ? Library  
           ?  
   Fingerprinting + Metadata  
           ?  
   Beets Replacement API (FastAPI)  
           ?  
   JSON Layer (albums.json, recent.json)  
           ?  
   Web Interface (index.html)

Everything is modular, deterministic, and validated at each step.

## Installation

### 1. Clone the repository
git clone https://github.com/yourusername/beets-replacement-5
cd beets-replacement-5

### 2. Create directory structure
mkdir -p config data static scripts backend

### 3. Configure Beets
Place your config.yaml in ./config:

directory: /music/library
library: /data/beets-library.blb

import:
  move: yes
  copy: no
  write: yes
  resume: ask
  incremental: yes
  quiet_fallback: skip

plugins: chroma fetchart embedart duplicates smartplaylist

fetchart:
  auto: yes

embedart:
  auto: yes

### 4. Docker Compose

version: '3.8'

services:
  beets-replacement:
    build: .
    ports:
      - "7080:7080"
    volumes:
      - ./config:/config
      - ./data:/data
      - ./backend:/app/backend
      - ./scripts:/app/scripts
      - ./static:/app/static
      - /path/to/music:/music/library
      - /path/to/inbox:/music/inbox
    environment:
      - TZ=America/Los_Angeles
    restart: unless-stopped

### 5. Dockerfile (matches your real environment)

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    BEETS_CONFIG=/config/config.yaml \
    BEETS_LIBRARY=/data/beets-library.blb \
    APP_PORT=7080 \
    APP_USER=appuser

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libmagic1 sqlite3 curl git libchromaprint-tools \
    libjpeg-dev zlib1g-dev libpng-dev imagemagick build-essential \
    procps coreutils findutils grep sed gawk flac \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt

RUN pip install --no-cache-dir -r /app/backend/requirements.txt \
 && pip install --no-cache-dir requests pylast pyacoustid langdetect beautifulsoup4 Pillow Wand

RUN apt-get purge -y --auto-remove build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 ${APP_USER} \
 && mkdir -p /app/data /config /music /app/static \
 && chown -R ${APP_USER}:${APP_USER} /app /app/data /config /music /app/static

COPY backend /app/backend
COPY config /config
COPY scripts /app/scripts
COPY entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/scripts/*.sh /app/entrypoint.sh \
 && chown -R ${APP_USER}:${APP_USER} /app/backend /app/scripts /app/entrypoint.sh

EXPOSE ${APP_PORT}

USER ${APP_USER}

RUN mkdir -p /home/${APP_USER}/.config/beets && \
    rm -f /home/${APP_USER}/.config/beets/config.yaml && \
    ln -s /config/config.yaml /home/${APP_USER}/.config/beets/config.yaml

CMD ["/app/entrypoint.sh"]

### 6. Launch
docker compose up -d

### 7. Access the UI
http://localhost:7080

## Project Structure

beets-replacement-5/
+-- backend/
¦   +-- app.py
¦   +-- requirements.txt
¦   +-- ...
+-- static/
¦   +-- index.html
+-- scripts/
¦   +-- regenerate_albums.py
¦   +-- recompute_recent.py
¦   +-- smart_regenerate.py
+-- config/
¦   +-- config.yaml
+-- data/
¦   +-- beets-library.blb
+-- entrypoint.sh
+-- Dockerfile
+-- README.md

## API Endpoints

### Library
GET /api/stats  
POST /api/library/refresh  
POST /api/library/import  
GET /api/albums  
GET /api/albums/recent  

### Inbox
GET /api/inbox/stats  
GET /api/inbox/tree  
GET /api/inbox/folder  
POST /api/inbox/stats/clear-cache  

### Monitoring
GET /api/watcher/status

### Static
/  
/data/albums.json  
/music/library/{path}

## Automatic Import Workflow

1. Watcher detects new files  
2. Debounce to ensure complete copy  
3. Beets imports  
4. Fingerprinting + metadata  
5. JSON regeneration  
6. UI updates instantly  

## Inbox Cleanup

Deletes folders with no audio files and no UNPACK markers.

## Philosophy

- Deterministic — every action is explicit and reproducible  
- Audit-friendly — no silent mutations  
- Safety-first — destructive actions require fingerprint certainty  
- Complete — every album has cover art, metadata, and canonical JSON  
- Home-lab reliable — survives rebuilds and lifecycle events  

## Roadmap

- Playlist management  
- Multi-user support  
- Analytics dashboard  
- Mobile app  
- Spotify/Last.fm integration  
- Bulk editing  
- Advanced search  
- Export/backup tools  

## License

MIT License — see LICENSE.
