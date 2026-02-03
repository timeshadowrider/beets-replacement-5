#!/usr/bin/env python3
import json
import subprocess
import os
import logging
from pathlib import Path

BEETS_CONFIG = "/config/config.yaml"
OUT_DIR = "/data"
ALBUMS_FILE = os.path.join(OUT_DIR, "albums.json")
LIB_ROOT = "/music/library"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(OUT_DIR, "regen.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("regen")


def run_beet(args):
    try:
        p = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=300
        )
        return p.stdout or ""
    except Exception as e:
        logger.error(f"Beet command failed: {e}")
        return ""


def get_all_albums():
    fmt = "$id\t$albumartist\t$album\t$year"
    args = ["beet", "-c", BEETS_CONFIG, "list", "-a", "-f", fmt]
    out = run_beet(args)

    albums = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        album_id, artist, album, year = parts
        if not album_id.isdigit():
            continue
        albums.append({
            "id": int(album_id),
            "albumartist": artist,
            "album": album,
            "year": year
        })
    return albums


def get_tracks_for_album(album_id):
    fmt = "$disc\t$track\t$title\t$length\t$bitrate\t$format\t$path"
    args = [
        "beet", "-c", BEETS_CONFIG, "list", "-f", fmt,
        f"album_id:{album_id}"
    ]
    out = run_beet(args)

    tracks = []
    total_length = 0
    first_path = None

    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 7:
            continue

        disc, track, title, length, bitrate, fmtc, path = parts

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

        if first_path is None:
            first_path = path

        if length:
            total_length += length

        tracks.append({
            "disc": disc,
            "track": track,
            "title": title,
            "length": length,
            "bitrate": bitrate,
            "format": fmtc,
            "path": path
        })

    tracks.sort(key=lambda t: (t["disc"], t["track"] or 0))
    return tracks, total_length, first_path


def find_cover(folder):
    candidates = [
        "cover.jpg", "cover.png",
        "folder.jpg", "folder.png",
        "front.jpg", "front.png"
    ]
    for c in candidates:
        p = os.path.join(folder, c)
        if os.path.exists(p):
            return p
    return None


def to_relative_folder(folder_abs: str) -> str:
    """
    Convert an absolute folder path under LIB_ROOT into a
    relative path like '/Artist/Album' for the frontend.
    """
    try:
        rel = os.path.relpath(folder_abs, LIB_ROOT)
    except ValueError:
        # If something is weird, fall back to just stripping LIB_ROOT prefix
        if folder_abs.startswith(LIB_ROOT):
            rel = folder_abs[len(LIB_ROOT):]
        else:
            rel = folder_abs

    rel = rel.replace("\\", "/").strip("/")
    return "/" + rel if rel else ""


def to_relative_cover(cover_abs: str) -> str:
    """
    Convert an absolute cover path into a relative path
    rooted at /music/library, e.g. '/Artist/Album/cover.jpg'.
    """
    if not cover_abs:
        return None

    # Expect cover_abs to be under LIB_ROOT
    folder_abs = os.path.dirname(cover_abs)
    rel_folder = to_relative_folder(folder_abs)
    return rel_folder + "/cover.jpg"


def regenerate():
    logger.info("Starting full regeneration")

    albums = get_all_albums()
    logger.info(f"Found {len(albums)} albums")

    output = []

    for a in albums:
        logger.info(f"Processing: {a['albumartist']} - {a['album']}")

        tracks, total_length, first_path = get_tracks_for_album(a["id"])

        if not tracks or not first_path:
            logger.warning(f"No tracks found for album ID {a['id']}")
            continue

        # first_path is a full path like /music/library/Artist/Album/track.flac
        folder_abs = os.path.dirname(first_path)
        cover_abs = find_cover(folder_abs)

        folder_rel = to_relative_folder(folder_abs)
        cover_rel = to_relative_cover(cover_abs) if cover_abs else None

        album_obj = {
            "albumartist": a["albumartist"],
            "album": a["album"],
            "year": a["year"],
            # frontend uses this to build /music/library + folder + /cover.jpg
            "folder": folder_rel,
            # optional, but now also relative and consistent
            "cover": cover_rel,
            "track_count": len(tracks),
            "total_length": total_length,
            "tracks": tracks
        }

        output.append(album_obj)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(ALBUMS_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info(f"Saved {len(output)} albums to {ALBUMS_FILE}")


if __name__ == "__main__":
    regenerate()
