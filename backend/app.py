from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import subprocess
import json
import os
import shlex
import re
import logging
import time
import threading

from pathlib import Path
from datetime import datetime
from queue import Queue, Empty

import humanize
from mutagen import File as MutagenFile

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ---------------------------------------------------------------------------
# APP SETUP
# ---------------------------------------------------------------------------

app = FastAPI(title="Beets Replacement API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="/app/static"), name="static")

# ---------------------------------------------------------------------------
# CONFIG / PATHS
# ---------------------------------------------------------------------------

ALBUMS_FILE = "/data/albums.json"
RECENT_FILE = "/data/recent_albums.json"
REGEN_SCRIPT = "/app/scripts/regenerate_albums.py"
BEETS_CONFIG = "/config/config.yaml"
INDEX_HTML = "/app/static/index.html"

INBOX_PATH = Path("/music/inbox")
LIBRARY_PATH = Path("/music/library")

INBOX_STATS_CACHE_SECONDS = 60
DEBOUNCE_INBOX = 60.0
DEBOUNCE_LIBRARY = 30.0
DEBOUNCE_COVER = 30.0

IMPORT_TIMEOUT = 3600
REGEN_TIMEOUT = 900

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("beets-replacement")

# ---------------------------------------------------------------------------
# GLOBAL STATE
# ---------------------------------------------------------------------------

stop_event = threading.Event()

inbox_stats_cache = None
inbox_stats_cache_time = None

# Queues
inbox_q = Queue()
lib_q = Queue()
cover_q = Queue()

# Locks
inbox_lock = threading.Lock()
lib_lock = threading.Lock()
cover_lock = threading.Lock()

# Dedup sets
inbox_queued = set()
lib_queued = set()
cover_queued = set()

# Threads
inbox_thread = None
lib_thread = None
cover_thread = None
cleanup_thread = None

# Observers
inbox_observer = None
lib_observer = None
cover_observer = None

# ---------------------------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------------------------

def run_cmd_list(cmd, timeout=300):
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        return p.returncode == 0, p.stdout or ""
    except Exception as e:
        return False, str(e)

def invalidate_inbox_cache(*_a, **_k):
    global inbox_stats_cache, inbox_stats_cache_time
    inbox_stats_cache = None
    inbox_stats_cache_time = None

# ---------------------------------------------------------------------------
# INBOX CLEANUP (delete dirs with NO audio files)
# ---------------------------------------------------------------------------

def cleanup_inbox_empty_dirs():
    AUDIO_EXTS = (".flac", ".mp3", ".wav", ".aac", ".m4a", ".ogg")

    for root, dirs, files in os.walk(INBOX_PATH, topdown=False):
        root_path = Path(root)

        if root_path == INBOX_PATH:
            continue

        root_str = str(root_path).lower()
        if "unpack" in root_str:
            continue

        has_audio = any(f.lower().endswith(AUDIO_EXTS) for f in files)

        if not has_audio:
            try:
                logger.info("[CLEANUP] Removing inbox dir with no audio: %s", root_path)
                os.rmdir(root_path)
            except Exception as e:
                logger.error("[CLEANUP] Failed to remove %s: %s", root_path, e)

def inbox_cleanup_scheduler():
    while not stop_event.is_set():
        try:
            cleanup_inbox_empty_dirs()
        except Exception:
            logger.exception("Inbox cleanup scheduler error")
        time.sleep(1800)  # 30 minutes

# ---------------------------------------------------------------------------
# STATIC FILE ROUTES
# ---------------------------------------------------------------------------

@app.get("/music/library/{full_path:path}", include_in_schema=False)
@app.head("/music/library/{full_path:path}", include_in_schema=False)
def serve_library_file(full_path: str):
    base = Path("/music/library")
    requested = (base / full_path).resolve()
    try:
        requested.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if requested.exists() and requested.is_file():
        return FileResponse(str(requested))
    raise HTTPException(status_code=404, detail="Not found")

@app.get("/placeholder.jpg", include_in_schema=False)
@app.head("/placeholder.jpg", include_in_schema=False)
def serve_placeholder():
    p = "/app/static/placeholder.jpg"
    if os.path.exists(p):
        return FileResponse(p, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="Not found")

# ---------------------------------------------------------------------------
# UI + JSON
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def serve_index():
    if os.path.exists(INDEX_HTML):
        return FileResponse(INDEX_HTML, media_type="text/html")
    return HTMLResponse("<h1>Index not found</h1>", status_code=404)

@app.get("/data/albums.json")
def serve_albums_json():
    if os.path.exists(ALBUMS_FILE):
        return FileResponse(ALBUMS_FILE, media_type="application/json")
    return JSONResponse({"detail": "Not Found"}, status_code=404)

# ---------------------------------------------------------------------------
# API ENDPOINTS
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def stats():
    ok, out = run_cmd_list(["beet", "-c", BEETS_CONFIG, "stats"])
    if ok and out:
        tracks = albums = album_artists = 0
        total_time = total_size = "unknown"

        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Tracks:"):
                tracks = int(line.split(":", 1)[1].strip() or 0)
            elif line.startswith("Albums:"):
                albums = int(line.split(":", 1)[1].strip() or 0)
            elif line.startswith("Album artists:"):
                album_artists = int(line.split(":", 1)[1].strip() or 0)
            elif line.startswith("Total time:"):
                total_time = line.split(":", 1)[1].strip()
            elif line.startswith("Approximate total size:"):
                total_size = line.split(":", 1)[1].strip()

        return {
            "tracks": tracks,
            "albums": albums,
            "album_artists": album_artists,
            "total_time": total_time,
            "total_size": total_size,
        }

    try:
        with open(ALBUMS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "tracks": 0,
            "albums": len(data),
            "album_artists": len({a.get("albumartist") for a in data}),
            "total_time": "unknown",
            "total_size": "unknown",
        }
    except Exception:
        return {
            "tracks": 0,
            "albums": 0,
            "album_artists": 0,
            "total_time": "unknown",
            "total_size": "unknown",
        }

@app.post("/api/library/refresh")
def refresh_library():
    ok, out = run_cmd_list(["python3", REGEN_SCRIPT], timeout=120)
    if not ok:
        raise HTTPException(status_code=500, detail=out)
    return {"status": "ok", "detail": out.strip()}

@app.post("/api/library/import")
def import_library(background_tasks: BackgroundTasks):
    args = ["beet", "-c", BEETS_CONFIG, "import", "-A", str(INBOX_PATH)]

    def run():
        logger.info("Manual import started")
        subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=IMPORT_TIMEOUT)

    background_tasks.add_task(run)
    return {"status": "started", "cmd": args}

@app.get("/api/albums")
def albums(limit: int = 5000):
    try:
        with open(ALBUMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)[:limit]
    except Exception:
        return []

@app.get("/api/albums/recent")
def recent(limit: int = 12):
    try:
        with open(RECENT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)[:limit]
    except Exception:
        return []

# ---------------------------------------------------------------------------
# INBOX API
# ---------------------------------------------------------------------------

@app.get("/api/inbox/stats")
def inbox_stats():
    global inbox_stats_cache, inbox_stats_cache_time
    now = datetime.now()
    if inbox_stats_cache and inbox_stats_cache_time:
        if (now - inbox_stats_cache_time).total_seconds() < INBOX_STATS_CACHE_SECONDS:
            return inbox_stats_cache

    inbox_stats_cache = compute_inbox_stats_fast()
    inbox_stats_cache_time = now
    return inbox_stats_cache

def compute_inbox_stats_fast():
    AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".aac"}
    tracks = total_bytes = total_seconds = 0
    artists = set()
    albums = set()

    if not INBOX_PATH.exists():
        return {
            "tracks": 0,
            "total_time": "0 seconds",
            "total_size": "0 B",
            "artists": 0,
            "albums": 0,
            "album_artists": 0,
        }

    for artist_dir in INBOX_PATH.iterdir():
        if not artist_dir.is_dir() or artist_dir.name.startswith("."):
            continue
        artists.add(artist_dir.name)

        for album_dir in artist_dir.iterdir():
            if not album_dir.is_dir():
                continue
            albums.add(f"{artist_dir.name}::{album_dir.name}")

            for f in album_dir.iterdir():
                if f.suffix.lower() not in AUDIO_EXTS:
                    continue
                tracks += 1
                total_bytes += f.stat().st_size
                try:
                    audio = MutagenFile(str(f))
                    if audio and audio.info:
                        total_seconds += audio.info.length or 0
                except Exception:
                    pass

    return {
        "tracks": tracks,
        "total_time": humanize.precisedelta(int(total_seconds)),
        "total_size": humanize.naturalsize(total_bytes),
        "artists": len(artists),
        "albums": len(albums),
        "album_artists": len(artists),
    }

@app.get("/api/inbox/tree")
def inbox_tree():
    if not INBOX_PATH.exists():
        return {"folders": {}}
    return {
        "folders": {
            d.name: [x.name for x in d.iterdir() if x.is_dir()]
            for d in INBOX_PATH.iterdir()
            if d.is_dir()
        }
    }

@app.get("/api/inbox/tree/")
def inbox_tree_slash():
    return inbox_tree()

@app.get("/api/inbox/folder")
def inbox_folder(artist: str, album: str):
    folder = INBOX_PATH / artist / album
    if not folder.exists() or not folder.is_dir():
        return {"files": []}
    return {"files": [f.name for f in folder.iterdir() if f.is_file()]}

# ---------------------------------------------------------------------------
# FILE WATCHER HANDLER (DEBOUNCED)
# ---------------------------------------------------------------------------

class DebouncedHandler(FileSystemEventHandler):
    def __init__(self, queue, lock, queued, debounce, label, root=None, ignore_dirs=None):
        self.queue = queue
        self.lock = lock
        self.queued = queued
        self.debounce = debounce
        self.label = label
        self.root = Path(root).resolve() if root else None
        self.ignore_dirs = ignore_dirs or []
        self.last_seen = {}

    def on_any_event(self, event):
        try:
            path = event.src_path
            target = path if os.path.isdir(path) else os.path.dirname(path)
            target = os.path.normpath(target)

            if self.root:
                try:
                    Path(target).resolve().relative_to(self.root)
                except ValueError:
                    return

            # Check if any part of the path contains an ignored prefix
            if self.ignore_dirs:
                path_parts = Path(target).parts
                for part in path_parts:
                    if any(part.startswith(x) for x in self.ignore_dirs):
                        return

            base = os.path.basename(target)

            if base.startswith(".") or base.startswith("~"):
                return

            now = time.time()
            with self.lock:
                if now - self.last_seen.get(target, 0) < self.debounce:
                    return
                self.last_seen[target] = now
                if target in self.queued:
                    return
                self.queued.add(target)

            logger.info("%s enqueue: %s", self.label, target)
            self.queue.put(target)

        except Exception:
            logger.exception("%s handler error", self.label)

# ---------------------------------------------------------------------------
# WORKERS
# ---------------------------------------------------------------------------

def inbox_worker():
    logger.info("Inbox worker started")
    while not stop_event.is_set():
        try:
            target = inbox_q.get(timeout=1)
        except Empty:
            continue

        with inbox_lock:
            inbox_queued.discard(target)

        try:
            time.sleep(DEBOUNCE_INBOX)

            if not os.path.isdir(target):
                continue

            args = ["beet", "-c", BEETS_CONFIG, "import", "-A", target]
            logger.info("Importing inbox: %s", target)
            subprocess.run(args, timeout=IMPORT_TIMEOUT)

            invalidate_inbox_cache()

        except Exception:
            logger.exception("Inbox import failed for %s", target)
        finally:
            inbox_q.task_done()

    logger.info("Inbox worker stopped")

def library_worker():
    logger.info("Library worker started")
    while not stop_event.is_set():
        try:
            target = lib_q.get(timeout=1)
        except Empty:
            continue

        with lib_lock:
            lib_queued.discard(target)

        try:
            time.sleep(DEBOUNCE_LIBRARY)

            success = False

            # First attempt: exact target
            ok, _ = run_cmd_list(
                ["python3", REGEN_SCRIPT, target],
                timeout=REGEN_TIMEOUT
            )
            success = success or ok

            # Second attempt: strip [n] suffix
            if not success:
                t2 = re.sub(r"\s*\[\d+\]$", "", target)
                if t2 != target and os.path.exists(t2):
                    ok, _ = run_cmd_list(
                        ["python3", REGEN_SCRIPT, t2],
                        timeout=REGEN_TIMEOUT
                    )
                    success = success or ok

            # Final fallback: full regen
            if not success:
                ok, _ = run_cmd_list(
                    ["python3", REGEN_SCRIPT],
                    timeout=REGEN_TIMEOUT * 2
                )
                success = success or ok

            if success:
                run_cmd_list(
                    ["python3", "/app/scripts/recompute_recent.py"],
                    timeout=120
                )

        except Exception:
            logger.exception("Library processing failed for %s", target)
        finally:
            lib_q.task_done()

    logger.info("Library worker stopped")

def cover_worker():
    logger.info("Cover worker started")
    while not stop_event.is_set():
        try:
            target = cover_q.get(timeout=1)
        except Empty:
            continue

        with cover_lock:
            cover_queued.discard(target)

        try:
            album_dir = Path(target)
            cover = album_dir / "cover.jpg"

            if not album_dir.exists() or cover.exists():
                continue

            logger.info("Fetching cover for %s", album_dir)
            ok, _ = run_cmd_list(
                ["python3", "/app/scripts/fetch_cover.py", str(album_dir)],
                timeout=300,
            )

            if ok:
                run_cmd_list(
                    ["python3", REGEN_SCRIPT, str(album_dir)],
                    timeout=REGEN_TIMEOUT
                )
                run_cmd_list(
                    ["python3", "/app/scripts/recompute_recent.py"],
                    timeout=120
                )

        except Exception:
            logger.exception("Cover fetch failed for %s", target)
        finally:
            cover_q.task_done()

    logger.info("Cover worker stopped")

# ---------------------------------------------------------------------------
# FASTAPI LIFESPAN (STARTUP / SHUTDOWN)
# ---------------------------------------------------------------------------

@app.on_event("startup")
def startup():
    global inbox_thread, lib_thread, cover_thread, cleanup_thread
    global inbox_observer, lib_observer, cover_observer

    stop_event.clear()

    # Start workers
    inbox_thread = threading.Thread(target=inbox_worker, daemon=True)
    lib_thread = threading.Thread(target=library_worker, daemon=True)
    cover_thread = threading.Thread(target=cover_worker, daemon=True)
    cleanup_thread = threading.Thread(target=inbox_cleanup_scheduler, daemon=True)

    inbox_thread.start()
    lib_thread.start()
    cover_thread.start()
    cleanup_thread.start()

    # Start watchers
    inbox_handler = DebouncedHandler(
        inbox_q,
        inbox_lock,
        inbox_queued,
        DEBOUNCE_INBOX,
        "INBOX",
        root=INBOX_PATH,
        ignore_dirs=["_UNPACK_", "UNPACK", "unpack"]
    )
    
    # Wrap the handler to invalidate inbox cache on any event
    original_on_any = inbox_handler.on_any_event
    def on_any_with_cache_invalidation(event):
        invalidate_inbox_cache()
        original_on_any(event)
    inbox_handler.on_any_event = on_any_with_cache_invalidation
    
    inbox_observer = Observer()
    inbox_observer.schedule(inbox_handler, str(INBOX_PATH), recursive=True)

    lib_observer = Observer()
    lib_observer.schedule(
        DebouncedHandler(
            lib_q,
            lib_lock,
            lib_queued,
            DEBOUNCE_LIBRARY,
            "LIBRARY",
            root=LIBRARY_PATH
        ),
        str(LIBRARY_PATH),
        recursive=True,
    )

    cover_observer = Observer()
    cover_observer.schedule(
        DebouncedHandler(
            cover_q,
            cover_lock,
            cover_queued,
            DEBOUNCE_COVER,
            "COVER",
            root=LIBRARY_PATH
        ),
        str(LIBRARY_PATH),
        recursive=True,
    )

    inbox_observer.start()
    lib_observer.start()
    cover_observer.start()

    logger.info("Startup complete: workers + watchers + cleanup scheduler running.")

@app.on_event("shutdown")
def shutdown():
    stop_event.set()

    for obs in (inbox_observer, lib_observer, cover_observer):
        if obs:
            obs.stop()
            obs.join()

    logger.info("Shutdown complete.")
