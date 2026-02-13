#!/usr/bin/env python3
import json
import os
from pathlib import Path

ALBUMS_FILE = "/data/albums.json"
RECENT_FILE = "/data/recent_albums.json"
LIB_ROOT = "/music/library"

def main():
    if not os.path.exists(ALBUMS_FILE):
        return

    with open(ALBUMS_FILE, "r") as f:
        albums = json.load(f)

    # Get folder mtimes
    for a in albums:
        folder = a.get("folder", "")
        abs_path = Path(LIB_ROOT) / folder.lstrip("/")
        try:
            a["_mtime"] = os.path.getmtime(abs_path)
        except OSError:
            a["_mtime"] = 0

    # Sort and save top 50 to recent_albums.json
    recent = sorted(albums, key=lambda x: x["_mtime"], reverse=True)[:50]
    
    with open(RECENT_FILE + ".tmp", "w") as f:
        json.dump(recent, f, indent=2)
    os.replace(RECENT_FILE + ".tmp", RECENT_FILE)
    print(f"Updated {RECENT_FILE}")

if __name__ == "__main__":
    main()