#!/usr/bin/env python3
import os
import sys
import time
import requests
import logging
from pathlib import Path

# -----------------------------
# CONFIGURATION
# -----------------------------
BEETS_API = os.getenv("BEETS_API", "http://localhost:7080")
INBOX = Path(os.getenv("BEETS_INBOX",
    "/srv/dev-disk-by-uuid-901efa52-2e9a-4fdd-a53c-08b891fb8458/SnapRaid_mergerfs/Beets-Flask/clean"))
QUARANTINE = Path(os.getenv("BEETS_QUARANTINE",
    "/srv/dev-disk-by-uuid-901efa52-2e9a-4fdd-a53c-08b891fb8458/SnapRaid_mergerfs/Beets-Flask/unmatched"))
LOGFILE = os.getenv("BEETS_LOGFILE", "/srv/logs/beets_rebuild.log")
REQUEST_TIMEOUT = int(os.getenv("BEETS_REQUEST_TIMEOUT", "30"))
IMPORT_WAIT = int(os.getenv("BEETS_IMPORT_WAIT", "60"))  # seconds to wait after triggering import
POLL_INTERVAL = int(os.getenv("BEETS_POLL_INTERVAL", "10"))  # seconds between status checks
MAX_WAIT_TIME = int(os.getenv("BEETS_MAX_WAIT", "3600"))  # max seconds to wait for import
AUDIO_EXTENSIONS = (".flac", ".mp3", ".m4a", ".wav", ".ogg", ".aiff", ".alac", ".ape", ".wv")

# -----------------------------
# LOGGING SETUP
# -----------------------------
log_dir = os.path.dirname(LOGFILE)
if log_dir:
    os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    filename=LOGFILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(console)

# -----------------------------
# COUNT AUDIO FILES
# -----------------------------
def count_audio_files(directory):
    """Count audio files in a directory"""
    count = 0
    for root, dirs, files in os.walk(directory):
        for f in files:
            if f.lower().endswith(AUDIO_EXTENSIONS):
                count += 1
    return count

# -----------------------------
# CHECK IMPORT STATUS
# -----------------------------
def check_import_status():
    """
    Check if an import is currently running.
    Adjust this based on your FastAPI implementation.
    """
    try:
        r = requests.get(f"{BEETS_API}/api/library/status", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        # Adjust based on your API response format
        return data.get("importing", False)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            # Endpoint might not exist, assume not importing
            logging.warning("Status endpoint not available, assuming import not running")
            return False
        raise
    except Exception as e:
        logging.warning(f"Could not check import status: {e}")
        return False

# -----------------------------
# TRIGGER IMPORT
# -----------------------------
def trigger_import():
    """
    Calls your FastAPI endpoint:
        POST /api/library/import
    This runs:
        beet import -A /music/inbox
    inside the container.
    """
    try:
        r = requests.post(f"{BEETS_API}/api/library/import", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        response_data = r.json()
        logging.info(f"Import triggered: {response_data}")
        return True
    except requests.exceptions.Timeout:
        logging.error("Import request timed out (this is expected for long imports)")
        logging.info("Import may still be running in background")
        return True  # Assume it started
    except Exception as e:
        logging.error(f"Failed to trigger import: {e}")
        return False

# -----------------------------
# WAIT FOR IMPORT COMPLETION
# -----------------------------
def wait_for_import_completion():
    """
    Poll the API to check if import is complete.
    Falls back to fixed wait time if status endpoint unavailable.
    """
    logging.info("Waiting for import to complete...")
    
    elapsed = 0
    while elapsed < MAX_WAIT_TIME:
        try:
            if not check_import_status():
                logging.info(f"Import completed after {elapsed} seconds")
                return True
            
            logging.info(f"Import still running... ({elapsed}s elapsed)")
            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            
        except Exception as e:
            logging.warning(f"Error checking status, falling back to fixed wait: {e}")
            remaining = IMPORT_WAIT - elapsed
            if remaining > 0:
                logging.info(f"Waiting {remaining}s more...")
                time.sleep(remaining)
            return True
    
    logging.warning(f"Import did not complete within {MAX_WAIT_TIME}s")
    return False

# -----------------------------
# CLEANUP EMPTY DIRECTORIES
# -----------------------------
def cleanup_empty_dirs(directory):
    """Remove empty directories recursively"""
    removed = 0
    for root, dirs, files in os.walk(directory, topdown=False):
        for d in dirs:
            dir_path = Path(root) / d
            try:
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
                    logging.info(f"Removed empty directory: {dir_path}")
                    removed += 1
            except Exception as e:
                logging.debug(f"Could not remove {dir_path}: {e}")
    return removed

# -----------------------------
# QUARANTINE LEFTOVER FILES
# -----------------------------
def quarantine_leftovers():
    """Move any remaining audio files to quarantine"""
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    moved = 0
    errors = 0
    
    for root, dirs, files in os.walk(INBOX):
        for f in files:
            if not f.lower().endswith(AUDIO_EXTENSIONS):
                continue
            
            src = Path(root) / f
            dst = QUARANTINE / f
            
            # Handle filename conflicts
            if dst.exists():
                stem, ext = os.path.splitext(f)
                dst = QUARANTINE / f"{stem}_{int(time.time())}{ext}"
            
            try:
                src.rename(dst)
                logging.info(f"Quarantined leftover: {src} -> {dst}")
                moved += 1
            except Exception as e:
                logging.error(f"Failed to quarantine {src}: {e}")
                errors += 1
    
    # Cleanup empty directories
    if moved > 0:
        removed = cleanup_empty_dirs(INBOX)
        logging.info(f"Removed {removed} empty directories from inbox")
    
    logging.info(f"Quarantine complete: moved {moved} files, {errors} errors")
    return moved, errors

# -----------------------------
# MAIN
# -----------------------------
def main():
    logging.info("=" * 60)
    logging.info("Starting Beets rebuild process (bulk import via FastAPI)")
    logging.info(f"Beets API: {BEETS_API}")
    logging.info(f"Inbox: {INBOX}")
    logging.info(f"Quarantine: {QUARANTINE}")
    logging.info("=" * 60)
    
    # Validate inbox exists
    if not INBOX.exists():
        logging.error(f"Inbox directory does not exist: {INBOX}")
        sys.exit(1)
    
    # Count files before import
    initial_count = count_audio_files(INBOX)
    logging.info(f"Found {initial_count} audio files in inbox")
    
    if initial_count == 0:
        logging.info("No audio files to import, exiting")
        return
    
    # Check API availability
    try:
        r = requests.get(f"{BEETS_API}/api/stats", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        stats = r.json()
        logging.info(f"Beets API is reachable - Library stats: {stats}")
    except Exception as e:
        logging.error(f"Cannot reach Beets API at {BEETS_API}: {e}")
        sys.exit(1)
    
    # Check if import already running
    if check_import_status():
        logging.warning("An import is already running!")
        response = input("Continue anyway? (y/N): ").strip().lower()
        if response != 'y':
            logging.info("Aborting")
            sys.exit(0)
    
    # Trigger import
    if not trigger_import():
        logging.error("Failed to trigger import, aborting")
        sys.exit(1)
    
    # Wait for completion
    wait_for_import_completion()
    
    # Count remaining files
    remaining_count = count_audio_files(INBOX)
    imported_count = initial_count - remaining_count
    
    logging.info(f"Import statistics:")
    logging.info(f"  Initial files: {initial_count}")
    logging.info(f"  Remaining files: {remaining_count}")
    logging.info(f"  Likely imported: {imported_count}")
    
    # Move leftovers to quarantine
    if remaining_count > 0:
        logging.info(f"Quarantining {remaining_count} leftover files...")
        moved, errors = quarantine_leftovers()
        
        # Final verification
        final_count = count_audio_files(INBOX)
        if final_count > 0:
            logging.warning(f"WARNING: {final_count} files still remain in inbox!")
    else:
        logging.info("All files were imported successfully!")
    
    # Final stats
    try:
        r = requests.get(f"{BEETS_API}/api/stats", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        final_stats = r.json()
        logging.info(f"Final library stats: {final_stats}")
    except Exception as e:
        logging.warning(f"Could not retrieve final stats: {e}")
    
    logging.info("=" * 60)
    logging.info("Beets rebuild complete")
    logging.info("=" * 60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Process interrupted by user")
        sys.exit(130)
    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)