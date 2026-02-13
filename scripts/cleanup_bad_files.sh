#!/bin/bash
# ============================================================================
# CLEANUP EXISTING BAD FILES IN LIBRARY
# ============================================================================
# Removes files from library that have:
# - Filenames starting with 00-XX (bad disc/track numbers)
# - Multiple duplicate extensions (.1.flac, .2.flac, etc.)
# Then updates the beets database
# ============================================================================

LIBRARY_PATH="/music/library"
QUARANTINE_PATH="/music/quarantine/bad_metadata"
LOG_FILE="/tmp/library_cleanup_$(date +%Y%m%d-%H%M%S).log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "=============================================="
echo "Library Cleanup - Bad Metadata Files"
echo "$(date)"
echo "=============================================="
echo "" | tee "$LOG_FILE"

# Ensure quarantine exists
mkdir -p "$QUARANTINE_PATH"

REMOVED=0
QUARANTINED=0

# ============================================================================
# 1. FIND AND REMOVE FILES WITH 00-XX PREFIX
# ============================================================================

echo -e "${BLUE}[1/4] Finding files with 00-XX prefix...${NC}" | tee -a "$LOG_FILE"

# Find all files starting with 00-
find "$LIBRARY_PATH" -type f -name "00-*.flac" -o -name "00-*.mp3" -o -name "00-*.m4a" | while read file; do
    echo -e "${YELLOW}Found bad file:${NC} $(basename "$file")" | tee -a "$LOG_FILE"
    
    # Move to quarantine
    filename=$(basename "$file")
    mv "$file" "$QUARANTINE_PATH/$filename" 2>/dev/null
    
    if [ $? -eq 0 ]; then
        ((QUARANTINED++))
        echo -e "${RED}  -> Quarantined${NC}" | tee -a "$LOG_FILE"
    fi
done

echo -e "${GREEN}Quarantined files with 00- prefix${NC}" | tee -a "$LOG_FILE"
echo ""

# ============================================================================
# 2. FIND AND REMOVE DUPLICATE EXTENSION FILES
# ============================================================================

echo -e "${BLUE}[2/4] Finding duplicate extension files (.1.flac, .2.flac, etc.)...${NC}" | tee -a "$LOG_FILE"

# Find files with .N.extension pattern
find "$LIBRARY_PATH" -type f -regextype posix-extended -regex '.*\.[0-9]+\.(flac|mp3|m4a|ogg|wav)$' | while read file; do
    echo -e "${YELLOW}Found duplicate:${NC} $(basename "$file")" | tee -a "$LOG_FILE"
    
    # Just delete these - they're duplicates created by failed moves
    rm "$file" 2>/dev/null
    
    if [ $? -eq 0 ]; then
        ((REMOVED++))
        echo -e "${RED}  -> Removed${NC}" | tee -a "$LOG_FILE"
    fi
done

echo -e "${GREEN}Removed duplicate extension files${NC}" | tee -a "$LOG_FILE"
echo ""

# ============================================================================
# 3. FIND AND HANDLE [0000] AND [0001] YEAR FOLDERS
# ============================================================================

echo -e "${BLUE}[3/4] Finding albums with year [0000] or [0001]...${NC}" | tee -a "$LOG_FILE"

# Find directories with [0000] or [0001] in the name
find "$LIBRARY_PATH" -type d \( -name "*[0000]*" -o -name "*[0001]*" \) | while read dir; do
    if [ -d "$dir" ]; then
        echo -e "${YELLOW}Found bad year folder:${NC} $(basename "$dir")" | tee -a "$LOG_FILE"
        
        # Remove entire album directory
        rm -rf "$dir"
        
        if [ $? -eq 0 ]; then
            ((REMOVED++))
            echo -e "${RED}  -> Removed directory${NC}" | tee -a "$LOG_FILE"
        fi
    fi
done

echo -e "${GREEN}Removed bad year folders${NC}" | tee -a "$LOG_FILE"
echo ""

# ============================================================================
# 4. CLEANUP EMPTY DIRECTORIES
# ============================================================================

echo -e "${BLUE}[4/4] Cleaning up empty directories...${NC}" | tee -a "$LOG_FILE"

find "$LIBRARY_PATH" -type d -empty -delete 2>/dev/null

echo -e "${GREEN}Empty directories removed${NC}" | tee -a "$LOG_FILE"
echo ""

# ============================================================================
# 5. UPDATE BEETS DATABASE
# ============================================================================

echo -e "${BLUE}[5/5] Updating beets database...${NC}" | tee -a "$LOG_FILE"

# Remove orphaned entries from database
beet update -a 2>&1 | tee -a "$LOG_FILE"

echo ""
echo -e "${GREEN}Database updated${NC}" | tee -a "$LOG_FILE"
echo ""

# ============================================================================
# SUMMARY
# ============================================================================

echo -e "${GREEN}=============================================="
echo "Cleanup Complete"
echo "==============================================${NC}"
echo ""
echo "Files quarantined: $QUARANTINED"
echo "Files/folders removed: $REMOVED"
echo ""
echo "Quarantine location: $QUARANTINE_PATH"
echo "Log file: $LOG_FILE"
echo ""

if [ $((QUARANTINED + REMOVED)) -gt 0 ]; then
    echo -e "${YELLOW}??  Run 'beet import /inbox' to re-import clean files${NC}"
    echo -e "${YELLOW}??  Review quarantined files in: $QUARANTINE_PATH${NC}"
else
    echo -e "${GREEN}? No bad files found - library is clean!${NC}"
fi

echo ""
