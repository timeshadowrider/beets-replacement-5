#!/usr/bin/env python3
"""
fetch_cover.py - Optimized for OMV/Docker
Strategies (in order of speed):
1. Local File Check (folder.jpg, front.png, etc.)
2. Embedded Art Extraction (via Mutagen)
3. Internet Fetch (Cover Art Archive via Beets ID)
"""

import os
import sys
import subprocess
import tempfile
import requests
from pathlib import Path
from mutagen import File as MutagenFile

# ---------------- CONFIGURATION ---------------- #
BEETS_CONFIG = "/config/config.yaml"
COVER_FILENAME = "cover.jpg"
USER_AGENT = "beets-replacement-cover-fetcher/1.0 (https://musicbrainz.org)"

# ---------------- STRATEGY 1: LOCAL FILES ---------------- #

def find_existing_image(album_dir: Path) -> bytes | None:
    """Fastest: Scans directory for common image names."""
    base_names = ["cover", "folder", "front", "album", "art"]
    base_exts = [".jpg", ".png", ".jpeg"]

    try:
        # Get all files once to minimize HDD head movement on OMV
        existing = {f.name.lower(): f for f in album_dir.iterdir() if f.is_file()}
        for base in base_names:
            for ext in base_exts:
                candidate = (base + ext).lower()
                if candidate in existing:
                    # If it's already named cover.jpg, we are done
                    if existing[candidate].name == COVER_FILENAME:
                        return None 
                    return existing[candidate].read_bytes()
    except Exception:
        pass
    return None

# ---------------- STRATEGY 2: EMBEDDED ART ---------------- #

def extract_embedded_art(album_dir: Path) -> bytes | None:
    """Second Fastest: Reads headers of the first few audio files."""
    exts = [".flac", ".mp3", ".m4a", ".ogg", ".opus"]
    try:
        # Only check the first 3 files to keep performance high
        audio_files = [f for f in album_dir.iterdir() if f.suffix.lower() in exts][:3]
        for fpath in audio_files:
            audio = MutagenFile(str(fpath))
            if not audio: continue
            
            # Check FLAC/Vorbis pictures
            if hasattr(audio, 'pictures') and audio.pictures:
                return audio.pictures[0].data
            
            # Check ID3 tags (MP3/AIFF)
            if audio.tags:
                for tag in audio.tags.values():
                    # Look for APIC frames (Attached Picture)
                    if hasattr(tag, 'data') and ('APIC' in str(type(tag)) or 'PICTURE' in str(type(tag))):
                        return tag.data
    except Exception:
        pass
    return None

# ---------------- STRATEGY 3: INTERNET (CAA) ---------------- #

def get_mbid_from_beet(album_dir: Path):
    """Slowest: Queries Beets SQLite DB for MusicBrainz ID."""
    # Using path: search is indexed and fast
    args = ["beet", "-c", BEETS_CONFIG, "list", "-a", "-f", "$mb_albumid", f"path:{album_dir}"]
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=20)
        mbid = res.stdout.strip()
        return mbid if mbid else None
    except:
        return None

def fetch_from_caa(mbid: str) -> bytes | None:
    """Fetches art from Cover Art Archive."""
    if not mbid: return None
    url = f"https://coverartarchive.org/release/{mbid}/front-500"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        if resp.status_code == 200:
            return resp.content
    except:
        pass
    return None

# ---------------- HELPERS ---------------- #

def write_atomic(target: Path, data: bytes):
    """Prevents corrupted JPEGs if the container restarts."""
    if not data: return
    tmp = target.with_suffix(f".tmp_{os.getpid()}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
        # Set permissions for webserver access
        os.chmod(target, 0o664) 
    except Exception as e:
        if tmp.exists(): tmp.unlink()
        print(f"Error writing file: {e}")

# ---------------- MAIN EXECUTION ---------------- #

def main():
    if len(sys.argv) < 2:
        print("Usage: fetch_cover.py /path/to/album_dir")
        sys.exit(1)

    album_dir = Path(sys.argv[1]).resolve()
    cover_path = album_dir / COVER_FILENAME

    # 0. Skip if valid cover exists
    if cover_path.exists() and cover_path.stat().st_size > 500:
        print(f"EXISTS: {album_dir.name}")
        sys.exit(0)

    print(f"PROCESSING: {album_dir.name}")

    # 1. Check for Local Files (Instantly fixes existing folder.jpg)
    data = find_existing_image(album_dir)
    if data:
        write_atomic(cover_path, data)
        print(f"SUCCESS (Local Image): {album_dir.name}")
        sys.exit(0)

    # 2. Extract from Tags (Handles files with embedded art but no JPG)
    data = extract_embedded_art(album_dir)
    if data:
        write_atomic(cover_path, data)
        print(f"SUCCESS (Embedded): {album_dir.name}")
        sys.exit(0)

    # 3. Internet Fallback (For newly imported music with no local art)
    mbid = get_mbid_from_beet(album_dir)
    if mbid:
        data = fetch_from_caa(mbid)
        if data:
            write_atomic(cover_path, data)
            print(f"SUCCESS (Internet): {album_dir.name}")
            sys.exit(0)

    print(f"FAILED: No art found for {album_dir.name}")
    sys.exit(1)

if __name__ == "__main__":
    main()