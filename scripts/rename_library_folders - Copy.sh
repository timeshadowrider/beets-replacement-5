#!/bin/bash
set -euo pipefail

MUSIC_ROOT="/music/library"
DB="/app/data/beets-library.blb"
LOG="/app/data/rename_cleanup.log"

echo "=== Library Rename Started: $(date) ===" | tee -a "$LOG"

# Query Beets for REAL album metadata
get_album_metadata() {
    local path="$1"
    local escaped="${path//\'/\'\'}"
    sqlite3 -cmd ".timeout 10000" "$DB" "
        SELECT
            albums.album,
            albums.albumdisambig
        FROM albums
        JOIN items ON items.album_id = albums.id
        WHERE items.path LIKE '${escaped}/%'
        LIMIT 1;
    " 2>/dev/null || echo "|"
}

# Clean whitespace
trim() {
    echo "$1" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//'
}

# Remove true duplicates inside an album folder
cleanup_duplicates_in_folder() {
    local folder="$1"
    echo "  → Checking for duplicates in: $folder" | tee -a "$LOG"
    
    # Build a map of track identity → best file
    declare -A best_files
    declare -A best_bitrates
    
    local processed=0
    
    shopt -s nullglob
    for f in "$folder"/*; do
        [[ -f "$f" ]] || continue
        
        ((processed++))
        if (( processed % 5 == 0 )); then
            echo "      Processed $processed files..." | tee -a "$LOG"
        fi
        
        # Extract metadata using ffprobe with timeout (or fallback to filename)
        local title=$(timeout 3 ffprobe -v quiet -show_entries format_tags=title -of default=nw=1:nk=1 "$f" 2>/dev/null || echo "")
        local track=$(timeout 3 ffprobe -v quiet -show_entries format_tags=track -of default=nw=1:nk=1 "$f" 2>/dev/null || echo "")
        local bitrate=$(timeout 3 ffprobe -v quiet -show_entries format=bit_rate -of default=nw=1:nk=1 "$f" 2>/dev/null || echo 0)
        
        # Fallbacks if metadata missing
        [[ -z "$title" ]] && title=$(basename "$f")
        [[ -z "$track" ]] && track="0"
        
        # Identity key: track + title
        local key="${track}_${title}"
        
        # If no best file yet, store this one
        if [[ -z "${best_files[$key]+x}" ]]; then
            best_files[$key]="$f"
            best_bitrates[$key]="$bitrate"
            continue
        fi
        
        # Compare bitrates
        if (( bitrate > best_bitrates[$key] )); then
            # New file is better → delete old one
            echo "    → Removing lower-quality duplicate: ${best_files[$key]}" | tee -a "$LOG"
            rm -f "${best_files[$key]}"
            best_files[$key]="$f"
            best_bitrates[$key]="$bitrate"
        else
            # Old file is better → delete new one
            echo "    → Removing lower-quality duplicate: $f" | tee -a "$LOG"
            rm -f "$f"
        fi
    done
    shopt -u nullglob
    
    echo "      Total files processed: $processed" | tee -a "$LOG"
}

# Remove duplicate album folders under the same artist
cleanup_duplicate_albums() {
    local artist_folder="$1"
    echo "→ Checking for duplicate albums in artist folder: $artist_folder" | tee -a "$LOG"
    
    declare -A album_groups
    
    # Group album folders by normalized album name
    shopt -s nullglob
    for album_dir in "$artist_folder"/*; do
        [[ -d "$album_dir" ]] || continue
        
        local base=$(basename "$album_dir")
        
        # Normalize: remove editions, case-insensitive, trim spaces
        local normalized=$(echo "$base" | sed -E 's/\([^)]*\)$//' | sed -E 's/\[[^]]*\]$//' | tr '[:upper:]' '[:lower:]' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')
        
        # Skip if normalization resulted in empty string
        if [[ -z "$normalized" ]]; then
            echo "  → Warning: Skipping folder with empty normalized name: $album_dir" | tee -a "$LOG"
            continue
        fi
        
        # Add to group
        album_groups["$normalized"]+="$album_dir"$'\n'
    done
    shopt -u nullglob
    
    # Process each group
    for norm in "${!album_groups[@]}"; do
        IFS=$'\n' read -r -d '' -a dirs <<< "${album_groups[$norm]}" || true
        
        if (( ${#dirs[@]} > 1 )); then
            echo "  → Found ${#dirs[@]} potential duplicates for: '$norm'" | tee -a "$LOG"
            printf '    %s\n' "${dirs[@]}" | tee -a "$LOG"
            
            declare -A folder_file_counts
            declare -A folder_fingerprints
            declare -A folder_bitrates
            
            # First, get simple file counts
            for d in "${dirs[@]}"; do
                local count=$(find "$d" -type f | wc -l)
                folder_file_counts["$d"]=$count
            done
            
            # If file counts don't match, they're different albums
            local first_count="${folder_file_counts[${dirs[0]}]}"
            local counts_match=true
            
            for d in "${dirs[@]}"; do
                if [[ "${folder_file_counts[$d]}" != "$first_count" ]]; then
                    counts_match=false
                    break
                fi
            done
            
            if [[ "$counts_match" == false ]]; then
                echo "    → Different file counts - keeping all" | tee -a "$LOG"
                for d in "${dirs[@]}"; do
                    echo "      $d: ${folder_file_counts[$d]} files" | tee -a "$LOG"
                done
                unset folder_file_counts
                unset folder_fingerprints
                unset folder_bitrates
                continue
            fi
            
            echo "    → Same file count ($first_count), comparing tracklists..." | tee -a "$LOG"
            
            # Calculate fingerprint and total bitrate for each folder
            for d in "${dirs[@]}"; do
                echo "    → Analyzing: $d" | tee -a "$LOG"
                
                local fingerprint=""
                local total_bitrate=0
                local file_count=0
                
                shopt -s nullglob
                for f in "$d"/*; do
                    [[ -f "$f" ]] || continue
                    
                    ((file_count++))
                    echo "      Processing file $file_count/$first_count..." | tee -a "$LOG"
                    
                    # Use timeout to prevent hanging - reduced to 3 seconds
                    local title=$(timeout 3 ffprobe -v quiet -show_entries format_tags=title -of default=nw=1:nk=1 "$f" 2>/dev/null || echo "")
                    local track=$(timeout 3 ffprobe -v quiet -show_entries format_tags=track -of default=nw=1:nk=1 "$f" 2>/dev/null || echo "")
                    local bitrate=$(timeout 3 ffprobe -v quiet -show_entries format=bit_rate -of default=nw=1:nk=1 "$f" 2>/dev/null || echo 0)
                    
                    [[ -z "$title" ]] && title=$(basename "$f")
                    [[ -z "$track" ]] && track="0"
                    
                    fingerprint+="${track}_${title}|"
                    total_bitrate=$((total_bitrate + bitrate))
                done
                shopt -u nullglob
                
                folder_fingerprints["$d"]="$fingerprint"
                folder_bitrates["$d"]="$total_bitrate"
                
                echo "      Complete: $file_count files, Total bitrate: $total_bitrate" | tee -a "$LOG"
            done
            
            # Check if all fingerprints match (true duplicates)
            local first_fingerprint="${folder_fingerprints[${dirs[0]}]}"
            local all_match=true
            
            for d in "${dirs[@]}"; do
                if [[ "${folder_fingerprints[$d]}" != "$first_fingerprint" ]]; then
                    all_match=false
                    break
                fi
            done
            
            if [[ "$all_match" == true ]]; then
                echo "    → TRUE DUPLICATES - Same tracklist detected" | tee -a "$LOG"
                
                # Find the folder with highest total bitrate
                local best_folder=""
                local best_bitrate=0
                
                for d in "${dirs[@]}"; do
                    if (( ${folder_bitrates[$d]} > best_bitrate )); then
                        best_bitrate=${folder_bitrates[$d]}
                        best_folder="$d"
                    fi
                done
                
                echo "    → Keeping highest quality: $best_folder (bitrate: $best_bitrate)" | tee -a "$LOG"
                
                # Delete all others
                for d in "${dirs[@]}"; do
                    if [[ "$d" != "$best_folder" ]]; then
                        echo "    → Deleting duplicate: $d" | tee -a "$LOG"
                        rm -rf "$d"
                    fi
                done
            else
                echo "    → Different tracklists - keeping all" | tee -a "$LOG"
            fi
            
            unset folder_file_counts
            unset folder_fingerprints
            unset folder_bitrates
        fi
    done
}

rename_folder() {
    local old="$1"
    local base=$(basename "$old")
    local parent=$(dirname "$old")
    
    echo "Processing: $old" | tee -a "$LOG"
    
    # Clean up duplicates in the folder BEFORE renaming
    cleanup_duplicates_in_folder "$old"
    
    # Pull REAL metadata from Beets
    IFS='|' read -r album edition <<< "$(get_album_metadata "$old")"
    
    # If Beets has no metadata, skip
    if [[ -z "$album" ]]; then
        echo "  → No metadata found, skipping" | tee -a "$LOG"
        return
    fi
    
    album=$(trim "$album")
    edition=$(trim "$edition")
    
    # Build new folder name
    local new="$album"
    if [[ -n "$edition" ]]; then
        new="$album ($edition)"
    fi
    
    local new_path="$parent/$new"
    
    # Skip if unchanged
    if [[ "$old" == "$new_path" ]]; then
        echo "  → Already correct" | tee -a "$LOG"
        return
    fi
    
    # Check if destination already exists
    if [[ -e "$new_path" && "$old" != "$new_path" ]]; then
        echo "  → ERROR: Destination already exists: $new_path" | tee -a "$LOG"
        return
    fi
    
    echo "Renaming:" | tee -a "$LOG"
    echo "  OLD: $old" | tee -a "$LOG"
    echo "  NEW: $new_path" | tee -a "$LOG"
    
    # Perform rename
    if ! mv "$old" "$new_path" 2>&1 | tee -a "$LOG"; then
        echo "  → ERROR: Failed to rename" | tee -a "$LOG"
        return
    fi
    
    # Update DB paths
    local escaped_old="${old//\'/\'\'}"
    local escaped_new="${new_path//\'/\'\'}"
    if ! sqlite3 -cmd ".timeout 10000" "$DB" "
        UPDATE items
        SET path = REPLACE(path, '${escaped_old}/', '${escaped_new}/')
        WHERE path LIKE '${escaped_old}/%';
    " 2>&1 | tee -a "$LOG"; then
        echo "  → ERROR: Failed to update database" | tee -a "$LOG"
        return
    fi
    
    echo "  → Complete" | tee -a "$LOG"
}

# Gather artist folders
echo "Gathering artist folders..." | tee -a "$LOG"
mapfile -d '' artist_folders < <(find "$MUSIC_ROOT" -mindepth 1 -maxdepth 1 -type d -print0)
total_artists=${#artist_folders[@]}

echo "Found $total_artists artist folders" | tee -a "$LOG"
echo "" | tee -a "$LOG"

# First pass: Clean up duplicate albums within each artist folder
echo "=== Phase 1: Cleaning duplicate albums ===" | tee -a "$LOG"
for i in "${!artist_folders[@]}"; do
    echo "[Artist $((i+1))/$total_artists]" | tee -a "$LOG"
    cleanup_duplicate_albums "${artist_folders[$i]}"
    echo "" | tee -a "$LOG"
done

# Second pass: Rename remaining album folders
echo "=== Phase 2: Renaming album folders ===" | tee -a "$LOG"

# Re-gather album folders after cleanup
mapfile -d '' folders < <(find "$MUSIC_ROOT" -mindepth 2 -maxdepth 2 -type d -print0)
total=${#folders[@]}

echo "Found $total album folders" | tee -a "$LOG"
echo "" | tee -a "$LOG"

# Process each folder
for i in "${!folders[@]}"; do
    echo "[$((i+1))/$total]" | tee -a "$LOG"
    rename_folder "${folders[$i]}"
    echo "" | tee -a "$LOG"
done

echo "Running VACUUM on database..." | tee -a "$LOG"
sqlite3 -cmd ".timeout 10000" "$DB" "VACUUM;" 2>&1 | tee -a "$LOG"

echo "=== Library Rename Finished: $(date) ===" | tee -a "$LOG"