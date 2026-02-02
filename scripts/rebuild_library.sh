#!/bin/bash
set -euo pipefail

# === CONFIG ===
OLD_LIB="/music/library"
NEW_LIB="/music/library_clean"
NEW_DB="/data/beets-library-rebuild.blb"
REBUILD_CONFIG="/config/rebuild.yaml"
LOG="/data/rebuild_import.log"

echo "=== Starting full library rebuild: $(date) ===" | tee -a "$LOG"
echo "Old library: $OLD_LIB" | tee -a "$LOG"
echo "New library: $NEW_LIB" | tee -a "$LOG"
echo "New DB: $NEW_DB" | tee -a "$LOG"
echo "Log: $LOG" | tee -a "$LOG"
echo "" | tee -a "$LOG"

# === Validation ===
echo "Validating prerequisites..." | tee -a "$LOG"

# Check if beets is installed
if ! command -v beet &> /dev/null; then
    echo "ERROR: beet command not found. Is Beets installed?" | tee -a "$LOG"
    exit 1
fi

# Check if old library exists
if [[ ! -d "$OLD_LIB" ]]; then
    echo "ERROR: Old library not found at $OLD_LIB" | tee -a "$LOG"
    exit 1
fi

# Check if new library already exists and warn
if [[ -d "$NEW_LIB" ]] && [[ -n "$(ls -A "$NEW_LIB" 2>/dev/null)" ]]; then
    echo "WARNING: $NEW_LIB already exists and is not empty!" | tee -a "$LOG"
    read -p "Do you want to continue? This may overwrite existing files (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborting." | tee -a "$LOG"
        exit 1
    fi
fi

# Check if new DB already exists and warn
if [[ -f "$NEW_DB" ]]; then
    echo "WARNING: $NEW_DB already exists!" | tee -a "$LOG"
    read -p "Do you want to overwrite it? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Backing up existing DB..." | tee -a "$LOG"
        mv "$NEW_DB" "$NEW_DB.backup.$(date +%Y%m%d_%H%M%S)"
    else
        echo "Aborting." | tee -a "$LOG"
        exit 1
    fi
fi

echo "Validation complete." | tee -a "$LOG"
echo "" | tee -a "$LOG"

# === 1. Create clean library folder ===
echo "Creating clean library folder..." | tee -a "$LOG"
mkdir -p "$NEW_LIB"
chmod 755 "$NEW_LIB"
echo "Clean library folder created." | tee -a "$LOG"
echo "" | tee -a "$LOG"

# === 2. Create rebuild config ===
echo "Writing rebuild config to $REBUILD_CONFIG..." | tee -a "$LOG"
cat > "$REBUILD_CONFIG" <<'EOF'
directory: /music/library_clean
library: /data/beets-library-rebuild.blb

import:
  move: no
  copy: yes
  write: yes
  autotag: yes
  timid: no
  log: /data/rebuild_import.log

paths:
  default: $albumartist/$album/$track - $title
  singleton: Non-Album/$artist/$title
  comp: Compilations/$album/$track - $title

musicbrainz:
  enabled: yes

plugins: mbsync scrub

EOF

# Note: Using 'EOF' (quoted) prevents variable expansion in the heredoc
# Then replace the hardcoded paths with variables
sed -i "s|/music/library_clean|$NEW_LIB|g" "$REBUILD_CONFIG"
sed -i "s|/data/beets-library-rebuild.blb|$NEW_DB|g" "$REBUILD_CONFIG"
sed -i "s|/data/rebuild_import.log|$LOG|g" "$REBUILD_CONFIG"

echo "Rebuild config created." | tee -a "$LOG"
echo "Config contents:" | tee -a "$LOG"
cat "$REBUILD_CONFIG" | tee -a "$LOG"
echo "" | tee -a "$LOG"

# === 3. Run the import ===
echo "Starting metadata-based import..." | tee -a "$LOG"
echo "This may take a while depending on library size..." | tee -a "$LOG"

if ! beet -c "$REBUILD_CONFIG" import -A "$OLD_LIB" 2>&1 | tee -a "$LOG"; then
    echo "ERROR: Import failed. Check log at $LOG" | tee -a "$LOG"
    exit 1
fi

echo "" | tee -a "$LOG"
echo "Import complete." | tee -a "$LOG"
echo "" | tee -a "$LOG"

# === 4. Post-import cleanup ===
echo "Running post-import maintenance..." | tee -a "$LOG"

echo "Running update..." | tee -a "$LOG"
beet -c "$REBUILD_CONFIG" update 2>&1 | tee -a "$LOG"

echo "Running mbsync..." | tee -a "$LOG"
beet -c "$REBUILD_CONFIG" mbsync 2>&1 | tee -a "$LOG"

echo "Running scrub..." | tee -a "$LOG"
beet -c "$REBUILD_CONFIG" scrub 2>&1 | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo "VACUUM new DB..." | tee -a "$LOG"
if ! sqlite3 "$NEW_DB" "VACUUM;" 2>&1 | tee -a "$LOG"; then
    echo "WARNING: VACUUM failed, but continuing..." | tee -a "$LOG"
fi

echo "" | tee -a "$LOG"

# === 5. Summary ===
echo "=== Rebuild finished: $(date) ===" | tee -a "$LOG"
echo "" | tee -a "$LOG"
echo "Summary:" | tee -a "$LOG"
echo "  New library: $NEW_LIB" | tee -a "$LOG"
echo "  New DB: $NEW_DB" | tee -a "$LOG"
echo "  Log file: $LOG" | tee -a "$LOG"
echo "" | tee -a "$LOG"

# Count files
old_count=$(find "$OLD_LIB" -type f -name "*.mp3" -o -name "*.flac" -o -name "*.m4a" 2>/dev/null | wc -l)
new_count=$(find "$NEW_LIB" -type f -name "*.mp3" -o -name "*.flac" -o -name "*.m4a" 2>/dev/null | wc -l)

echo "File count comparison:" | tee -a "$LOG"
echo "  Old library: $old_count files" | tee -a "$LOG"
echo "  New library: $new_count files" | tee -a "$LOG"

if (( new_count < old_count )); then
    echo "  WARNING: New library has fewer files than old library!" | tee -a "$LOG"
else
    echo "  ✓ File counts match or exceed original" | tee -a "$LOG"
fi

echo "" | tee -a "$LOG"
echo "Next steps:" | tee -a "$LOG"
echo "  1. Verify the new library at $NEW_LIB" | tee -a "$LOG"
echo "  2. If satisfied, update your main Beets config to point to:" | tee -a "$LOG"
echo "     directory: $NEW_LIB" | tee -a "$LOG"
echo "     library: $NEW_DB" | tee -a "$LOG"
echo "  3. Consider backing up $OLD_LIB before removing it" | tee -a "$LOG"