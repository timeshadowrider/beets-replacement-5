#!/usr/bin/env python3
# recompute_recent.py
# Recomputes /data/recent_albums.json and /data/recent_artists.json from /data/albums.json

import json
import os
ALBUMS = "/data/albums.json"
OUT_ALBUMS = "/data/recent_albums.json"
OUT_ARTISTS = "/data/recent_artists.json"

def main():
    try:
        with open(ALBUMS, "r", encoding="utf-8") as f:
            albums = json.load(f)
    except Exception as e:
        print("failed to load albums.json:", e)
        return 1

    for a in albums:
        p = a.get("example_path") or a.get("path") or ""
        try:
            a["_mtime"] = os.path.getmtime(p) if p else 0
        except Exception:
            a["_mtime"] = 0

    albums_sorted = sorted(albums, key=lambda x: x["_mtime"], reverse=True)
    recent_albums = albums_sorted[:50]

    seen = set()
    recent_artists = []
    for a in recent_albums:
        artist = a.get("albumartist") or a.get("artist") or ""
        if artist and artist not in seen:
            seen.add(artist)
            recent_artists.append({"artist": artist})

    with open(OUT_ALBUMS, "w", encoding="utf-8") as f:
        json.dump(recent_albums, f, indent=2, ensure_ascii=False)

    with open(OUT_ARTISTS, "w", encoding="utf-8") as f:
        json.dump(recent_artists, f, indent=2, ensure_ascii=False)

    print("wrote", OUT_ALBUMS, "and", OUT_ARTISTS)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
