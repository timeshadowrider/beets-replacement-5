#!/bin/bash
# Patch app.py to fix LibraryHandler duplicate issue

set -e

echo "=== Patching LibraryHandler to ignore temp files ==="

# Stop container
docker stop beets-single-5
sleep 2

# Backup original
docker cp beets-single-5:/app/backend/app.py /tmp/app.py.original
echo "? Backed up original to /tmp/app.py.original"

# Create the patched version
cat > /tmp/app.py.patched << 'PYTHON_EOF'
# This will be inserted to replace the LibraryHandler class

class LibraryHandler(FileSystemEventHandler):
    """Watch library for changes and trigger regeneration"""
    
    def on_created(self, event):
        if event.is_directory:
            return
        
        # Get the filename
        filename = Path(event.src_path).name
        
        # IGNORE temporary files and beets internal files
        ignore_patterns = [
            '.beets',           # Beets temp files during import
            '.tmp',             # Generic temp files  
            '.partial',         # Partial downloads
            '.download',        # Download in progress
            '~',                # Backup files
            '.DS_Store',        # macOS metadata
            'Thumbs.db',        # Windows metadata
            '.lock',            # Lock files
            '.rwc',             # Beets write cache files (like .rwculbpk.beets)
        ]
        
        # Check if filename should be ignored
        for pattern in ignore_patterns:
            if pattern in filename:
                return  # Silently ignore these files
        
        # Only trigger regeneration for actual audio files
        audio_extensions = ['.flac', '.mp3', '.m4a', '.ogg', '.opus', '.wav', '.wma', '.aac']
        if not any(filename.lower().endswith(ext) for ext in audio_extensions):
            return  # Ignore non-audio files
        
        # Valid audio file detected - queue regeneration
        with lib_lock:
            if str(LIBRARY_PATH) not in lib_queued:
                lib_queued.add(str(LIBRARY_PATH))
                lib_q.put(time.time())
                add_watcher_log("info", f"Library change detected: {filename}")
PYTHON_EOF

# Use Python to do the replacement properly
docker exec beets-single-5 python3 << 'EOF'
import re

# Read original
with open('/app/backend/app.py', 'r') as f:
    content = f.read()

# Find and replace the LibraryHandler class
# Pattern: from "class LibraryHandler" to the next class or major section
pattern = r'class LibraryHandler\(FileSystemEventHandler\):.*?(?=class\s+\w+|# -{10,}|def\s+inbox_worker)'

replacement = '''class LibraryHandler(FileSystemEventHandler):
    """Watch library for changes and trigger regeneration"""
    
    def on_created(self, event):
        if event.is_directory:
            return
        
        # Get the filename
        from pathlib import Path
        filename = Path(event.src_path).name
        
        # IGNORE temporary files and beets internal files
        ignore_patterns = [
            '.beets',           # Beets temp files during import
            '.tmp',             # Generic temp files
            '.partial',         # Partial downloads
            '.download',        # Download in progress
            '~',                # Backup files
            '.DS_Store',        # macOS metadata
            'Thumbs.db',        # Windows metadata
            '.lock',            # Lock files
            '.rwc',             # Beets write cache files (like .rwculbpk.beets)
        ]
        
        # Check if filename should be ignored
        for pattern in ignore_patterns:
            if pattern in filename:
                return  # Silently ignore these files
        
        # Only trigger regeneration for actual audio files
        audio_extensions = ['.flac', '.mp3', '.m4a', '.ogg', '.opus', '.wav', '.wma', '.aac']
        if not any(filename.lower().endswith(ext) for ext in audio_extensions):
            return  # Ignore non-audio files
        
        # Valid audio file detected - queue regeneration
        with lib_lock:
            if str(LIBRARY_PATH) not in lib_queued:
                lib_queued.add(str(LIBRARY_PATH))
                lib_q.put(time.time())
                add_watcher_log("info", f"Library change detected: {filename}")

'''

# Replace using re.DOTALL to match across newlines
new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

# Write back
with open('/app/backend/app.py', 'w') as f:
    f.write(new_content)

print("? LibraryHandler patched successfully")
EOF

# Restart container
echo ""
echo "Starting container with patched code..."
docker start beets-single-5
sleep 5

echo ""
echo "? Container restarted with fix"
echo ""
echo "=== Verification ==="
docker exec beets-single-5 grep -A 15 "class LibraryHandler" /app/backend/app.py | head -20

echo ""
echo "=== Next Steps ==="
echo "Monitor the logs to ensure .beets files are ignored:"
echo "  docker logs -f beets-single-5 | grep -i 'library change'"
echo ""
echo "Watch for duplicates (should stay at current count):"
echo "  watch 'docker exec beets-single-5 find /music/library -name \"*.*.flac\" | wc -l'"