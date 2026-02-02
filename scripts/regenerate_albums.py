#!/usr/bin/env python3
"""
regenerate_albums.py

Full or targeted regeneration of /data/albums.json using beets.
- Explicit subprocess calls (no shell expansion).
- Debug logging to /data/albums-beet.log.
- Adds full track metadata, album totals, and cover art.
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


# ---------------------------------------------------------------------------
# UTIL
# ---------------------------------------------------------------------------

def run_beet(args):
    try:
        p = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False
        )
        return p.stdout or ""
    except Exception as e:
        return f"beet invocation failed: {e}\n"


def log(out):
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(BEET_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.utcnow().isoformat()} ---\n")
            f.write(out)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# TRACKS
# ---------------------------------------------------------------------------

def get_tracks(albumartist, album):
    fmt = "$disc\t$track\t$title\t$length\t$bitrate\t$format\t$path"
    args = [
        "beet", "-c", BEETS_CONFIG,
        "list", "-t",
        f"albumartist:{albumartist}",
        f"album:{album}",
        "-f", fmt
    ]

    out = run_beet(args)
    log(out)

    tracks = []
    total_length = 0

    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 7:
            continue

        disc, track, title, length, bitrate, fmtc, path = (p.strip() for p in parts)

        try:
            disc = int(disc)
        except Exception:
            disc = 1

        try:
            track = int(track)
        except Exception:
            track = None

        try:
            length = int(float(length))
        except Exception:
            length = None

        try:
            bitrate = int(bitrate)
        except Exception:
            bitrate = None

        rel_path = path.replace(LIB_ROOT, "")

        if length:
            total_length += length

        tracks.append({
            "disc": disc,
            "track": track,
            "title": title,
            "length": length,
            "bitrate": bitrate,
            "format": fmtc,
            "path": rel_path,
        })

    tracks.sort(key=lambda t: (t["disc"], t["track"] or 0))
    return tracks, total_length


# ---------------------------------------------------------------------------
# COVER ART
# ---------------------------------------------------------------------------

def get_cover(albumartist, album):
    args = [
        "beet", "-c", BEETS_CONFIG,
        "list", "-a",
        f"albumartist:{albumartist}",
        f"album:{album}",
        "-f", "$artpath"
    ]

    out = run_beet(args)
    log(out)

    for line in out.splitlines():
        art = line.strip()
        if art and os.path.exists(art):
            return art.replace(LIB_ROOT, "")

    return None


# ---------------------------------------------------------------------------
# ALBUMS
# ---------------------------------------------------------------------------

def run_beet_list(query=None):
    base_fmt = "$albumartist\t$album\t$year\t$path"
    args = ["beet", "-c", BEETS_CONFIG, "list", "-a"]

    if query:
        args.append(query)

    args.extend(["-f", base_fmt])

    out = run_beet(args)
    log(out)

    albums = []
    seen = set()

    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue

        albumartist, album, year, path = (p.strip() for p in parts[:4])

        if not albumartist or not album:
            continue
        if path == LIB_ROOT:
            continue

        key = (albumartist, album, year)
        if key in seen:
            continue
        seen.add(key)

        folder = path.replace(LIB_ROOT, "")

        tracks, total_length = get_tracks(albumartist, album)
        cover = get_cover(albumartist, album)

        albums.append({
            "albumartist": albumartist,
            "album": album,
            "year": year,
            "folder": folder,
            "cover": cover,
            "track_count": len(tracks),
            "total_length": total_length,
            "tracks": tracks,
        })

    return albums


# ---------------------------------------------------------------------------
# FILE HANDLING
# ---------------------------------------------------------------------------

def write_albums(albums):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(albums, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(albums)} albums to {OUT_PATH}")


def load_existing():
    if not os.path.exists(OUT_PATH):
        return []
    try:
        with open(OUT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def merge(existing, new):
    new_map = {
        (a["albumartist"], a["album"], a["year"]): a
        for a in new
    }

    merged = [
        e for e in existing
        if (e.get("albumartist"), e.get("album"), e.get("year")) not in new_map
    ]

    merged.extend(new_map.values())
    return merged


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None

    if arg:
        query = f'path:"{arg}"'
        new_albums = run_beet_list(query)

        if not new_albums:
            print("no albums found for target", file=sys.stderr)
            sys.exit(1)

        merged = merge(load_existing(), new_albums)
        write_albums(merged)
        print(f"targeted regen: {len(new_albums)} album(s)")
        return

    albums = run_beet_list()
    write_albums(albums)


if __name__ == "__main__":
    main()
