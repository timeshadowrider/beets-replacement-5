#!/bin/bash
# ============================================================================
# PRE-IMPORT VALIDATION SCRIPT
# ============================================================================
# Scans files BEFORE import and fixes or quarantines files with:
# - disc = 00 or 0
# - track = 00 or 0
# - year = 0000 or 0001
# - missing artist/album metadata
# ============================================================================

INBOX_PATH="/inbox"
QUARANTINE_PATH="/music/quarantine"
LOG_FILE="/tmp/pre_import_validation.log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "=============================================="
echo "Pre-Import Validation"
echo "$(date)"
echo "=============================================="
echo "" | tee -a "$LOG_FILE"

# Ensure quarantine exists
mkdir -p "$QUARANTINE_PATH"

# Statistics
CHECKED=0
BAD_DISC=0
BAD_TRACK=0
BAD_YEAR=0
MISSING_META=0
QUARANTINED=0

# ============================================================================
# FUNCTION: Check if file has bad metadata
# ============================================================================
check_file() {
    local file="$1"
    local has_issues=0
    local issues=""
    
    # Get metadata using ffprobe
    local metadata=$(ffprobe -v quiet -print_format json -show_format "$file" 2>/dev/null)
    
    if [ -z "$metadata" ]; then
        return 0
    fi
    
    # Extract fields
    local artist=$(echo "$metadata" | jq -r '.format.tags.artist // .format.tags.ARTIST // empty' 2>/dev/null)
    local album=$(echo "$metadata" | jq -r '.format.tags.album // .format.tags.ALBUM // empty' 2>/dev/null)
    local track=$(echo "$metadata" | jq -r '.format.tags.track // .format.tags.TRACK // empty' 2>/dev/null)
    local disc=$(echo "$metadata" | jq -r '.format.tags.disc // .format.tags.DISC // empty' 2>/dev/null)
    local year=$(echo "$metadata" | jq -r '.format.tags.date // .format.tags.DATE // .format.tags.year // .format.tags.YEAR // empty' 2>/dev/null | cut -d'-' -f1)
    
    # Check disc number
    if [ ! -z "$disc" ]; then
        disc_num=$(echo "$disc" | cut -d'/' -f1)
        if [ "$disc_num" = "0" ] || [ "$disc_num" = "00" ]; then
            has_issues=1
            issues="${issues}disc=00 "
            ((BAD_DISC++))
        fi
    fi
    
    # Check track number
    if [ ! -z "$track" ]; then
        track_num=$(echo "$track" | cut -d'/' -f1)
        if [ "$track_num" = "0" ] || [ "$track_num" = "00" ]; then
            has_issues=1
            issues="${issues}track=00 "
            ((BAD_TRACK++))
        fi
    fi
    
    # Check year
    if [ ! -z "$year" ]; then
        if [ "$year" = "0" ] || [ "$year" = "0000" ] || [ "$year" = "0001" ]; then
            has_issues=1
            issues="${issues}year=$year "
            ((BAD_YEAR++))
        fi
    fi
    
    # Check missing metadata
    if [ -z "$artist" ] || [ -z "$album" ]; then
        has_issues=1
        issues="${issues}missing_meta "
        ((MISSING_META++))
    fi
    
    if [ $has_issues -eq 1 ]; then
        echo -e "${YELLOW}[BAD] $issues${NC}: $(basename "$file")" | tee -a "$LOG_FILE"
        return 1
    fi
    
    return 0
}

# ============================================================================
# FUNCTION: Quarantine file
# ============================================================================
quarantine_file() {
    local file="$1"
    local filename=$(basename "$file")
    local dest="$QUARANTINE_PATH/$filename"
    
    # Handle duplicates
    if [ -f "$dest" ]; then
        local base="${filename%.*}"
        local ext="${filename##*.}"
        local counter=1
        while [ -f "$dest" ]; do
            dest="$QUARANTINE_PATH/${base}.${counter}.${ext}"
            ((counter++))
        done
    fi
    
    # Move file
    mv "$file" "$dest" 2>/dev/null
    if [ $? -eq 0 ]; then
        echo -e "${RED}[QUARANTINE]${NC} $filename -> $(basename "$dest")" | tee -a "$LOG_FILE"
        ((QUARANTINED++))
        return 0
    else
        echo -e "${RED}[ERROR]${NC} Failed to quarantine: $filename" | tee -a "$LOG_FILE"
        return 1
    fi
}

# ============================================================================
# SCAN INBOX
# ============================================================================

echo -e "${BLUE}Scanning inbox for bad metadata...${NC}"
echo ""

# Find all audio files
AUDIO_EXTS="flac|mp3|m4a|ogg|wav|aac"

find "$INBOX_PATH" -type f -regextype posix-extended -regex ".*\.($AUDIO_EXTS)$" | while read file; do
    ((CHECKED++))
    
    # Progress indicator
    if [ $((CHECKED % 100)) -eq 0 ]; then
        echo -e "${BLUE}Checked $CHECKED files...${NC}"
    fi
    
    # Check file
    if ! check_file "$file"; then
        # Bad metadata - quarantine it
        quarantine_file "$file"
    fi
done

# ============================================================================
# CLEANUP EMPTY DIRECTORIES
# ============================================================================

echo ""
echo -e "${BLUE}Cleaning up empty directories...${NC}"

find "$INBOX_PATH" -type d -empty -delete 2>/dev/null

# ============================================================================
# SUMMARY
# ============================================================================

echo ""
echo -e "${GREEN}=============================================="
echo "Validation Complete"
echo "==============================================${NC}"
echo ""
echo "Files checked: $CHECKED"
echo ""
echo -e "${YELLOW}Issues found:${NC}"
echo "  Files with disc=00: $BAD_DISC"
echo "  Files with track=00: $BAD_TRACK"
echo "  Files with year=0000: $BAD_YEAR"
echo "  Files missing metadata: $MISSING_META"
echo ""
echo -e "${RED}Files quarantined: $QUARANTINED${NC}"
echo ""
echo "Log: $LOG_FILE"
echo ""

if [ $QUARANTINED -gt 0 ]; then
    echo -e "${YELLOW}??  $QUARANTINED files were quarantined${NC}"
    echo -e "${YELLOW}Review them in: $QUARANTINE_PATH${NC}"
else
    echo -e "${GREEN}? No bad files found - inbox is clean!${NC}"
fi

echo ""
