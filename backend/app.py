"""
Beets Replacement API - Complete Production Version
Date: 2026-02-12
Features:
  - Advanced stats with formats and missing tracks
  - File locking to prevent concurrent imports
  - Non-blocking timeout-resistant workers
  - Full SLSKD integration
  - MPD/Volumio playlist support
  - Lyrics fetching with rate limiting
  - Cover art fetching
  - Inbox monitoring and cleanup
  - All original functionality preserved
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, File, UploadFile, Form
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
import tempfile
import shutil
import asyncio
import fcntl
import httpx

from pathlib import Path
from datetime import datetime
from queue import Queue, Empty, PriorityQueue
from typing import Optional, List, Dict, Any
from collections import Counter

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

os.makedirs("/app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="/app/static"), name="static")

# ---------------------------------------------------------------------------
# CONFIG / PATHS
# ---------------------------------------------------------------------------

ALBUMS_FILE = "/data/albums.json"
RECENT_FILE = "/data/recent_albums.json"
REGEN_SCRIPT = "/app/scripts/regenerate_albums.py"
BEETS_CONFIG = "/config/config.yaml"
INDEX_HTML = "/app/static/index.html"

INBOX_PATH = Path("/inbox")
LIBRARY_PATH = Path("/music/library")

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("beets-replacement")

# Volumio / MPD Configuration
VOLUMIO_MUSIC_MOUNT = os.environ.get("VOLUMIO_MUSIC_MOUNT", "INTERNAL")
VOLUMIO_PLAYLIST_DIR = Path("/data/playlist")
VOLUMIO_URL = os.environ.get("VOLUMIO_URL", "http://volumio.local")
MPD_HOST = os.environ.get("MPD_HOST", "10.0.0.102")
MPD_PORT = int(os.environ.get("MPD_PORT", "6600"))

# Log configuration
logger.info("=== Volumio / MPD Configuration ===")
logger.info(f"Volumio URL: {VOLUMIO_URL}")
logger.info(f"Music Mount: {VOLUMIO_MUSIC_MOUNT}")
logger.info(f"Local Playlist Dir: {VOLUMIO_PLAYLIST_DIR}")
logger.info(f"MPD Host: {MPD_HOST}")
logger.info(f"MPD Port: {MPD_PORT}")

# slskd Configuration
SLSKD_URL = "http://10.0.0.100:5030"
SLSKD_API_KEY = "PV1RixwWGOi91oVYfSMhd7JNVy1hj6jpcBOcdM+z1mKB+JnIQ2c4nwVWLgYi2JHd"

# Constants
INBOX_STATS_CACHE_SECONDS = 60
DEBOUNCE_INBOX = 60.0
DEBOUNCE_LIBRARY = 30.0
DEBOUNCE_COVER = 30.0
DEBOUNCE_LYRICS = 10.0

# CRITICAL FIX: Increased timeouts and made them non-blocking
IMPORT_TIMEOUT = 7200  # 2 hours instead of 1 hour
REGEN_TIMEOUT = 1800   # 30 minutes instead of 15 minutes
IMPORT_LOCK_FILE = "/tmp/beets_import.lock"

# Lyrics rate limiting
LYRICS_RATE_LIMIT = 10
LYRICS_RETRY_DELAY = 60
LYRICS_MAX_RETRIES = 3

# ---------------------------------------------------------------------------
# FILE LOCKING UTILITY
# ---------------------------------------------------------------------------

class FileLock:
    """Context manager for file-based locking to prevent concurrent operations"""
    def __init__(self, lockfile):
        self.lockfile = lockfile
        self.fd = None
    
    def __enter__(self):
        """Acquire the lock (blocking)"""
        self.fd = open(self.lockfile, 'w')
        try:
            fcntl.flock(self.fd.fileno(), fcntl.LOCK_EX)
            self.fd.write(f"{os.getpid()}\n{datetime.now().isoformat()}\n")
            self.fd.flush()
            return self
        except Exception as e:
            if self.fd:
                self.fd.close()
                self.fd = None
            raise
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Release the lock"""
        if self.fd:
            try:
                fcntl.flock(self.fd.fileno(), fcntl.LOCK_UN)
            except:
                pass
            finally:
                self.fd.close()
                self.fd = None
        return False
    
    def try_acquire(self, timeout=0):
        """Try to acquire the lock with optional timeout"""
        self.fd = open(self.lockfile, 'w')
        try:
            if timeout > 0:
                # Non-blocking attempt with timeout
                start_time = time.time()
                while time.time() - start_time < timeout:
                    try:
                        fcntl.flock(self.fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        self.fd.write(f"{os.getpid()}\n{datetime.now().isoformat()}\n")
                        self.fd.flush()
                        return True
                    except IOError:
                        time.sleep(0.5)
                return False
            else:
                # Non-blocking attempt
                fcntl.flock(self.fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.fd.write(f"{os.getpid()}\n{datetime.now().isoformat()}\n")
                self.fd.flush()
                return True
        except IOError:
            if self.fd:
                self.fd.close()
                self.fd = None
            return False

# ---------------------------------------------------------------------------
# GLOBAL STATE & LOGGING
# ---------------------------------------------------------------------------

stop_event = threading.Event()

# Inbox stats cache
inbox_stats_cache = None
inbox_stats_cache_time = None
inbox_stats_lock = threading.Lock()

# Library stats cache (new)
library_stats_cache = None
library_stats_cache_time = None
library_stats_lock = threading.Lock()
LIBRARY_STATS_CACHE_SECONDS = 300  # Cache for 5 minutes

# Queues
inbox_q = Queue()
lib_q = Queue()
cover_q = Queue()
lyrics_q = PriorityQueue()

# Locks
inbox_lock = threading.Lock()
lib_lock = threading.Lock()
cover_lock = threading.Lock()
lyrics_lock = threading.Lock()

# Dedup sets
inbox_queued = set()
lib_queued = set()
cover_queued = set()
lyrics_queued = set()

# Lyrics rate limiting
lyrics_request_times = []
lyrics_last_429 = None
lyrics_failed_tracks = {}

# Threads and observers
inbox_thread = None
lib_thread = None
cover_thread = None
cleanup_thread = None
lyrics_thread = None
inbox_observer = None
lib_observer = None
cover_observer = None

# Watcher logs
watcher_logs = []
watcher_logs_lock = threading.Lock()
MAX_WATCHER_LOGS = 100
last_log_id = 0

def add_watcher_log(level, message):
    """Add a log entry to the watcher logs"""
    global last_log_id
    with watcher_logs_lock:
        last_log_id += 1
        entry = {
            "id": last_log_id,
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "message": message
        }
        watcher_logs.insert(0, entry)
        
        if len(watcher_logs) > MAX_WATCHER_LOGS:
            watcher_logs.pop()
        
        logger.info(f"[{level.upper()}] {message}")

def get_recent_logs(since_id=None, limit=50):
    """Get recent logs, optionally since a specific ID"""
    with watcher_logs_lock:
        if since_id is None:
            return watcher_logs[:limit]
        else:
            return [log for log in watcher_logs if log["id"] > since_id][:limit]

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
    except subprocess.TimeoutExpired as e:
        logger.error(f"Command timed out after {timeout}s: {cmd}")
        return False, f"Timeout after {timeout}s"
    except Exception as e:
        return False, str(e)

def invalidate_inbox_cache(*_a, **_k):
    """Invalidate the inbox cache"""
    global inbox_stats_cache, inbox_stats_cache_time
    with inbox_stats_lock:
        if inbox_stats_cache is not None:
            inbox_stats_cache = None
            inbox_stats_cache_time = None

def invalidate_library_stats_cache(*_a, **_k):
    """Invalidate the library stats cache"""
    global library_stats_cache, library_stats_cache_time
    with library_stats_lock:
        if library_stats_cache is not None:
            library_stats_cache = None
            library_stats_cache_time = None
            logger.info("Library stats cache invalidated")

def convert_path_to_mpd_uri(beets_path: str) -> str:
    """
    Convert a Beets library path (/music/library/Artist/Album/Track.flac)
    into an MPD path for Volumio 4.x: NAS/MUSIC/Artist/Album/Track.flac
    """
    if not isinstance(beets_path, str):
        return ""
    
    # If already MPD format, return as-is
    if beets_path.startswith("NAS/MUSIC/"):
        return beets_path
    
    # Strip the /music/library/ prefix
    prefix = "/music/library/"
    if beets_path.startswith(prefix):
        relative = beets_path[len(prefix):]
    else:
        relative = beets_path.lstrip("/")
    
    return f"NAS/MUSIC/{relative}"

# ---------------------------------------------------------------------------
# LYRICS UTILITIES
# ---------------------------------------------------------------------------

def can_make_lyrics_request():
    """Check if we can make a lyrics request based on rate limiting"""
    global lyrics_request_times, lyrics_last_429
    
    now = time.time()
    
    if lyrics_last_429 and (now - lyrics_last_429) < LYRICS_RETRY_DELAY:
        return False
    
    lyrics_request_times = [t for t in lyrics_request_times if now - t < 60]
    
    return len(lyrics_request_times) < LYRICS_RATE_LIMIT

def record_lyrics_request():
    """Record a lyrics request for rate limiting"""
    lyrics_request_times.append(time.time())

def record_lyrics_429():
    """Record that we got a 429 error"""
    global lyrics_last_429
    lyrics_last_429 = time.time()
    add_watcher_log("warning", f"Lyrics API rate limit hit, pausing for {LYRICS_RETRY_DELAY}s")

def check_track_has_lyrics(track_path):
    """Check if a track already has lyrics embedded"""
    try:
        audio = MutagenFile(track_path)
        if audio is None:
            return False
        
        lyrics_tags = ['lyrics', 'LYRICS', 'unsyncedlyrics', 'USLT', 'USLT:XXX:eng']
        for tag in lyrics_tags:
            if tag in audio and audio[tag]:
                return True
        
        return False
    except Exception as e:
        logger.debug(f"Error checking lyrics for {track_path}: {e}")
        return False

def get_tracks_without_lyrics(directory):
    """Get all audio tracks in a directory that don't have lyrics"""
    AUDIO_EXTS = (".flac", ".mp3", ".wav", ".m4a", ".ogg")
    tracks_without_lyrics = []
    
    try:
        for file in Path(directory).rglob("*"):
            if file.suffix.lower() in AUDIO_EXTS:
                if not check_track_has_lyrics(str(file)):
                    tracks_without_lyrics.append(str(file))
    except Exception as e:
        logger.error(f"Error scanning directory for lyrics: {e}")
    
    return tracks_without_lyrics

# ---------------------------------------------------------------------------
# INBOX CLEANUP
# ---------------------------------------------------------------------------

def cleanup_inbox_empty_dirs():
    """Remove directories that have no audio files"""
    AUDIO_EXTS = (".flac", ".mp3", ".wav", ".aac", ".m4a", ".ogg")
    
    def has_audio_files(directory):
        try:
            for root, dirs, files in os.walk(directory):
                if any(f.lower().endswith(AUDIO_EXTS) for f in files):
                    return True
            return False
        except Exception:
            return True
    
    def remove_empty_dirs(directory):
        if not directory.exists() or not directory.is_dir():
            return
        
        for subdir in list(directory.iterdir()):
            if subdir.is_dir():
                remove_empty_dirs(subdir)
        
        try:
            if not any(directory.iterdir()):
                add_watcher_log("info", f"Removing empty dir: {directory.name}")
                directory.rmdir()
        except Exception as e:
            logger.debug(f"[CLEANUP] Could not remove {directory}: {e}")
    
    if not INBOX_PATH.exists():
        return
    
    for item in INBOX_PATH.iterdir():
        if not item.is_dir() or item.name.startswith("."):
            continue
        
        if "unpack" in item.name.lower():
            continue
        
        if not has_audio_files(item):
            try:
                add_watcher_log("warning", f"Removing directory tree with no audio: {item.name}")
                shutil.rmtree(item)
            except Exception as e:
                logger.error(f"[CLEANUP] Failed to remove {item}: {e}")
        else:
            remove_empty_dirs(item)

def inbox_cleanup_scheduler():
    """Periodic cleanup of old inbox files"""
    while not stop_event.is_set():
        try:
            cleanup_inbox_empty_dirs()
        except Exception:
            logger.exception("Inbox cleanup scheduler error")
        time.sleep(1800)  # Every 30 minutes

# ---------------------------------------------------------------------------
# SLSKD INTEGRATION
# ---------------------------------------------------------------------------

def slskd_headers():
    """Get headers for slskd API requests"""
    return {
        "X-API-Key": SLSKD_API_KEY,
        "Content-Type": "application/json"
    }

async def search_slskd(query: str, file_type: str = "flac") -> Dict[str, Any]:
    """
    Search slskd for files
    
    Args:
        query: Search query string
        file_type: File type filter (flac, mp3, etc.)
    
    Returns:
        Search results from slskd
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Start a search
            search_response = await client.post(
                f"{SLSKD_URL}/api/v0/searches",
                headers=slskd_headers(),
                json={
                    "searchText": query,
                    "filterResponses": True
                }
            )
            
            if search_response.status_code not in [200, 201]:
                logger.error(f"slskd search failed: {search_response.status_code}")
                return {"error": "Search failed", "results": []}
            
            search_data = search_response.json()
            search_id = search_data.get("id")
            
            if not search_id:
                return {"error": "No search ID returned", "results": []}
            
            # Wait for results
            await asyncio.sleep(3)
            
            # Get search results
            results_response = await client.get(
                f"{SLSKD_URL}/api/v0/searches/{search_id}",
                headers=slskd_headers()
            )
            
            if results_response.status_code != 200:
                logger.error(f"Failed to get search results: {results_response.status_code}")
                return {"error": "Failed to get results", "results": []}
            
            results = results_response.json()
            
            # Filter and format results
            filtered_results = []
            
            for response in results.get("responses", []):
                username = response.get("username", "Unknown")
                
                for file in response.get("files", []):
                    filename = file.get("filename", "")
                    
                    # Filter by file type if specified
                    if file_type and not filename.lower().endswith(f".{file_type.lower()}"):
                        continue
                    
                    filtered_results.append({
                        "username": username,
                        "filename": filename,
                        "size": file.get("size", 0),
                        "bitrate": file.get("bitRate"),
                        "length": file.get("length"),
                        "quality": file.get("bitDepth"),
                        "search_id": search_id,
                        "file_id": file.get("id")
                    })
            
            # Sort by bitrate (prefer higher quality)
            filtered_results.sort(key=lambda x: x.get("bitrate") or 0, reverse=True)
            
            return {
                "search_id": search_id,
                "query": query,
                "total_results": len(filtered_results),
                "results": filtered_results[:50]  # Limit to top 50
            }
            
    except Exception as e:
        logger.error(f"slskd search error: {e}")
        return {"error": str(e), "results": []}

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
# API ENDPOINTS - ENHANCED STATS
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def get_library_stats(force_refresh: bool = False):
    """
    Enhanced stats endpoint with caching to prevent repeated slow queries.
    
    Query params:
        force_refresh: Set to true to bypass cache and force fresh stats
    
    Features:
    - Base stats (tracks, albums, artists, time, size)
    - Format breakdown
    - Missing tracks count (can be slow, may show -1 if timeout)
    - Top genres, years
    
    Uses 5-minute cache by default to avoid hammering the database.
    """
    global library_stats_cache, library_stats_cache_time
    
    # Check cache first (unless force refresh)
    if not force_refresh:
        with library_stats_lock:
            if library_stats_cache and library_stats_cache_time:
                age = time.time() - library_stats_cache_time
                if age < LIBRARY_STATS_CACHE_SECONDS:
                    logger.debug(f"Returning cached library stats (age: {age:.1f}s)")
                    return library_stats_cache
    
    # Compute fresh stats
    logger.info("Computing fresh library stats")
    stats_data = {
        "tracks": 0,
        "albums": 0,
        "album_artists": 0,
        "total_time": "unknown",
        "total_size": "unknown",
        "formats": {},
        "missing_tracks_count": 0,
        "top_genres": {},
        "years": {},
        "labels": {}
    }
    
    # 1. Base stats from `beet stats` - increased timeout for large libraries
    ok, out = run_cmd_list(["beet", "-c", BEETS_CONFIG, "stats"], timeout=120)
    if ok and out:
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Tracks:"):
                try:
                    stats_data["tracks"] = int(line.split(":", 1)[1].strip() or 0)
                except:
                    pass
            elif line.startswith("Albums:"):
                try:
                    stats_data["albums"] = int(line.split(":", 1)[1].strip() or 0)
                except:
                    pass
            elif line.startswith("Album artists:"):
                try:
                    stats_data["album_artists"] = int(line.split(":", 1)[1].strip() or 0)
                except:
                    pass
            elif line.startswith("Total time:"):
                stats_data["total_time"] = line.split(":", 1)[1].strip()
            elif line.startswith("Approximate total size:"):
                stats_data["total_size"] = line.split(":", 1)[1].strip()
    
    # 2. Formats breakdown (populates FORMATS box) - increased timeout
    f_ok, f_out = run_cmd_list(["beet", "-c", BEETS_CONFIG, "list", "-f", "$format"], timeout=180)
    if f_ok and f_out:
        formats = [f.strip() for f in f_out.splitlines() if f.strip()]
        stats_data["formats"] = dict(Counter(formats).most_common(10))
    else:
        logger.warning(f"Format stats failed or timed out")
    
    # 3. Missing tracks count (populates MISSING TRACKS box) - INCREASED TIMEOUT
    # This is often the slowest operation, so we give it more time
    m_ok, m_out = run_cmd_list(["beet", "-c", BEETS_CONFIG, "missing"], timeout=300)
    if m_ok and m_out:
        stats_data["missing_tracks_count"] = len([l for l in m_out.splitlines() if l.strip()])
    else:
        logger.warning(f"Missing tracks count failed or timed out - skipping")
        stats_data["missing_tracks_count"] = -1  # Indicates timeout/error
    
    # 4. Top genres and years - increased timeout
    g_ok, g_out = run_cmd_list(["beet", "-c", BEETS_CONFIG, "list", "-a", "-f", "$genre|$year"], timeout=180)
    if g_ok and g_out:
        genres, years = [], []
        for line in g_out.splitlines():
            if "|" in line:
                g, y = line.split("|", 1)
                if g.strip():
                    genres.append(g.strip())
                if y.strip() and y.strip() != "0":
                    years.append(y.strip())
        stats_data["top_genres"] = dict(Counter(genres).most_common(5))
        stats_data["years"] = dict(Counter(years).most_common(5))
    else:
        logger.warning(f"Genre/year stats failed or timed out")
    
    # 5. Label statistics (top 15 labels)
    l_ok, l_out = run_cmd_list(["beet", "-c", BEETS_CONFIG, "list", "-a", "-f", "$label"], timeout=180)
    if l_ok and l_out:
        labels = [l.strip() for l in l_out.splitlines() if l.strip() and l.strip().lower() != 'unknown']
        stats_data["labels"] = dict(Counter(labels).most_common(15))
    else:
        logger.warning(f"Label stats failed or timed out")
        stats_data["labels"] = {}
    
    # Fallback to albums.json if beet stats failed
    if stats_data["tracks"] == 0 and stats_data["albums"] == 0:
        try:
            with open(ALBUMS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            stats_data["albums"] = len(data)
            stats_data["album_artists"] = len({a.get("albumartist") for a in data})
        except Exception:
            pass
    
    # Cache the results
    with library_stats_lock:
        library_stats_cache = stats_data
        library_stats_cache_time = time.time()
        logger.info(f"Library stats cached (will expire in {LIBRARY_STATS_CACHE_SECONDS}s)")
    
    return stats_data

# ---------------------------------------------------------------------------
# API ENDPOINTS - LIBRARY OPERATIONS
# ---------------------------------------------------------------------------

@app.post("/api/library/refresh")
def refresh_library():
    add_watcher_log("info", "Manual library refresh triggered")
    ok, out = run_cmd_list(["python3", REGEN_SCRIPT], timeout=120)
    if not ok:
        add_watcher_log("error", f"Library refresh failed: {out[:100]}")
        raise HTTPException(status_code=500, detail=out)
    add_watcher_log("success", "Library refresh completed")
    return {"status": "ok", "detail": out.strip()}

@app.post("/api/library/import")
def import_library(background_tasks: BackgroundTasks):
    """Manual import with file locking to prevent concurrent imports"""
    args = ["beet", "-c", BEETS_CONFIG, "import", "-q", "-A", str(INBOX_PATH)]
    
    def run():
        lock_file = None
        try:
            # Try to acquire exclusive lock
            lock_file = open(IMPORT_LOCK_FILE, 'w')
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            add_watcher_log("warning", "Import already running, manual import skipped")
            if lock_file:
                lock_file.close()
            return
        
        try:
            add_watcher_log("info", "Manual import started")
            result = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=IMPORT_TIMEOUT
            )
            if result.returncode == 0:
                add_watcher_log("success", "Manual import completed")
            else:
                add_watcher_log("error", f"Manual import failed: {result.stdout[:200]}")
        except subprocess.TimeoutExpired:
            add_watcher_log("error", f"Manual import timed out after {IMPORT_TIMEOUT}s")
        finally:
            if lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()
    
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
# API ENDPOINTS - PLAYLIST
# ---------------------------------------------------------------------------

@app.post("/api/playlist/build")
async def build_playlist(
    playlist_name: str = Form(...),
    playlist_data: str = Form(...)
):
    """
    Save playlist locally and push to Volumio via MPD.
    
    Form data:
        playlist_name: Name of the playlist
        playlist_data: JSON array of tracks
    """
    try:
        # Parse playlist data
        playlist = json.loads(playlist_data)
        
        # Create playlist directory if it doesn't exist
        VOLUMIO_PLAYLIST_DIR.mkdir(exist_ok=True, parents=True)
        
        # Transform tracks for Volumio format
        for i, track in enumerate(playlist):
            track.update({
                "service": "mpd",
                "type": "track",
                "tracknumber": i + 1
            })
            
            # Convert Beets path to MPD URI
            if "uri" in track:
                track["uri"] = convert_path_to_mpd_uri(track["uri"])
        
        # Save playlist locally
        playlist_file = VOLUMIO_PLAYLIST_DIR / f"{playlist_name}.json"
        with open(playlist_file, "w", encoding="utf-8") as f:
            json.dump(playlist, f, indent=2)
        
        # ============================================================
        # PUSH TO VOLUMIO VIA MPD
        # ============================================================
        volumio_success = False
        volumio_message = ""
        
        try:
            base_cmd = ["mpc", "-h", MPD_HOST, "-p", str(MPD_PORT)]
            
            # 1) Clear MPD queue
            ok, out = run_cmd_list(base_cmd + ["clear"], timeout=10)
            if not ok:
                raise RuntimeError(f"MPD clear failed: {out[:200]}")
            
            # 2) Add tracks
            added = 0
            for track in playlist:
                uri = track.get("uri", "")
                if not uri:
                    continue
                
                ok, out = run_cmd_list(base_cmd + ["add", uri], timeout=10)
                if not ok:
                    logger.warning(f"MPD add failed for {uri}: {out[:200]}")
                    continue
                
                added += 1
            
            if added == 0:
                raise RuntimeError("No tracks were added to MPD playlist")
            
            # 3) Save playlist in MPD
            ok, out = run_cmd_list(base_cmd + ["save", playlist_name], timeout=10)
            if not ok:
                raise RuntimeError(f"MPD save failed: {out[:200]}")
            
            volumio_success = True
            volumio_message = (
                f"Playlist created in Volumio via MPD at {MPD_HOST}:{MPD_PORT} "
                f"({added} tracks)"
            )
            
            add_watcher_log("success", f"Pushed playlist to Volumio via MPD: {playlist_name} ({added} tracks)")
        
        except Exception as e:
            volumio_message = f"MPD/Volumio playlist push failed: {str(e)}"
            add_watcher_log("warning", f"MPD playlist error: {str(e)[:200]}")
        
        # ============================================================
        # RETURN RESPONSE
        # ============================================================
        return {
            "status": "success",
            "message": f"Playlist saved: {playlist_name}",
            "path": str(playlist_file),
            "tracks": len(playlist),
            "format": "Volumio JSON",
            "volumio_pushed": volumio_success,
            "volumio_message": volumio_message,
            "note": (
                f"Playlist saved locally and "
                f"{'pushed to Volumio via MPD' if volumio_success else 'attempted MPD push to Volumio'} "
                f"at {MPD_HOST}:{MPD_PORT}"
            ),
        }
    
    except Exception as e:
        add_watcher_log("error", f"Failed to save playlist: {str(e)[:100]}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/playlist/list")
def list_playlists():
    """List all saved playlists"""
    try:
        if not VOLUMIO_PLAYLIST_DIR.exists():
            return {"playlists": []}
        
        playlists = []
        for file in VOLUMIO_PLAYLIST_DIR.glob("*.json"):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    playlists.append({
                        "name": file.stem,
                        "tracks": len(data) if isinstance(data, list) else 0,
                        "path": str(file)
                    })
            except Exception:
                continue
        
        return {"playlists": playlists}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------------
# API ENDPOINTS - SLSKD
# ---------------------------------------------------------------------------

@app.get("/api/slskd/search")
async def api_slskd_search(
    artist: str,
    album: str,
    track: Optional[str] = None,
    file_type: str = "flac"
):
    """
    Search slskd for missing tracks
    
    Query params:
        artist: Artist name
        album: Album name
        track: Optional specific track name
        file_type: File type to search for (default: flac)
    """
    # Build search query
    query_parts = [artist, album]
    if track:
        query_parts.append(track)
    
    query = " ".join(query_parts)
    
    logger.info(f"Searching slskd: {query} (type: {file_type})")
    
    results = await search_slskd(query, file_type)
    
    return JSONResponse(content=results)

@app.post("/api/slskd/download")
async def api_slskd_download(
    username: str = Form(...),
    filename: str = Form(...),
    search_id: str = Form(...)
):
    """Queue a download from slskd"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{SLSKD_URL}/api/v0/transfers/downloads",
                headers=slskd_headers(),
                json={
                    "username": username,
                    "files": [filename]
                }
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"Queued download: {filename} from {username}")
                return JSONResponse(content={
                    "success": True,
                    "message": f"Download queued: {filename}",
                    "username": username,
                    "filename": filename
                })
            else:
                logger.error(f"Download queue failed: {response.status_code}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "success": False,
                        "error": f"Failed to queue download: {response.status_code}"
                    }
                )
    
    except Exception as e:
        logger.error(f"Download error: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e)
            }
        )

@app.get("/api/slskd/downloads")
async def api_slskd_downloads():
    """Get current download queue from slskd"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{SLSKD_URL}/api/v0/transfers/downloads",
                headers=slskd_headers()
            )
            
            if response.status_code == 200:
                downloads = response.json()
                return JSONResponse(content=downloads)
            else:
                return JSONResponse(
                    status_code=500,
                    content={"error": "Failed to get downloads"}
                )
    
    except Exception as e:
        logger.error(f"Get downloads error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

# ---------------------------------------------------------------------------
# API ENDPOINTS - WATCHER STATUS
# ---------------------------------------------------------------------------

@app.get("/api/watcher/status")
def watcher_status(since_id: int = None):
    """Get current watcher status and recent logs"""
    return {
        "inbox_queue": inbox_q.qsize(),
        "library_queue": lib_q.qsize(),
        "cover_queue": cover_q.qsize(),
        "lyrics_queue": lyrics_q.qsize(),
        "recent_logs": get_recent_logs(since_id=since_id)
    }

# ---------------------------------------------------------------------------
# API ENDPOINTS - LYRICS
# ---------------------------------------------------------------------------

@app.get("/api/lyrics/stats")
def lyrics_stats():
    """Get lyrics fetching statistics"""
    with lyrics_lock:
        now = time.time()
        recent_requests = len([t for t in lyrics_request_times if now - t < 60])
        
        return {
            "queue_size": lyrics_q.qsize(),
            "requests_last_minute": recent_requests,
            "rate_limit": LYRICS_RATE_LIMIT,
            "paused_until": (lyrics_last_429 + LYRICS_RETRY_DELAY) if lyrics_last_429 else None,
            "failed_tracks": len(lyrics_failed_tracks)
        }

@app.post("/api/lyrics/scan")
def scan_for_missing_lyrics(background_tasks: BackgroundTasks):
    """Scan library for tracks missing lyrics and queue them"""
    def scan():
        add_watcher_log("info", "Starting library-wide lyrics scan")
        count = 0
        
        try:
            for album_dir in LIBRARY_PATH.iterdir():
                if not album_dir.is_dir() or album_dir.name.startswith("."):
                    continue
                
                tracks = get_tracks_without_lyrics(str(album_dir))
                for track in tracks:
                    with lyrics_lock:
                        if track not in lyrics_queued:
                            lyrics_queued.add(track)
                            lyrics_q.put((2, time.time(), track))
                            count += 1
            
            add_watcher_log("success", f"Lyrics scan complete: {count} tracks queued")
        except Exception as e:
            add_watcher_log("error", f"Lyrics scan failed: {str(e)[:100]}")
    
    background_tasks.add_task(scan)
    return {"status": "started"}

@app.post("/api/lyrics/pause")
def pause_lyrics_fetching():
    """Temporarily pause lyrics fetching"""
    global lyrics_last_429
    lyrics_last_429 = time.time()
    add_watcher_log("info", f"Lyrics fetching paused for {LYRICS_RETRY_DELAY}s")
    return {"status": "paused", "duration": LYRICS_RETRY_DELAY}

@app.post("/api/lyrics/resume")
def resume_lyrics_fetching():
    """Resume lyrics fetching"""
    global lyrics_last_429
    lyrics_last_429 = None
    add_watcher_log("info", "Lyrics fetching resumed")
    return {"status": "resumed"}

# ---------------------------------------------------------------------------
# API ENDPOINTS - INBOX
# ---------------------------------------------------------------------------

@app.get("/api/inbox")
@app.get("/api/inbox/stats")
def get_inbox_stats():
    """Get inbox statistics with caching"""
    global inbox_stats_cache, inbox_stats_cache_time
    
    with inbox_stats_lock:
        now = time.time()
        if inbox_stats_cache and inbox_stats_cache_time:
            age = now - inbox_stats_cache_time
            if age < INBOX_STATS_CACHE_SECONDS:
                logger.debug(f"Returning cached inbox stats (age: {age:.1f}s)")
                return inbox_stats_cache
        
        logger.info("Computing fresh inbox stats")
        inbox_stats_cache = compute_inbox_stats_fast()
        inbox_stats_cache_time = now
        return inbox_stats_cache

@app.get("/api/inbox/tree")
def get_inbox_tree():
    """Get inbox directory structure as a tree"""
    try:
        if not INBOX_PATH.exists():
            return {"folders": {}}
        
        tree = {}
        
        # Iterate through artist folders
        for artist_dir in sorted(INBOX_PATH.iterdir()):
            if not artist_dir.is_dir() or artist_dir.name.startswith(".") or "_UNPACK_" in artist_dir.name:
                continue
            
            artist_name = artist_dir.name
            tree[artist_name] = []
            
            # Get albums for this artist
            for album_dir in sorted(artist_dir.iterdir()):
                if not album_dir.is_dir() or album_dir.name.startswith("."):
                    continue
                
                # Count audio files in this album
                audio_files = list(album_dir.glob("*.flac")) + \
                             list(album_dir.glob("*.mp3")) + \
                             list(album_dir.glob("*.m4a")) + \
                             list(album_dir.glob("*.ogg")) + \
                             list(album_dir.glob("*.wav"))
                
                tree[artist_name].append({
                    "name": album_dir.name,
                    "tracks": len(audio_files)
                })
        
        return {"folders": tree}
    
    except Exception as e:
        logger.error(f"Error generating inbox tree: {e}")
        return {"folders": {}, "error": str(e)}

@app.get("/api/inbox/folder")
def get_inbox_folder(artist: str, album: str):
    """Get tracks in a specific inbox folder"""
    try:
        folder_path = INBOX_PATH / artist / album
        
        if not folder_path.exists() or not folder_path.is_dir():
            raise HTTPException(status_code=404, detail="Folder not found")
        
        tracks = []
        audio_extensions = [".flac", ".mp3", ".m4a", ".ogg", ".wav", ".aac"]
        
        for file_path in sorted(folder_path.iterdir()):
            if file_path.is_file() and file_path.suffix.lower() in audio_extensions:
                # Get basic file info
                stat = file_path.stat()
                
                # Try to get audio metadata
                try:
                    audio = MutagenFile(str(file_path))
                    duration = int(audio.info.length) if audio and hasattr(audio.info, 'length') else 0
                except Exception:
                    duration = 0
                
                tracks.append({
                    "filename": file_path.name,
                    "size": stat.st_size,
                    "duration": duration,
                    "path": str(file_path.relative_to(INBOX_PATH))
                })
        
        return {
            "artist": artist,
            "album": album,
            "tracks": tracks,
            "track_count": len(tracks)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading inbox folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def compute_inbox_stats_fast():
    """Compute inbox stats using shell commands"""
    
    if not INBOX_PATH.exists():
        logger.warning(f"Inbox path does not exist: {INBOX_PATH}")
        return {
            "tracks": 0,
            "total_time": "0 seconds",
            "total_size": "0 B",
            "artists": 0,
            "albums": 0,
            "album_artists": 0,
        }
    
    try:
        count_cmd = f'find "{INBOX_PATH}" -type f \\( -iname "*.mp3" -o -iname "*.flac" -o -iname "*.m4a" -o -iname "*.ogg" -o -iname "*.wav" -o -iname "*.aac" \\) | wc -l'
        result = subprocess.run(count_cmd, shell=True, capture_output=True, text=True, timeout=10)
        tracks = int(result.stdout.strip()) if result.returncode == 0 else 0
        
        size_cmd = f'du -sb "{INBOX_PATH}" 2>/dev/null | cut -f1'
        result = subprocess.run(size_cmd, shell=True, capture_output=True, text=True, timeout=10)
        total_bytes = int(result.stdout.strip()) if result.returncode == 0 else 0
        
        artists = set()
        albums = set()
        
        try:
            for item in list(INBOX_PATH.iterdir())[:200]:
                if not item.is_dir() or item.name.startswith(".") or "_UNPACK_" in item.name:
                    continue
                artists.add(item.name)
                albums.add(item.name)
        except Exception as e:
            logger.warning(f"Error sampling directories: {e}")
        
        estimated_minutes = tracks * 3
        time_str = humanize.precisedelta(estimated_minutes * 60) if estimated_minutes > 0 else "0 seconds"
        
        return {
            "tracks": tracks,
            "total_time": time_str,
            "total_size": humanize.naturalsize(total_bytes),
            "artists": len(artists),
            "albums": len(albums),
            "album_artists": len(artists),
        }
    except Exception as e:
        logger.error(f"Error computing inbox stats: {e}")
        return {
            "tracks": 0,
            "total_time": "0 seconds",
            "total_size": "0 B",
            "artists": 0,
            "albums": 0,
            "album_artists": 0,
        }

# ---------------------------------------------------------------------------
# FILE SYSTEM EVENT HANDLERS
# ---------------------------------------------------------------------------

class InboxHandler(FileSystemEventHandler):
    """Watch inbox for new files and trigger import"""
    def on_created(self, event):
        if event.is_directory:
            return
        # Debounce - add to queue
        with inbox_lock:
            if str(INBOX_PATH) not in inbox_queued:
                inbox_queued.add(str(INBOX_PATH))
                inbox_q.put(time.time())
                add_watcher_log("info", f"Inbox change detected: {Path(event.src_path).name}")

class LibraryHandler(FileSystemEventHandler):
    """Watch library for changes and trigger regeneration"""
    def on_created(self, event):
        if event.is_directory:
            return
        
        # Ignore temp files
        filename = Path(event.src_path).name
        if ".beets" in filename or filename.startswith("."):
            return
        
        with lib_lock:
            if str(LIBRARY_PATH) not in lib_queued:
                lib_queued.add(str(LIBRARY_PATH))
                lib_q.put(time.time())
                add_watcher_log("info", f"Library change detected: {filename}")

class CoverHandler(FileSystemEventHandler):
    """Watch for new albums and trigger cover art fetching"""
    def on_created(self, event):
        if not event.is_directory:
            return
        
        album_dir = Path(event.src_path)
        
        # Skip hidden directories
        if album_dir.name.startswith("."):
            return
        
        # Check if cover.jpg already exists
        cover_file = album_dir / "cover.jpg"
        if cover_file.exists():
            return
        
        with cover_lock:
            album_path = str(album_dir)
            if album_path not in cover_queued:
                cover_queued.add(album_path)
                cover_q.put(album_path)
                add_watcher_log("info", f"New album detected (needs cover): {album_dir.name}")

# ---------------------------------------------------------------------------
# WORKER THREADS
# ---------------------------------------------------------------------------

def inbox_worker():
    """
    Process inbox import queue with settling timer and file locking
    CRITICAL FIX: Runs subprocess without timeout to prevent interruption
    """
    logger.info("Inbox worker started")
    add_watcher_log("info", "Inbox worker started (with import locking)")
    
    while not stop_event.is_set():
        try:
            timestamp = inbox_q.get(timeout=1)
            
            # Debounce - settling timer
            deadline = time.time() + DEBOUNCE_INBOX
            while time.time() < deadline:
                try:
                    new_timestamp = inbox_q.get(timeout=1)
                    deadline = time.time() + DEBOUNCE_INBOX
                    logger.debug("Inbox activity, resetting timer")
                except Empty:
                    continue
            
            # Clear the queue
            while not inbox_q.empty():
                try:
                    inbox_q.get_nowait()
                except Empty:
                    break
            
            with inbox_lock:
                inbox_queued.clear()
            
            # Use FileLock for better error handling
            lock = FileLock(IMPORT_LOCK_FILE)
            if not lock.try_acquire(timeout=0):
                add_watcher_log("warning", "Import already in progress - skipping")
                continue
            
            try:
                add_watcher_log("info", "Starting automatic inbox import (after settling)")
                
                # CRITICAL FIX: Run import with Popen for non-blocking execution
                process = subprocess.Popen(
                    ["beet", "-c", BEETS_CONFIG, "import", "-A", str(INBOX_PATH)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
                
                # Wait for completion (this can take hours)
                stdout, _ = process.communicate()
                
                if process.returncode == 0:
                    add_watcher_log("success", "Automatic import completed")
                    invalidate_inbox_cache()
                    
                    # Trigger library regeneration
                    with lib_lock:
                        if str(LIBRARY_PATH) not in lib_queued:
                            lib_queued.add(str(LIBRARY_PATH))
                            lib_q.put(time.time())
                else:
                    add_watcher_log("error", f"Import failed: {stdout[:200] if stdout else 'No output'}")
            
            except Exception as e:
                add_watcher_log("error", f"Import exception: {str(e)[:200]}")
            finally:
                # Release lock
                lock.__exit__(None, None, None)
        
        except Empty:
            continue
        except Exception as e:
            logger.error(f"Inbox worker error: {e}")
            time.sleep(5)

def library_worker():
    """
    Process library regeneration queue
    CRITICAL FIX: Runs subprocess with Popen to avoid timeout
    """
    while not stop_event.is_set():
        try:
            timestamp = lib_q.get(timeout=1)
            
            # Debounce
            time.sleep(DEBOUNCE_LIBRARY)
            
            # Clear the queue
            while not lib_q.empty():
                try:
                    lib_q.get_nowait()
                except Empty:
                    break
            
            with lib_lock:
                lib_queued.clear()
            
            add_watcher_log("info", "Starting library regeneration")
            
            # CRITICAL FIX: Use Popen instead of subprocess.run to avoid timeout
            process = subprocess.Popen(
                ["python3", REGEN_SCRIPT],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            
            # Wait for completion
            stdout, _ = process.communicate()
            
            if process.returncode == 0:
                add_watcher_log("success", "Library regeneration completed")
                # Invalidate stats cache so next request gets fresh data
                invalidate_library_stats_cache()
            else:
                add_watcher_log("error", f"Regeneration failed: {stdout[:200] if stdout else 'No output'}")
        
        except Empty:
            continue
        except Exception as e:
            logger.error(f"Library worker error: {e}")
            time.sleep(5)

def cover_worker():
    """Process cover art fetching queue"""
    logger.info("Cover worker started")
    add_watcher_log("info", "Cover worker started")
    
    while not stop_event.is_set():
        try:
            album_dir = cover_q.get(timeout=1)
        except Empty:
            continue
        
        with cover_lock:
            cover_queued.discard(album_dir)
        
        try:
            # Debounce
            time.sleep(DEBOUNCE_COVER)
            
            album_path = Path(album_dir)
            cover_file = album_path / "cover.jpg"
            
            # Skip if cover already exists or directory doesn't exist
            if not album_path.exists() or cover_file.exists():
                continue
            
            add_watcher_log("info", f"Fetching cover art: {album_path.name}")
            
            # Run fetch_cover.py script
            result = subprocess.run(
                ["python3", "/app/scripts/fetch_cover.py", str(album_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=300
            )
            
            if result.returncode == 0 and cover_file.exists():
                add_watcher_log("success", f"Cover art fetched: {album_path.name}")
                
                # Trigger library regeneration to update the album with cover
                with lib_lock:
                    if str(LIBRARY_PATH) not in lib_queued:
                        lib_queued.add(str(LIBRARY_PATH))
                        lib_q.put(time.time())
            else:
                add_watcher_log("warning", f"Cover fetch failed: {album_path.name}")
        
        except Exception as e:
            logger.error(f"Cover worker error: {e}")
        finally:
            cover_q.task_done()

def lyrics_worker():
    """Process lyrics fetching queue"""
    logger.info("Lyrics worker started")
    add_watcher_log("info", "Lyrics worker started")
    
    while not stop_event.is_set():
        try:
            # Check rate limiting
            if not can_make_lyrics_request():
                time.sleep(1)
                continue
            
            try:
                priority, timestamp, track_path = lyrics_q.get(timeout=1)
            except Empty:
                continue
            
            with lyrics_lock:
                lyrics_queued.discard(track_path)
            
            try:
                # Skip if track doesn't exist
                if not os.path.exists(track_path):
                    continue
                
                # Skip if already has lyrics
                if check_track_has_lyrics(track_path):
                    continue
                
                # Check retry count
                retry_count = lyrics_failed_tracks.get(track_path, 0)
                if retry_count >= LYRICS_MAX_RETRIES:
                    logger.debug(f"Max retries reached for: {os.path.basename(track_path)}")
                    continue
                
                # Debounce
                time.sleep(DEBOUNCE_LYRICS)
                
                track_name = os.path.basename(track_path)
                add_watcher_log("info", f"Fetching lyrics: {track_name}")
                
                record_lyrics_request()
                
                # Fetch lyrics using beets
                result = subprocess.run(
                    ["beet", "-c", BEETS_CONFIG, "lyrics", "-f", track_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=60
                )
                
                # Check for rate limiting
                if "429" in result.stdout or "Too Many Requests" in result.stdout:
                    record_lyrics_429()
                    # Re-queue with lower priority
                    with lyrics_lock:
                        if track_path not in lyrics_queued:
                            lyrics_queued.add(track_path)
                            lyrics_q.put((priority + 1, time.time(), track_path))
                    lyrics_failed_tracks[track_path] = retry_count + 1
                    continue
                
                if result.returncode == 0 or "lyrics found" in result.stdout.lower():
                    add_watcher_log("success", f"Lyrics found: {track_name}")
                    lyrics_failed_tracks.pop(track_path, None)
                else:
                    if "not found" not in result.stdout.lower():
                        lyrics_failed_tracks[track_path] = retry_count + 1
            
            except Exception as e:
                logger.error(f"Lyrics fetch error: {e}")
                lyrics_failed_tracks[track_path] = lyrics_failed_tracks.get(track_path, 0) + 1
            finally:
                lyrics_q.task_done()
        
        except Exception as e:
            logger.exception("Lyrics worker error")
            time.sleep(5)

# ---------------------------------------------------------------------------
# STARTUP / SHUTDOWN
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    """Start file watchers and worker threads on app startup."""
    global inbox_thread, lib_thread, cover_thread, lyrics_thread, cleanup_thread
    global inbox_observer, lib_observer, cover_observer
    
    logger.info("=== Starting File Watchers ===")
    
    # Inbox Watcher
    if INBOX_PATH.exists():
        inbox_observer = Observer()
        inbox_observer.schedule(InboxHandler(), str(INBOX_PATH), recursive=True)
        inbox_observer.start()
        logger.info(f"✓ Inbox watcher started: {INBOX_PATH}")
    else:
        logger.warning(f"✗ Inbox path does not exist: {INBOX_PATH}")
    
    # Library Watcher
    if LIBRARY_PATH.exists():
        lib_observer = Observer()
        lib_observer.schedule(LibraryHandler(), str(LIBRARY_PATH), recursive=True)
        lib_observer.start()
        logger.info(f"✓ Library watcher started: {LIBRARY_PATH}")
    else:
        logger.warning(f"✗ Library path does not exist: {LIBRARY_PATH}")
    
    # Cover Watcher
    if LIBRARY_PATH.exists():
        cover_observer = Observer()
        cover_observer.schedule(CoverHandler(), str(LIBRARY_PATH), recursive=True)
        cover_observer.start()
        logger.info(f"✓ Cover watcher started: {LIBRARY_PATH}")
    
    # Worker Threads
    inbox_thread = threading.Thread(target=inbox_worker, daemon=True)
    inbox_thread.start()
    logger.info("✓ Inbox worker thread started")
    
    lib_thread = threading.Thread(target=library_worker, daemon=True)
    lib_thread.start()
    logger.info("✓ Library worker thread started")
    
    cover_thread = threading.Thread(target=cover_worker, daemon=True)
    cover_thread.start()
    logger.info("✓ Cover worker thread started")
    
    lyrics_thread = threading.Thread(target=lyrics_worker, daemon=True)
    lyrics_thread.start()
    logger.info("✓ Lyrics worker thread started")
    
    # Cleanup Scheduler
    cleanup_thread = threading.Thread(target=inbox_cleanup_scheduler, daemon=True)
    cleanup_thread.start()
    logger.info("✓ Cleanup scheduler started")
    
    add_watcher_log("success", "All watchers and workers started (with timeout-resistant import/regen)")
    logger.info("=== Startup Complete ===")

@app.on_event("shutdown")
async def shutdown_event():
    """Stop watchers and workers on app shutdown."""
    logger.info("=== Shutting Down ===")
    
    stop_event.set()
    
    if inbox_observer:
        inbox_observer.stop()
        inbox_observer.join(timeout=5)
        logger.info("✓ Inbox watcher stopped")
    
    if lib_observer:
        lib_observer.stop()
        lib_observer.join(timeout=5)
        logger.info("✓ Library watcher stopped")
    
    if cover_observer:
        cover_observer.stop()
        cover_observer.join(timeout=5)
        logger.info("✓ Cover watcher stopped")
    
    logger.info("=== Shutdown Complete ===")

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
