#!/bin/sh
# wrapper to keep Dockerfile happy and to run the python generator
exec python3 /app/scripts/regenerate_albums.py "$@"
