#!/usr/bin/env python3
# /app/watch_imports.py
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Timer, Lock
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler

# Config
WATCH_DIR = "/music/inbox"
LIB_DIR = "/music/library"
DB = "/data/musiclibrary.db"
PIDFILE = "/var/run/import_watchdog.pid"
LOGFILE = "/var/log/import_watchdog.log"
BEET_LOG = "/data/last_beets_imports.log"
DEBOUNCE_SECONDS = 3
START_DELAY = 2

# Setup logging
Path(LOGFILE).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOGFILE), logging.StreamHandler(sys.stdout)],
)

_lock = Lock()
_timer = None

def write_pid():
    Path(PIDFILE).parent.mkdir(parents=True, exist_ok=True)
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))

def remove_pid():
    try:
        os.remove(PIDFILE)
    except FileNotFoundError:
        pass

def is_import_running():
    # pgrep -f "beet .*import"
    try:
        out = subprocess.check_output(["pgrep", "-f", "beet .*import"], stderr=subprocess.DEVNULL)
        return bool(out.strip())
    except subprocess.CalledProcessError:
        return False

def start_import():
    if is_import_running():
        logging.info("Import already running; skipping start.")
        return

    # quick check for files in inbox
    try:
        inbox_count = int(subprocess.check_output(["bash", "-lc", f"find {WATCH_DIR} -type f | wc -l"]).strip())
    except Exception:
        inbox_count = 0

    if inbox_count == 0:
        logging.info("No files in inbox; skipping import.")
        return

    logging.info("Starting beet import (delayed %ss)...", START_DELAY)
    time.sleep(START_DELAY)
    cmd = f"sudo -u appuser beet -l {DB} import -A -q --resume {LIB_DIR}"
    with open(BEET_LOG, "ab") as out:
        # run in background but keep output appended to BEET_LOG
        p = subprocess.Popen(cmd, shell=True, stdout=out, stderr=subprocess.STDOUT)
        logging.info("Launched beet import pid=%s", p.pid)

def _debounced_start():
    global _timer
    with _lock:
        if _timer:
            _timer.cancel()
        _timer = Timer(DEBOUNCE_SECONDS, start_import)
        _timer.daemon = True
        _timer.start()

class MyHandler(PatternMatchingEventHandler):
    def __init__(self):
        super().__init__(patterns=["*.mp3", "*.flac", "*.m4a", "*.wav"], ignore_directories=True, case_sensitive=False)

    def on_created(self, event):
        logging.info("Detected created: %s", event.src_path)
        _debounced_start()

    def on_moved(self, event):
        logging.info("Detected moved: %s", event.dest_path)
        _debounced_start()

    def on_closed(self, event):
        logging.info("Detected closed: %s", event.src_path)
        _debounced_start()

def main():
    logging.info("Import watcher starting. Watch dir: %s", WATCH_DIR)
    write_pid()
    try:
        event_handler = MyHandler()
        observer = Observer()
        observer.schedule(event_handler, WATCH_DIR, recursive=True)
        observer.start()
        # initial check on startup
        _debounced_start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down (KeyboardInterrupt)")
    except Exception:
        logging.exception("Watcher crashed")
    finally:
        try:
            observer.stop()
            observer.join()
        except Exception:
            pass
        remove_pid()
        logging.info("Watcher stopped")

if __name__ == "__main__":
    main()
