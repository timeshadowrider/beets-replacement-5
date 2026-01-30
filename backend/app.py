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
import mutagen
import humanize
from queue import Queue, Empty
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Application setup
app = FastAPI(title="Beets Replacement API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# STATIC MOUNT (ADDED)
# ---------------------------------------------------------------------------

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

inbox_stats_cache = None
inbox_stats_cache_time = None
INBOX_STATS_CACHE_SECONDS = 60

# Tunables
DEBOUNCE_INBOX = 20.0
DEBOUNCE_LIBRARY = 10.0
IMPORT_TIMEOUT = 3600
REGEN_TIMEOUT = 900

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("beets-replacement")

# ---------------------------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------------------------

def run_cmd(cmd, timeout=300):
    try:
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=timeout)
        return True, out.decode(errors="replace")
    except subprocess.CalledProcessError as e:
        return False, e.output.decode(errors="replace")
    except Exception as e:
        return False, str(e)

def run_cmd_list(cmd_list, timeout=300):
    try:
        p = subprocess.run(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        return p.returncode == 0, p.stdout or ""
    except Exception as e:
        return False, str(e)

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
    if os.path.exists(p) and os.path.isfile(p):
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
    ok, out = run_cmd(f"beet -c {shlex.quote(BEETS_CONFIG)} stats")

    if ok and out:
        tracks = 0
        albums = 0
        album_artists = 0
        total_time = "0 seconds"
        total_size = "0 B"

        for line in out.splitlines():
            line = line.strip()

            if line.startswith("Tracks:"):
                try:
                    tracks = int(line.split(":", 1)[1].strip())
                except:
                    pass

            elif line.startswith("Albums:"):
                try:
                    albums = int(line.split(":", 1)[1].strip())
                except:
                    pass

            elif line.startswith("Album artists:"):
                try:
                    album_artists = int(line.split(":", 1)[1].strip())
                except:
                    pass

            elif line.startswith("Total time:"):
                total_time = line.split(":", 1)[1].strip()

            elif line.startswith("Approximate total size:"):
                total_size = line.split(":", 1)[1].strip()

        return {
            "tracks": tracks,
            "albums": albums,
            "album_artists": album_artists,
            "total_time": total_time,
            "total_size": total_size
        }

    try:
        with open(ALBUMS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "tracks": 0,
            "albums": len(data),
            "album_artists": len({a.get("albumartist") for a in data}),
            "total_time": "unknown",
            "total_size": "unknown"
        }
    except:
        return {
            "tracks": 0,
            "albums": 0,
            "album_artists": 0,
            "total_time": "unknown",
            "total_size": "unknown"
        }

@app.post("/api/library/refresh")
def refresh_library():
    if not os.path.exists(REGEN_SCRIPT):
        raise HTTPException(status_code=500, detail="regenerate script missing")
    ok, out = run_cmd(f"python3 {shlex.quote(REGEN_SCRIPT)}", timeout=120)
    if not ok:
        raise HTTPException(status_code=500, detail=out)
    return {"status": "ok", "detail": out.strip()}

@app.post("/api/library/import")
def import_library(background_tasks: BackgroundTasks):
    cmd = f"beet -c {shlex.quote(BEETS_CONFIG)} import -A /music/inbox"
    try:
        def run():
            subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        background_tasks.add_task(run)
        return {"status": "started", "cmd": cmd}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/albums")
def albums(limit: int = 5000):
    try:
        with open(ALBUMS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data[:limit]
    except Exception:
        return []

@app.get("/api/albums/recent")
def recent(limit: int = 12):
    try:
        with open(RECENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data[:limit]
    except Exception:
        return []

# ---------------------------------------------------------------------------
# INBOX API ENDPOINTS
# ---------------------------------------------------------------------------

@app.get("/api/inbox/stats")
def inbox_stats():
    global inbox_stats_cache, inbox_stats_cache_time
    
    now = datetime.now()
    if (inbox_stats_cache is not None and 
        inbox_stats_cache_time is not None and 
        (now - inbox_stats_cache_time).total_seconds() < INBOX_STATS_CACHE_SECONDS):
        return inbox_stats_cache
    
    stats = compute_inbox_stats_fast()
    inbox_stats_cache = stats
    inbox_stats_cache_time = now
    
    return stats

def compute_inbox_stats_fast():
    if not INBOX_PATH.exists():
        return {
            "tracks": 0,
            "total_time": "0 seconds",
            "total_size": "0 B",
            "artists": 0,
            "albums": 0,
            "album_artists": 0,
        }
    
    tracks = 0
    total_bytes = 0
    artists = set()
    albums = set()
    
    try:
        for artist_dir in INBOX_PATH.iterdir():
            if not artist_dir.is_dir() or artist_dir.name.startswith('.'):
                continue
            artists.add(artist_dir.name)
            
            for album_dir in artist_dir.iterdir():
                if not album_dir.is_dir() or album_dir.name.startswith('.'):
                    continue
                albums.add(f"{artist_dir.name}::{album_dir.name}")
                
                for file in album_dir.iterdir():
                    if not file.is_file():
                        continue
                    if file.suffix.lower() in [".mp3", ".flac", ".m4a", ".ogg", ".wav", ".aac"]:
                        tracks += 1
                        try:
                            total_bytes += file.stat().st_size
                        except Exception:
                            pass
    except Exception as e:
        logger.error(f"Error computing inbox stats: {e}")
    
    estimated_minutes = total_bytes / (200 * 1024) if total_bytes > 0 else 0
    estimated_seconds = int(estimated_minutes * 60)
    
    return {
        "tracks": tracks,
        "total_time": humanize.precisedelta(estimated_seconds) if estimated_seconds > 0 else "0 seconds",
        "total_size": humanize.naturalsize(total_bytes),
        "artists": len(artists),
        "albums": len(albums),
        "album_artists": len(artists),
    }

@app.get("/api/inbox/tree")
def inbox_tree():
    if not INBOX_PATH.exists():
        return {"folders": {}}
    tree = {}
    for artist_dir in INBOX_PATH.iterdir():
        if not artist_dir.is_dir():
            continue
        tree[artist_dir.name] = [d.name for d in artist_dir.iterdir() if d.is_dir()]
    return {"folders": tree}

@app.get("/api/inbox/folder")
def inbox_folder(artist: str, album: str):
    folder = INBOX_PATH / artist / album
    if not folder.exists():
        return {"files": []}
    files = [f.name for f in folder.iterdir() if f.is_file()]
    return {"files": files}

# ---------------------------------------------------------------------------
# WATCHERS / WORKERS
# ---------------------------------------------------------------------------

inbox_q = Queue()
lib_q = Queue()
inbox_last = {}
lib_last = {}
inbox_lock = threading.Lock()
lib_lock = threading.Lock()
stop_event = threading.Event()

inbox_observer = None
lib_observer = None
inbox_worker_thread = None
lib_worker_thread = None

inbox_queued = set()
lib_queued = set()

class InboxHandler(FileSystemEventHandler):
    def on_any_event(self, event):
        try:
            src = event.src_path
            target = src if os.path.isdir(src) else os.path.dirname(src)
            target = os.path.normpath(target)
            base = os.path.basename(target)
            if not target or base.startswith(".") or base.startswith("~"):
                return
            now = time.time()
            with inbox_lock:
                last = inbox_last.get(target, 0)
                if now - last < DEBOUNCE_INBOX:
                    return
                inbox_last[target] = now
                if target in inbox_queued:
                    logger.debug("Inbox already queued: %s", target)
                    return
                inbox_queued.add(target)
            logger.info("Inbox enqueue: %s", target)
            inbox_q.put(target)
        except Exception:
            logger.exception("Inbox handler error")

class LibraryHandler(FileSystemEventHandler):
    def on_any_event(self, event):
        try:
            src = event.src_path
            target = src if os.path.isdir(src) else os.path.dirname(src)
            target = os.path.normpath(target)
            if not str(target).startswith(str(LIBRARY_PATH)):
                return
            base = os.path.basename(target)
            if base.startswith(".") or base.startswith("~"):
                return
            now = time.time()
            with lib_lock:
                last = lib_last.get(target, 0)
                if now - last < DEBOUNCE_LIBRARY:
                    return
                lib_last[target] = now
                if target in lib_queued:
                    logger.debug("Library already queued: %s", target)
                    return
                lib_queued.add(target)
            logger.info("Library enqueue: %s", target)
            lib_q.put(target)
        except Exception:
            logger.exception("Library handler error")

def inbox_worker(beets_config_path: str):
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
            if not os.path.exists(target):
                logger.info("Inbox target disappeared: %s", target)
                inbox_q.task_done()
                continue
            args = ["beet", "-c", beets_config_path, "import", "-A", target]
            logger.info("Running import: %s", " ".join(shlex.quote(a) for a in args))
            res = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=IMPORT_TIMEOUT, text=True)
            out = res.stdout or ""
            if res.returncode == 0:
                logger.info("Import succeeded for %s", target)
            else:
                logger.error("Import failed for %s rc=%s out=%s", target, res.returncode, (out[:400] if out else ""))
        except Exception:
            logger.exception("Exception during inbox import for %s", target)
        finally:
            inbox_q.task_done()
    logger.info("Inbox worker stopping")

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
            if not os.path.exists(target):
                logger.info("Library target missing: %s", target)
                lib_q.task_done()
                continue

            success = False
            tried = []

            if os.path.exists(REGEN_SCRIPT):
                # 1) Try exact path
                cmd1 = ["python3", REGEN_SCRIPT, target]
                tried.append(("exact", cmd1))
                ok, out = run_cmd_list(cmd1, timeout=REGEN_TIMEOUT)
                if ok:
                    success = True
                else:
                    # 2) Strip trailing bracketed suffixes - REGEX FIXED HERE
                    t2 = re.sub(r'\s\[\d+\]$', '', target)
                    if t2 != target and os.path.exists(t2):
                        cmd2 = ["python3", REGEN_SCRIPT, t2]
                        tried.append(("stripbrackets", cmd2))
                        ok, out = run_cmd_list(cmd2, timeout=REGEN_TIMEOUT)
                        if ok:
                            success = True

                # 3) Fallback: Run full regen
                if not success:
                    cmd3 = ["python3", REGEN_SCRIPT]
                    tried.append(("full", cmd3))
                    ok, out = run_cmd_list(cmd3, timeout=REGEN_TIMEOUT * 2)
                    if ok:
                        success = True

                if success:
                    logger.info("Regen succeeded for %s", target)
                    # Trigger recompute of recent lists
                    try:
                        ok, out = run_cmd_list(["python3", "/app/scripts/recompute_recent.py"], timeout=120)
                        if ok:
                            logger.info("Recompute recent succeeded for %s", target)
                        else:
                            logger.error("Recompute recent failed for %s: %s", target, (out or "")[:400])
                    except Exception:
                        logger.exception("Exception while running recompute_recent for %s", target)
                else:
                    logger.error("Regen failed for %s after tries: %s", target, [t[0] for t in tried])
            else:
                logger.warning("REGEN_SCRIPT missing; no action for %s", target)

        except Exception:
            logger.exception("Exception during library processing for %s", target)
        finally:
            lib_q.task_done()
    logger.info("Library worker stopping")

@app.on_event("startup")
def start_watchers():
    global inbox_observer, lib_observer, inbox_worker_thread, lib_worker_thread
    if INBOX_PATH.exists():
        inbox_observer = Observer()
        inbox_observer.schedule(InboxHandler(), str(INBOX_PATH), recursive=True)
        inbox_observer.start()
        inbox_worker_thread = threading.Thread(target=inbox_worker, args=(BEETS_CONFIG,), daemon=True)
        inbox_worker_thread.start()
        logger.info("Started inbox watcher on %s", INBOX_PATH)
    else:
        logger.warning("Inbox path missing: %s", INBOX_PATH)

    if LIBRARY_PATH.exists():
        lib_observer = Observer()
        lib_observer.schedule(LibraryHandler(), str(LIBRARY_PATH), recursive=True)
        lib_observer.start()
        lib_worker_thread = threading.Thread(target=library_worker, daemon=True)
        lib_worker_thread.start()
        logger.info("Started library watcher on %s", LIBRARY_PATH)
    else:
        logger.warning("Library path missing: %s", LIBRARY_PATH)

@app.on_event("shutdown")
def stop_watchers():
    global inbox_observer, lib_observer
    logger.info("Stopping watchers")
    stop_event.set()
    
    try:
        if inbox_observer is not None:
            inbox_observer.stop()
            inbox_observer.join(timeout=5)
            logger.info("Stopped inbox observer")
    except Exception:
        logger.exception("Error stopping inbox observer")
    
    try:
        if lib_observer is not None:
            lib_observer.stop()
            lib_observer.join(timeout=5)
            logger.info("Stopped library observer")
    except Exception:
        logger.exception("Error stopping library observer")
    
    logger.info("Watchers stopped")
