#!/usr/bin/env python3
import json
import subprocess
import os
import time
import logging
from pathlib import Path

BEETS_CONFIG = "/config/config.yaml"
OUT_DIR = "/data"
ALBUMS_FILE = os.path.join(OUT_DIR, "albums.json")
STATE_FILE = os.path.join(OUT_DIR, "regen_state.json")
LIB_ROOT = "/music/library"

CHECK_INTERVAL = 3600
SKIP_RECENTLY_CHECKED = 7 * 24 * 3600
MODIFICATION_WINDOW = 7 * 24 * 3600

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(OUT_DIR, "smart_regen.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("smart_regen")


# ---------------- PATH NORMALIZATION ---------------- #

def to_relative_folder(folder_abs: str) -> str:
    """
    Convert an absolute folder path under LIB_ROOT into a
    relative path like '/Artist/Album' for the frontend.
    """
    try:
        rel = os.path.relpath(folder_abs, LIB_ROOT)
    except ValueError:
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

    folder_abs = os.path.dirname(cover_abs)
    rel_folder = to_relative_folder(folder_abs)
    return rel_folder + "/cover.jpg"


# ---------------- BEETS HELPERS ---------------- #

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


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def load_albums():
    if not os.path.exists(ALBUMS_FILE):
        return []
    try:
        with open(ALBUMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_albums(albums):
    with open(ALBUMS_FILE, "w", encoding="utf-8") as f:
        json.dump(albums, f, indent=2)


def get_tracks(album_id):
    fmt = "$disc\t$track\t$title\t$length\t$bitrate\t$format\t$path"
    args = ["beet", "-c", BEETS_CONFIG, "list", "-f", fmt, f"album_id:{album_id}"]
    out = run_beet(args)

    tracks = []
    total_length = 0
    first_path = None

    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 7:
            continue

        disc, track, title, length, bitrate, fmtc, path = parts

        try: disc = int(disc)
        except: disc = 1

        try: track = int(track)
        except: track = None

        try: length = int(float(length))
        except: length = None

        try: bitrate = int(bitrate)
        except: bitrate = None

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


def get_album_metadata(album_id):
    fmt = "$albumartist\t$album\t$year"
    args = ["beet", "-c", BEETS_CONFIG, "list", "-a", "-f", fmt, f"id:{album_id}"]
    out = run_beet(args).strip()

    if not out:
        return None

    parts = out.split("\t")
    if len(parts) < 3:
        return None

    return {
        "albumartist": parts[0],
        "album": parts[1],
        "year": parts[2]
    }


def get_all_album_ids():
    args = ["beet", "-c", BEETS_CONFIG, "list", "-a", "-f", "$id"]
    out = run_beet(args)
    ids = []
    for line in out.splitlines():
        if line.strip().isdigit():
            ids.append(int(line.strip()))
    return ids


def update_album(albums, new_album):
    key = (new_album["albumartist"], new_album["album"], new_album["year"])
    for i, a in enumerate(albums):
        if (a["albumartist"], a["album"], a["year"]) == key:
            albums[i] = new_album
            return albums
    albums.append(new_album)
    return albums


# ---------------- MAIN CYCLE ---------------- #

def process_cycle():
    logger.info("Starting regeneration cycle")

    state = load_state()
    now = time.time()

    album_ids = get_all_album_ids()
    logger.info(f"Found {len(album_ids)} albums")

    albums = load_albums()
    processed = 0

    for album_id in album_ids:
        key = str(album_id)
        last_checked = state.get(key, 0)

        if now - last_checked < SKIP_RECENTLY_CHECKED:
            continue

        meta = get_album_metadata(album_id)
        if not meta:
            state[key] = now
            continue

        tracks, total_length, first_path = get_tracks(album_id)
        if not tracks or not first_path:
            state[key] = now
            continue

        folder_abs = os.path.dirname(first_path)
        cover_abs = find_cover(folder_abs)

        folder_rel = to_relative_folder(folder_abs)
        cover_rel = to_relative_cover(cover_abs) if cover_abs else None

        album_obj = {
            "albumartist": meta["albumartist"],
            "album": meta["album"],
            "year": meta["year"],
            "folder": folder_rel,
            "cover": cover_rel,
            "track_count": len(tracks),
            "total_length": total_length,
            "tracks": tracks
        }

        albums = update_album(albums, album_obj)
        state[key] = now
        processed += 1

    save_albums(albums)
    save_state(state)

    logger.info(f"Cycle complete: processed {processed} albums")


def main_loop():
    logger.info("Smart regeneration service started")
    while True:
        try:
            process_cycle()
        except Exception as e:
            logger.exception(f"Error in cycle: {e}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main_loop()
