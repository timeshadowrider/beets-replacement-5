# Dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    BEETS_CONFIG=/config/config.yaml \
    BEETS_LIBRARY=/data/beets-library.blb \
    APP_PORT=7080 \
    APP_USER=appuser

# Install runtime deps, build deps, and essential debugging tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Runtime + beets plugin deps
    ffmpeg \
    libmagic1 \
    sqlite3 \
    curl \
    git \
    libchromaprint-tools \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    imagemagick \
    build-essential \
    # Debugging + process inspection tools
    procps \
    coreutils \
    findutils \
    grep \
    sed \
    gawk \
    flac \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only requirements first to leverage layer caching
COPY backend/requirements.txt /app/backend/requirements.txt

# Install Python deps
RUN pip install --no-cache-dir -r /app/backend/requirements.txt \
 && pip install --no-cache-dir requests pylast pyacoustid langdetect beautifulsoup4 Pillow Wand

# Remove build deps to shrink image
RUN apt-get purge -y --auto-remove build-essential \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user and required dirs
RUN useradd -m -u 1000 ${APP_USER} \
 && mkdir -p /app/data /config /music /app/static \
 && chown -R ${APP_USER}:${APP_USER} /app /app/data /config /music /app/static

# Copy application backend and scripts
COPY backend /app/backend
COPY config /config
COPY scripts /app/scripts
COPY entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/scripts/*.sh /app/entrypoint.sh \
 && chown -R ${APP_USER}:${APP_USER} /app/backend /app/scripts /app/entrypoint.sh

EXPOSE ${APP_PORT}

# Switch to non-root user
USER ${APP_USER}

# ? Create the REAL symlink in the REAL user's home directory
# This is the ONLY path beets is actually reading
RUN mkdir -p /home/${APP_USER}/.config/beets && \
    rm -f /home/${APP_USER}/.config/beets/config.yaml && \
    ln -s /config/config.yaml /home/${APP_USER}/.config/beets/config.yaml

CMD ["/app/entrypoint.sh"]
