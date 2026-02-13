#!/usr/bin/env python3
import json
import subprocess
import os
import time
import logging
from pathlib import Path
from collections import defaultdict

# Configuration matching your app.py
BEETS_CONFIG = "/config/config.yaml"
OUT_DIR = "/data"
ALBUMS_FILE = os.path.join(OUT_DIR, "albums.json")
LIB_ROOT = "/music/library"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("smart_regen")

def run_beet(args):
    """Executes beet list with a high timeout for large libraries."""
    try:
        p = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, check=False, timeout=600
        )
        return p.stdout or ""
    except Exception as e:
        logger.error(f"Beet command failed: {e}")
        return ""

def process_cycle():
    logger.info("Starting read-only regeneration...")
    
    # 1. Fetch Albums
    alb_fmt = "$id\t$albumartist\t$album\t$year\t$genre\t$label"
    alb_out = run_beet(["beet", "-c", BEETS_CONFIG, "list", "-a", "-f", alb_fmt])
    
    # 2. Fetch Tracks (to find folder paths)
    trk_fmt = "$album_id\t$path"
    trk_out = run_beet(["beet", "-c", BEETS_CONFIG, "list", "-f", trk_fmt])
    
    path_map = {}
    for line in trk_out.splitlines():
        p = line.split("\t")
        if len(p) < 2: continue
        # Just store the first path found for each album_id
        if p[0] not in path_map:
            path_map[p[0]] = os.path.dirname(p[1])

    output = []
    for line in alb_out.splitlines():
        parts = line.split("\t")
        if len(parts) < 6: continue
        
        album_id = parts[0]
        folder_abs = path_map.get(album_id, "")
        folder_rel = ""
        
        if folder_abs:
            folder_rel = "/" + os.path.relpath(folder_abs, LIB_ROOT).replace("\\", "/")

        output.append({
            "id": album_id,
            "albumartist": parts[1],
            "album": parts[2],
            "year": parts[3],
            "genre": parts[4],
            "label": parts[5],
            "folder": folder_rel,
            "cover": f"{folder_rel}/cover.jpg" if folder_rel else None
        })

    # ATOMIC WRITE: Write to temp, then replace
    temp_file = ALBUMS_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    os.replace(temp_file, ALBUMS_FILE)
    logger.info(f"Successfully wrote {len(output)} albums to {ALBUMS_FILE}")

if __name__ == "__main__":
    process_cycle()
