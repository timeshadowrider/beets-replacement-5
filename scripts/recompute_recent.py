#!/usr/bin/env python3
# recompute_recent.py
# Recomputes /data/recent_albums.json and /data/recent_artists.json from /data/albums.json

import json
import os
from pathlib import Path

ALBUMS = "/data/albums.json"
OUT_ALBUMS = "/data/recent_albums.json"
OUT_ARTISTS = "/data/recent_artists.json"
LIB_ROOT = "/music/library"


def get_album_mtime(album):
    """
    Get the most recent modification time for an album.
    Tries multiple strategies to find the best timestamp.
    """
    # Strategy 1: Use the folder's mtime
    folder = album.get("folder")
    if folder:
        # folder is like "/Artist/Album", need to prepend LIB_ROOT
        folder_abs = os.path.join(LIB_ROOT, folder.lstrip("/"))
        try:
            if os.path.exists(folder_abs):
                return os.path.getmtime(folder_abs)
        except Exception:
            pass
    
    # Strategy 2: Use the most recent track file mtime
    tracks = album.get("tracks", [])
    if tracks:
        max_mtime = 0
        for track in tracks:
            track_path = track.get("path", "")
            if track_path:
                # If path is relative, make it absolute
                if not track_path.startswith("/"):
                    track_path = os.path.join(LIB_ROOT, track_path)
                try:
                    if os.path.exists(track_path):
                        mtime = os.path.getmtime(track_path)
                        max_mtime = max(max_mtime, mtime)
                except Exception:
                    continue
        if max_mtime > 0:
            return max_mtime
    
    # Strategy 3: Fallback to 0 if nothing works
    return 0


def main():
    try:
        with open(ALBUMS, "r", encoding="utf-8") as f:
            albums = json.load(f)
    except Exception as e:
        print(f"Failed to load albums.json: {e}")
        return 1

    print(f"Processing {len(albums)} albums...")

    # Add _mtime to each album for sorting
    for a in albums:
        a["_mtime"] = get_album_mtime(a)

    # Sort by mtime (most recent first) and take top 50
    albums_sorted = sorted(albums, key=lambda x: x["_mtime"], reverse=True)
    recent_albums = albums_sorted[:50]

    # Extract unique recent artists
    seen = set()
    recent_artists = []
    for a in recent_albums:
        artist = a.get("albumartist") or a.get("artist") or ""
        if artist and artist not in seen:
            seen.add(artist)
            recent_artists.append({"artist": artist})

    # Write output files
    with open(OUT_ALBUMS, "w", encoding="utf-8") as f:
        json.dump(recent_albums, f, indent=2, ensure_ascii=False)

    with open(OUT_ARTISTS, "w", encoding="utf-8") as f:
        json.dump(recent_artists, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(recent_albums)} albums to {OUT_ALBUMS}")
    print(f"Wrote {len(recent_artists)} artists to {OUT_ARTISTS}")
    
    # Show top 5 recent albums for verification
    print("\nTop 5 most recent albums:")
    for i, a in enumerate(recent_albums[:5], 1):
        from datetime import datetime
        mtime = a.get("_mtime", 0)
        date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime > 0 else "unknown"
        print(f"{i}. {a.get('albumartist', 'Unknown')} - {a.get('album', 'Unknown')} ({date_str})")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())