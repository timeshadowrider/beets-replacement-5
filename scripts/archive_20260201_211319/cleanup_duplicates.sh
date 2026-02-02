#!/bin/bash
set -euo pipefail

MUSIC_ROOT="/music/library"
DB="/app/data/beets-library.blb"
LOG="/app/data/duplicate_cleanup.log"

echo "=== Duplicate Cleanup Started: $(date) ===" | tee -a "$LOG"

# 1. Find duplicate folders like "Album [1032]"
mapfile -t DUPES < <(find "$MUSIC_ROOT" -maxdepth 3 -type d -regex ".* 

\[[0-9]+\]

$")

if [[ ${#DUPES[@]} -eq 0 ]]; then
    echo "No duplicate folders found." | tee -a "$LOG"
    exit 0
fi

echo "Found ${#DUPES[@]} duplicate folders:" | tee -a "$LOG"
printf '%s\n' "${DUPES[@]}" | tee -a "$LOG"

# 2. Extract numeric IDs from folder names
IDS=()
for d in "${DUPES[@]}"; do
    ID=$(echo "$d" | sed -E 's/.*

\[([0-9]+)\]

$/\1/')
    IDS+=("$ID")
done

echo "Duplicate Beets album IDs: ${IDS[*]}" | tee -a "$LOG"

# 3. Delete duplicate folders
echo "Deleting duplicate folders..." | tee -a "$LOG"
for d in "${DUPES[@]}"; do
    echo "Removing: $d" | tee -a "$LOG"
    rm -rf "$d"
done

# 4. Delete DB rows
echo "Cleaning Beets database..." | tee -a "$LOG"

sqlite3 "$DB" <<EOF
BEGIN;
DELETE FROM items WHERE album_id IN (${IDS[*]// /,});
DELETE FROM albums WHERE id IN (${IDS[*]// /,});
COMMIT;
VACUUM;
EOF

echo "Database cleanup complete." | tee -a "$LOG"

echo "=== Duplicate Cleanup Finished: $(date) ===" | tee -a "$LOG"
