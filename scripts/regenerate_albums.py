#!/usr/bin/env python3
"""
regenerate_albums.py

Full or targeted regeneration of /data/albums.json using beets.
- Uses subprocess with explicit args to avoid shell expansion issues.
- Filters out empty/placeholder rows and the library root.
- Logs raw beet output to /data/albums-beet.log for debugging.
"""
import json
import subprocess
import sys
import os
from datetime import datetime

BEETS_CONFIG = "/config/config.yaml"
OUT_DIR = "/data"
OUT_PATH = os.path.join(OUT_DIR, "albums.json")
BEET_LOG = os.path.join(OUT_DIR, "albums-beet.log")
LIB_ROOT = "/music/library"

def run_beet_list(query=None):
    base_fmt = "$albumartist\t$album\t$year\t$path"
    args = ["beet", "-c", BEETS_CONFIG, "list", "-a"]
    if query:
        args.append(query)
    args.extend(["-f", base_fmt])
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        out = p.stdout or ""
    except Exception as e:
        out = f"beet invocation failed: {e}\n"
    # write raw beet output for debugging
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(BEET_LOG, "a", encoding="utf-8") as L:
            L.write(f"\n--- {datetime.utcnow().isoformat()} ---\n")
            L.write(out)
    except Exception:
        pass
    albums = []
    seen = set()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        albumartist, album, year, path = (p.strip() for p in parts[:4])
        # Skip empty/placeholder rows and the library root
        if not album or not albumartist:
            continue
        if path == LIB_ROOT:
            continue
        key = (albumartist, album, year)
        if key in seen:
            continue
        seen.add(key)
        albums.append({
            "albumartist": albumartist,
            "album": album,
            "year": year,
            "example_path": path
        })
    return albums

def write_albums_file(albums):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(albums, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(albums)} albums to {OUT_PATH}")

def load_existing_albums():
    if not os.path.exists(OUT_PATH):
        return []
    try:
        with open(OUT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def merge_targeted(existing, new_albums):
    new_map = { (a["albumartist"], a["album"], a["year"]) : a for a in new_albums }
    merged = []
    for e in existing:
        key = (e.get("albumartist"), e.get("album"), e.get("year"))
        if key in new_map:
            continue
        merged.append(e)
    for key, a in new_map.items():
        merged.append(a)
    return merged

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg:
        query = f'path:"{arg}"'
        new_albums = run_beet_list(query=query)
        if not new_albums:
            print("no albums found for target or beet failed", file=sys.stderr)
            sys.exit(1)
        existing = load_existing_albums()
        merged = merge_targeted(existing, new_albums)
        write_albums_file(merged)
        print(f"targeted regen: updated {len(new_albums)} album(s) for {arg}")
        return
    albums = run_beet_list(query=None)
    if albums is None:
        print("beet list failed", file=sys.stderr)
        sys.exit(1)
    write_albums_file(albums)

if __name__ == "__main__":
    main()
