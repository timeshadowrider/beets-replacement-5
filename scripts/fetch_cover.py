#!/usr/bin/env python3
"""
fetch_cover.py

Fetch cover art for a given album directory.

Strategy:
1. Use beets to get albumartist, album, and MusicBrainz album ID (mb_albumid).
2. If mb_albumid is available, fetch from Cover Art Archive.
3. If that fails, try to extract embedded art from audio files (mutagen).
4. If that fails, look for existing image files (folder.jpg, front.jpg, cover.png, etc.).
5. Write cover.jpg atomically into the album directory.
"""

import os
import sys
import subprocess
import tempfile
from pathlib import Path

import requests
from mutagen import File as MutagenFile

BEETS_CONFIG = "/config/config.yaml"
COVER_FILENAME = "cover.jpg"
USER_AGENT = "beets-replacement-cover-fetcher/1.0 (https://musicbrainz.org)"


def run_beet_info(album_dir: Path):
    """
    Use beets to get album metadata and MusicBrainz album ID for the given path.
    """
    args = [
        "beet",
        "-c",
        BEETS_CONFIG,
        "list",
        "-a",
        "-f",
        "$albumartist\t$album\t$mb_albumid",
        f'path:"{str(album_dir)}"',
    ]
    try:
        p = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        out = p.stdout or ""
        if p.stderr:
            print(f"[fetch_cover] beets stderr: {p.stderr.strip()}", file=sys.stderr)
    except Exception as e:
        print(f"beet invocation failed: {e}", file=sys.stderr)
        return None

    for line in out.splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 3:
            continue
        albumartist, album, mb_albumid = (x.strip() for x in parts[:3])
        if not albumartist or not album:
            continue
        return {
            "albumartist": albumartist,
            "album": album,
            "mb_albumid": mb_albumid or None,
        }

    return None


def fetch_from_cover_art_archive(mb_albumid: str) -> bytes | None:
    """
    Fetch front cover from Cover Art Archive using MusicBrainz release ID.
    """
    if not mb_albumid:
        return None

    url = f"https://coverartarchive.org/release/{mb_albumid}/front-500"
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200 and resp.content:
            return resp.content
        # Try default front if specific size fails
        url2 = f"https://coverartarchive.org/release/{mb_albumid}/front"
        resp2 = requests.get(url2, headers=headers, timeout=15)
        if resp2.status_code == 200 and resp2.content:
            return resp2.content
    except Exception as e:
        print(f"Cover Art Archive fetch failed: {e}", file=sys.stderr)
    return None


def extract_embedded_art(album_dir: Path) -> bytes | None:
    """
    Try to extract embedded cover art from the first audio file with artwork.
    """
    exts = [".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aac", ".wav"]

    def _extract_from_file(fpath: Path) -> bytes | None:
        try:
            audio = MutagenFile(str(fpath))
            if not audio:
                return None

            # FLAC / Vorbis / others with pictures
            pics = getattr(audio, "pictures", None)
            if pics:
                for pic in pics:
                    if getattr(pic, "data", None):
                        return pic.data

            # ID3 (MP3) APIC frames
            if hasattr(audio, "tags") and audio.tags:
                for key in audio.tags.keys():
                    if key.startswith("APIC"):
                        apic = audio.tags[key]
                        data = getattr(apic, "data", None)
                        if data:
                            return data
        except Exception:
            pass
        return None

    try:
        # Scan top-level directory first for predictable results,
        # then fall back to subdirectories (e.g. disc folders).
        top_level_files = [f for f in album_dir.iterdir() if f.is_file() and f.suffix.lower() in exts]
        for fpath in sorted(top_level_files):
            data = _extract_from_file(fpath)
            if data:
                return data

        for root, dirs, files in os.walk(album_dir):
            # Skip the top-level dir since we already scanned it
            if Path(root) == album_dir:
                continue
            for name in sorted(files):
                if Path(name).suffix.lower() not in exts:
                    continue
                data = _extract_from_file(Path(root) / name)
                if data:
                    return data
    except Exception as e:
        print(f"Embedded art extraction failed: {e}", file=sys.stderr)
    return None


def find_existing_image(album_dir: Path) -> bytes | None:
    """
    Look for existing image files in the album directory and return the first one found.
    """
    # Base names to look for, checked case-insensitively.
    # Priority order is preserved: cover > folder > front, jpg > png.
    base_names = ["cover", "folder", "front"]
    base_exts = [".jpg", ".png"]

    # Build a map of lowercased filename -> actual Path for all files in the directory
    existing = {f.name.lower(): f for f in album_dir.iterdir() if f.is_file()}

    for base in base_names:
        for ext in base_exts:
            candidate = (base + ext).lower()
            if candidate in existing:
                try:
                    with open(existing[candidate], "rb") as f:
                        return f.read()
                except Exception:
                    continue
    return None


def write_atomic(target: Path, data: bytes):
    """
    Write data to target atomically.
    """
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), prefix=".cover_tmp_", suffix=".jpg")
    try:
        with os.fdopen(tmp_fd, "wb") as tmp_f:
            tmp_f.write(data)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def main():
    if len(sys.argv) < 2:
        print("Usage: fetch_cover.py /path/to/album_dir", file=sys.stderr)
        sys.exit(1)

    album_dir = Path(sys.argv[1]).resolve()
    if not album_dir.exists() or not album_dir.is_dir():
        print(f"Album dir not found: {album_dir}", file=sys.stderr)
        sys.exit(1)

    cover_path = album_dir / COVER_FILENAME
    if cover_path.exists():
        print(f"Cover already exists: {cover_path}")
        sys.exit(0)

    print(f"[fetch_cover] Processing album dir: {album_dir}")

    meta = run_beet_info(album_dir)
    if meta:
        print(f"[fetch_cover] Album: {meta['albumartist']} - {meta['album']}")
        mbid = meta.get("mb_albumid")
    else:
        print("[fetch_cover] No beets metadata found; proceeding without mb_albumid")
        mbid = None

    # 1) Try Cover Art Archive
    if mbid:
        data = fetch_from_cover_art_archive(mbid)
        if data:
            try:
                write_atomic(cover_path, data)
                print(f"[fetch_cover] Wrote cover from Cover Art Archive: {cover_path}")
                sys.exit(0)
            except Exception as e:
                print(f"[fetch_cover] Failed to write cover from CAA: {e}", file=sys.stderr)

    # 2) Try embedded art
    data = extract_embedded_art(album_dir)
    if data:
        try:
            write_atomic(cover_path, data)
            print(f"[fetch_cover] Wrote cover from embedded art: {cover_path}")
            sys.exit(0)
        except Exception as e:
            print(f"[fetch_cover] Failed to write cover from embedded art: {e}", file=sys.stderr)

    # 3) Try existing image files
    data = find_existing_image(album_dir)
    if data:
        try:
            write_atomic(cover_path, data)
            print(f"[fetch_cover] Wrote cover from existing image: {cover_path}")
            sys.exit(0)
        except Exception as e:
            print(f"[fetch_cover] Failed to write cover from existing image: {e}", file=sys.stderr)

    print("[fetch_cover] No cover art found", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()