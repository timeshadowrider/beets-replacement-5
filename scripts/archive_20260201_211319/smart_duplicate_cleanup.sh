#!/bin/bash
set -euo pipefail

MUSIC_ROOT="/music/library"
DB="/app/data/beets-library.blb"
LOG="/app/data/duplicate_cleanup.log"

echo "=== Smart Duplicate Cleanup Started: $(date) ===" | tee -a "$LOG"

# Normalize folder names to detect true duplicates
normalize() {
    local result
    result=$(echo "$1" \
        | tr '[:upper:]' '[:lower:]' \
        | sed -E 's/\[[^]]*\]$//' \
        | sed -E 's/\([^)]*\)$//' \
        | sed -E 's/[^a-z0-9]+/ /g' \
        | sed -E 's/ +/ /g' \
        | sed -E 's/^ //; s/ $//')
    
    # If result is empty, fall back to original (lowercased, basic cleanup)
    if [[ -z "$result" ]]; then
        result=$(echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
    fi
    
    echo "$result"
}

# Generate a fingerprint of the tracklist to detect identical albums
get_track_fingerprint() {
    find "$1" -type f -iregex '.*\.\(flac\|mp3\|m4a\|aac\)' \
        | sort \
        | sed -E 's/.*\///' \
        | tr '[:upper:]' '[:lower:]' \
        | sed -E 's/[^a-z0-9]+/ /g' \
        | sed -E 's/ +/ /g' \
        | sed -E 's/^ //; s/ $//' \
        | md5sum | awk '{print $1}'
}

declare -A groups

# Collect album folders grouped by normalized name
while IFS= read -r -d '' dir; do
    base=$(basename "$dir")
    norm=$(normalize "$base")
    
    # Skip if normalization resulted in empty string
    if [[ -z "$norm" ]]; then
        echo "Warning: Skipping directory with empty normalized name: $dir" | tee -a "$LOG"
        continue
    fi
    
    groups["$norm"]+="$dir"$'\n'
done < <(find "$MUSIC_ROOT" -mindepth 2 -maxdepth 2 -type d -print0)

# Process each group
for norm in "${!groups[@]}"; do
    IFS=$'\n' read -r -d '' -a dirs <<< "${groups[$norm]}" || true

    if (( ${#dirs[@]} > 1 )); then
        echo "" | tee -a "$LOG"
        echo "Checking group: '$norm'" | tee -a "$LOG"
        printf '  %s\n' "${dirs[@]}" | tee -a "$LOG"

        declare -A fingerprints

        # Compute fingerprints for each folder
        for d in "${dirs[@]}"; do
            fp=$(get_track_fingerprint "$d")
            fingerprints["$d"]="$fp"
        done

        # Count unique fingerprints
        unique_fps=($(printf "%s\n" "${fingerprints[@]}" | sort -u))

        # If all fingerprints match → true duplicates
        if (( ${#unique_fps[@]} == 1 )); then
            keep="${dirs[0]}"
            echo "  → True duplicates detected (identical tracklist)" | tee -a "$LOG"
            echo "  → Keeping: $keep" | tee -a "$LOG"
            
            for d in "${dirs[@]}"; do
                if [[ "$d" != "$keep" ]]; then
                    echo "  → Removing: $d" | tee -a "$LOG"
                    rm -rf "$d"
                fi
            done
        else
            echo "  → Different tracklists - keeping all" | tee -a "$LOG"
        fi
        
        unset fingerprints
    fi
done

echo "" | tee -a "$LOG"
echo "=== Smart Duplicate Cleanup Completed: $(date) ===" | tee -a "$LOG"
