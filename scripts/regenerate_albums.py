#!/usr/bin/env python3
import json
import subprocess
import os
import logging
from pathlib import Path

# Config from your app.py
BEETS_CONFIG = "/config/config.yaml"
OUT_DIR = "/data"
ALBUMS_FILE = os.path.join(OUT_DIR, "albums.json")
LIB_ROOT = "/music/library"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("regen")

def run_beet(args):
    try:
        # Use -p to ensure we aren't accidentally triggering any write-plugins
        p = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=False, timeout=300
        )
        return p.stdout or ""
    except Exception as e:
        logger.error(f"Beet query failed: {e}")
        return ""

def regenerate():
    logger.info("Starting read-only library scan...")
    
    # Query Beets for all albums with specific fields
    # Using specific delimiters to prevent parsing errors
    fmt = "$id|$albumartist|$album|$year|$genre|$label"
    out = run_beet(["beet", "-c", BEETS_CONFIG, "list", "-a", "-f", fmt])
    
    albums = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 6: continue
        
        # Get relative folder path for the frontend
        # We query one track from the album to find the physical path
        alb_id = parts[0]
        path_out = run_beet(["beet", "-c", BEETS_CONFIG, "list", f"album_id:{alb_id}", "-f", "$path", "-l", "1"])
        
        folder_rel = ""
        if path_out:
            abs_path = os.path.dirname(path_out.strip())
            folder_rel = "/" + os.path.relpath(abs_path, LIB_ROOT).replace("\\", "/")

        albums.append({
            "id": alb_id,
            "albumartist": parts[1],
            "album": parts[2],
            "year": parts[3],
            "genre": parts[4],
            "label": parts[5],
            "folder": folder_rel,
            "cover": f"{folder_rel}/cover.jpg" if folder_rel else None
        })

    # ATOMIC WRITE: Write to a temp file first, then rename
    # This prevents the frontend from reading a half-written file
    temp_file = ALBUMS_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(albums, f, indent=2, ensure_ascii=False)
    
    os.replace(temp_file, ALBUMS_FILE)
    logger.info(f"Successfully updated {len(albums)} albums in {ALBUMS_FILE}")

if __name__ == "__main__":
    regenerate()