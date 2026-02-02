#!/usr/bin/env python3
"""
smart_regenerate.py

Incremental album regeneration that:
- Runs on a timer in the background
- Only processes albums modified in the last 7 days
- Tracks last-checked timestamps to avoid redundant work
- Logs all activity
"""

import json
import subprocess
import os
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

BEETS_CONFIG = "/config/config.yaml"
OUT_DIR = "/data"
ALBUMS_FILE = os.path.join(OUT_DIR, "albums.json")
STATE_FILE = os.path.join(OUT_DIR, "regen_state.json")
LOG_FILE = os.path.join(OUT_DIR, "smart_regen.log")
LIB_ROOT = Path("/music/library")

CHECK_INTERVAL = 3600  # Check every hour (in seconds)
SKIP_RECENTLY_CHECKED = 7 * 24 * 60 * 60  # Skip if checked within 7 days (in seconds)
MODIFICATION_WINDOW = 7 * 24 * 60 * 60  # Only process if modified in last 7 days (in seconds)

# ---------------------------------------------------------------------------
# LOGGING SETUP
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("smart_regen")

# ---------------------------------------------------------------------------
# STATE MANAGEMENT
# ---------------------------------------------------------------------------

def load_state():
    """Load the last-checked timestamps for each album."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load state: {e}")
        return {}


def save_state(state):
    """Save the last-checked timestamps."""
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")


# ---------------------------------------------------------------------------
# FILESYSTEM SCANNING
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# BEETS INTEGRATION
# ---------------------------------------------------------------------------

def run_beet(args):
    """Run a beets command and return output."""
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


def get_album_data(artist, album):
    """Get complete album data from beets."""
    # Get basic album info
    args = [
        "beet", "-c", BEETS_CONFIG,
        "list", "-a",
        f"albumartist:{artist}",
        f"album:{album}",
        "-f", "$albumartist\t$album\t$year\t$path\t$artpath"
    ]
    
    out = run_beet(args)
    lines = out.strip().splitlines()
    
    if not lines:
        return None
    
    parts = lines[0].split("\t")
    if len(parts) < 4:
        return None
    
    albumartist, album_name, year, path = parts[:4]
    artpath = parts[4] if len(parts) > 4 else ""
    
    # Get tracks
    tracks, total_length = get_tracks(artist, album)
    
    # Process cover art path
    cover = None
    if artpath and os.path.exists(artpath):
        cover = artpath.replace(str(LIB_ROOT), "")
    
    folder = path.replace(str(LIB_ROOT), "")
    
    return {
        "albumartist": albumartist,
        "album": album_name,
        "year": year,
        "folder": folder,
        "cover": cover,
        "track_count": len(tracks),
        "total_length": total_length,
        "tracks": tracks,
    }


def get_tracks(albumartist, album):
    """Get all tracks for an album."""
    fmt = "$disc\t$track\t$title\t$length\t$bitrate\t$format\t$path"
    args = [
        "beet", "-c", BEETS_CONFIG,
        "list", "-t",
        f"albumartist:{albumartist}",
        f"album:{album}",
        "-f", fmt
    ]
    
    out = run_beet(args)
    tracks = []
    total_length = 0
    
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        
        disc, track, title, length, bitrate, fmtc, path = (p.strip() for p in parts)
        
        try:
            disc = int(disc)
        except:
            disc = 1
        
        try:
            track = int(track)
        except:
            track = None
        
        try:
            length = int(float(length))
        except:
            length = None
        
        try:
            bitrate = int(bitrate)
        except:
            bitrate = None
        
        rel_path = path.replace(str(LIB_ROOT), "")
        
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
# ALBUM DATABASE MANAGEMENT
# ---------------------------------------------------------------------------

def load_albums():
    """Load existing albums.json."""
    if not os.path.exists(ALBUMS_FILE):
        return []
    try:
        with open(ALBUMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load albums: {e}")
        return []


def save_albums(albums):
    """Save albums.json."""
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(ALBUMS_FILE, "w", encoding="utf-8") as f:
            json.dump(albums, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(albums)} albums to {ALBUMS_FILE}")
    except Exception as e:
        logger.error(f"Failed to save albums: {e}")


def update_album_in_list(albums, new_album):
    """Update or add an album in the list."""
    # Find existing album by key
    key = (new_album["albumartist"], new_album["album"], new_album["year"])
    
    for i, album in enumerate(albums):
        existing_key = (album.get("albumartist"), album.get("album"), album.get("year"))
        if existing_key == key:
            albums[i] = new_album
            return albums
    
    # Not found, add it
    albums.append(new_album)
    return albums


# ---------------------------------------------------------------------------
# MAIN PROCESSING LOOP
# ---------------------------------------------------------------------------

def get_all_albums():
    """
    Scan the library for ALL album directories.
    Returns: list of (artist, album, folder_path, mtime)
    """
    if not LIB_ROOT.exists():
        logger.warning(f"Library path does not exist: {LIB_ROOT}")
        return []

    all_albums = []

    try:
        # Scan Artist/Album structure
        for artist_dir in LIB_ROOT.iterdir():
            if not artist_dir.is_dir() or artist_dir.name.startswith('.'):
                continue

            for album_dir in artist_dir.iterdir():
                if not album_dir.is_dir() or album_dir.name.startswith('.'):
                    continue

                try:
                    mtime = album_dir.stat().st_mtime
                    all_albums.append({
                        "artist": artist_dir.name,
                        "album": album_dir.name,
                        "path": str(album_dir),
                        "mtime": mtime
                    })
                except Exception as e:
                    logger.debug(f"Error checking {album_dir}: {e}")

    except Exception as e:
        logger.error(f"Error scanning library: {e}")

    return all_albums


def process_cycle():
    """Run one processing cycle."""
    logger.info("Starting regeneration cycle")
    
    # Load state
    state = load_state()
    now = time.time()
    
    # Get ALL albums in the library
    all_albums = get_all_albums()
    logger.info(f"Found {len(all_albums)} total albums in library")
    
    if not all_albums:
        logger.info("No albums found in library")
        return
    
    # Categorize albums into priority groups
    never_checked = []      # Never seen before - HIGHEST PRIORITY
    recently_modified = []  # Modified in last 7 days - HIGH PRIORITY
    needs_recheck = []      # Not checked in 7+ days - MEDIUM PRIORITY
    recently_checked = []   # Checked recently - SKIP
    
    cutoff_time = now - MODIFICATION_WINDOW
    
    for album_info in all_albums:
        key = f"{album_info['artist']}::{album_info['album']}"
        last_checked = state.get(key, 0)
        mtime = album_info['mtime']
        
        # Never checked before
        if last_checked == 0:
            never_checked.append(album_info)
        # Recently modified
        elif mtime > cutoff_time:
            # But skip if we just checked it recently
            if now - last_checked >= SKIP_RECENTLY_CHECKED:
                recently_modified.append(album_info)
            else:
                recently_checked.append(album_info)
        # Needs periodic recheck
        elif now - last_checked >= SKIP_RECENTLY_CHECKED:
            needs_recheck.append(album_info)
        # Recently checked and not modified
        else:
            recently_checked.append(album_info)
    
    logger.info(f"Album status: {len(never_checked)} never checked, "
                f"{len(recently_modified)} recently modified, "
                f"{len(needs_recheck)} need recheck, "
                f"{len(recently_checked)} recently checked")
    
    # Build priority processing queue
    # Process in order: never_checked ? recently_modified ? needs_recheck (limited)
    to_process = []
    to_process.extend(never_checked)
    to_process.extend(recently_modified)
    
    # Only process a few from needs_recheck each cycle to avoid overwhelming
    MAX_RECHECK_PER_CYCLE = 10
    if needs_recheck:
        # Sort by oldest check time first
        needs_recheck.sort(key=lambda x: state.get(f"{x['artist']}::{x['album']}", 0))
        to_process.extend(needs_recheck[:MAX_RECHECK_PER_CYCLE])
    
    if not to_process:
        logger.info("Nothing to process this cycle")
        return
    
    logger.info(f"Processing {len(to_process)} albums this cycle")
    
    # Load existing albums
    albums = load_albums()
    
    # Process each album
    processed = 0
    for album_info in to_process:
        artist = album_info['artist']
        album = album_info['album']
        key = f"{artist}::{album}"
        
        try:
            logger.info(f"Processing: {key}")
            album_data = get_album_data(artist, album)
            
            if album_data:
                albums = update_album_in_list(albums, album_data)
                state[key] = now
                processed += 1
                logger.info(f"Updated: {key}")
            else:
                logger.warning(f"No data found for: {key}")
                # Mark as checked even if no data, so we don't keep retrying
                state[key] = now
        
        except Exception as e:
            logger.error(f"Error processing {key}: {e}")
    
    # Save results
    if processed > 0:
        save_albums(albums)
        save_state(state)
        logger.info(f"Cycle complete: processed {processed} albums")
    else:
        # Still save state even if no albums updated (to track attempts)
        save_state(state)
        logger.info("Cycle complete: no albums updated")


def main_loop():
    """Run the processing loop indefinitely."""
    logger.info("Smart regeneration service started")
    logger.info(f"Check interval: {CHECK_INTERVAL}s ({CHECK_INTERVAL/3600:.1f} hours)")
    logger.info(f"Skip if checked within: {SKIP_RECENTLY_CHECKED/86400:.0f} days")
    logger.info(f"Modification detection window: {MODIFICATION_WINDOW/86400:.0f} days")
    logger.info("Processing priority: never-checked ? recently-modified ? periodic-recheck")
    
    # Create empty albums.json if it doesn't exist
    if not os.path.exists(ALBUMS_FILE):
        logger.info("Creating empty albums.json")
        save_albums([])
    
    while True:
        try:
            process_cycle()
        except Exception as e:
            logger.exception(f"Error in processing cycle: {e}")
        
        logger.info(f"Sleeping for {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main_loop()
