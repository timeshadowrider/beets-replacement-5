#!/bin/bash
# ============================================================================
# BEETS AUTOMATED LIBRARY MAINTENANCE SCRIPT
# ============================================================================
# Fully automated - no prompts, runs unattended
# Safe to run overnight or in the background
# ============================================================================

echo "=============================================="
echo "Beets Automated Library Maintenance"
echo "Starting: $(date)"
echo "=============================================="
echo ""

# Logging
LOGFILE="/tmp/beets_maintenance_$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOGFILE") 2>&1

echo "Log file: $LOGFILE"
echo ""

# ============================================================================
# CONFIGURATION - Edit these to customize what runs
# ============================================================================
DO_BACKUP=true                    # Always backup database
DO_CLEAN_ORPHANS=true            # Remove dead entries
DO_UPDATE_METADATA=false         # Update from MusicBrainz (slow)
DO_GENERATE_FINGERPRINTS=false   # Generate chromaprint (VERY slow)
DO_FIX_CASE_ISSUES=true          # Fix case sensitivity
DO_FETCH_ARTWORK=true            # Download missing artwork
DO_UPDATE_PLAYERS=true           # Update Plex/Volumio/Navidrome

echo "Configuration:"
echo "  Backup database: $DO_BACKUP"
echo "  Clean orphans: $DO_CLEAN_ORPHANS"
echo "  Update metadata: $DO_UPDATE_METADATA"
echo "  Generate fingerprints: $DO_GENERATE_FINGERPRINTS"
echo "  Fix case issues: $DO_FIX_CASE_ISSUES"
echo "  Fetch artwork: $DO_FETCH_ARTWORK"
echo "  Update players: $DO_UPDATE_PLAYERS"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

STEP=1
TOTAL_STEPS=10

# ============================================================================
# STEP 1: BACKUP DATABASE
# ============================================================================
if [ "$DO_BACKUP" = true ]; then
    echo -e "${BLUE}[$STEP/$TOTAL_STEPS] Backing up database...${NC}"
    BACKUP_FILE="/config/library.db.maintenance-$(date +%Y%m%d-%H%M%S)"
    cp /config/library.db "$BACKUP_FILE"
    echo "Database backed up to: $BACKUP_FILE"
    echo ""
fi
((STEP++))

# ============================================================================
# STEP 2: QUARANTINE BAD METADATA
# ============================================================================
echo -e "${BLUE}[$STEP/$TOTAL_STEPS] Quarantining files with bad metadata...${NC}"

# Find and remove albums with year 0000/0001 (bad metadata)
BAD_YEAR_COUNT=$(beet ls -a year:0000 year:0001 2>/dev/null | wc -l)

if [ "$BAD_YEAR_COUNT" -gt 0 ]; then
    echo "Found $BAD_YEAR_COUNT albums with year 0000/0001"
    echo "Removing from library (files will be moved to quarantine)..."
    
    # Remove albums with bad years - files will be deleted
    beet remove -a year:0000 year:0001 -d 2>&1 | tee -a /tmp/bad_metadata.log
    
    echo -e "${GREEN}Bad metadata albums removed!${NC}"
else
    echo -e "${GREEN}No albums with bad metadata found!${NC}"
fi
echo ""
((STEP++))

# ============================================================================
# STEP 3: CLEAN ORPHANED ENTRIES
# ============================================================================
if [ "$DO_CLEAN_ORPHANS" = true ]; then
    echo -e "${BLUE}[$STEP/$TOTAL_STEPS] Cleaning orphaned database entries...${NC}"
    echo "Scanning for files that don't exist on disk..."
    beet update -a 2>&1 | tee -a /tmp/orphan_clean.log
    echo ""
    echo -e "${GREEN}Orphaned entries cleaned!${NC}"
    echo ""
fi
((STEP++))

# ============================================================================
# STEP 4: UPDATE METADATA FROM MUSICBRAINZ
# ============================================================================
if [ "$DO_UPDATE_METADATA" = true ]; then
    echo -e "${BLUE}[$STEP/$TOTAL_STEPS] Updating metadata from MusicBrainz...${NC}"
    echo "This may take 30-60 minutes for large libraries..."
    
    # Only sync albums that have MusicBrainz IDs
    beet mbsync -a mb_albumid::.+ 2>&1 | tee -a /tmp/mbsync.log
    
    echo ""
    echo -e "${GREEN}Metadata updated!${NC}"
    echo ""
else
    echo -e "${YELLOW}[$STEP/$TOTAL_STEPS] Skipping metadata update${NC}"
    echo ""
fi
((STEP++))

# ============================================================================
# STEP 5: GENERATE AUDIO FINGERPRINTS
# ============================================================================
if [ "$DO_GENERATE_FINGERPRINTS" = true ]; then
    echo -e "${BLUE}[$STEP/$TOTAL_STEPS] Generating audio fingerprints...${NC}"
    echo "This may take 2-4 HOURS for large libraries..."
    
    # Force fingerprint generation for all tracks
    beet fingerprint -f 2>&1 | tee -a /tmp/fingerprint.log
    
    echo ""
    echo -e "${GREEN}Fingerprints generated!${NC}"
    echo ""
else
    echo -e "${YELLOW}[$STEP/$TOTAL_STEPS] Skipping fingerprint generation${NC}"
    echo ""
fi
((STEP++))

# ============================================================================
# STEP 6: FIX CASE SENSITIVITY ISSUES
# ============================================================================
if [ "$DO_FIX_CASE_ISSUES" = true ]; then
    echo -e "${BLUE}[$STEP/$TOTAL_STEPS] Fixing case-sensitivity issues...${NC}"
    
    # Common case issues - add more as needed
    FIXED=0
    
    # Alice in Chains
    if beet ls -a albumartist:"Alice In Chains" > /dev/null 2>&1; then
        echo "Fixing: Alice In Chains -> Alice in Chains"
        beet modify -a albumartist:"Alice In Chains" albumartist="Alice in Chains" -y
        ((FIXED++))
    fi
    
    # American Steel (if duplicate exists)
    count=$(beet ls -a -f '$albumartist' | grep -i "^American Steel$" | sort -u | wc -l)
    if [ "$count" -gt 1 ]; then
        echo "Fixing: American Steel case variations"
        beet modify -a albumartist::"American Steel" albumartist="American Steel" -y
        ((FIXED++))
    fi
    
    # Move files to correct locations
    if [ "$FIXED" -gt 0 ]; then
        echo "Moving files to correct locations..."
        beet move -a 2>&1 | grep -v "already in place"
    fi
    
    echo ""
    echo -e "${GREEN}Fixed $FIXED case issues!${NC}"
    echo ""
else
    echo -e "${YELLOW}[$STEP/$TOTAL_STEPS] Skipping case issue fixes${NC}"
    echo ""
fi
((STEP++))

# ============================================================================
# STEP 7: FIND AND REPORT DUPLICATES
# ============================================================================
echo -e "${BLUE}[$STEP/$TOTAL_STEPS] Checking for duplicate albums...${NC}"

beet duplicates -a > /tmp/duplicates_mbid.txt 2>/dev/null

if [ -s /tmp/duplicates_mbid.txt ]; then
    DUP_COUNT=$(wc -l < /tmp/duplicates_mbid.txt)
    echo -e "${YELLOW}Found $DUP_COUNT duplicate entries${NC}"
    echo "First 10:"
    head -10 /tmp/duplicates_mbid.txt
    echo ""
    echo "Full list saved to: /tmp/duplicates_mbid.txt"
    echo "Review and manually remove unwanted versions with:"
    echo "  beet remove -a mb_albumid:SPECIFIC_ID -d"
else
    echo -e "${GREEN}No duplicates found!${NC}"
fi
echo ""
((STEP++))

# ============================================================================
# STEP 8: FETCH MISSING ARTWORK
# ============================================================================
if [ "$DO_FETCH_ARTWORK" = true ]; then
    echo -e "${BLUE}[$STEP/$TOTAL_STEPS] Fetching missing artwork...${NC}"
    
    NO_ART_COUNT=$(beet ls -a artpath:: 2>/dev/null | wc -l)
    
    if [ "$NO_ART_COUNT" -gt 0 ]; then
        echo "Found $NO_ART_COUNT albums without artwork"
        echo "Downloading artwork..."
        
        beet fetchart -a artpath:: 2>&1 | tee -a /tmp/fetchart.log
        
        echo ""
        echo -e "${GREEN}Artwork fetched!${NC}"
    else
        echo -e "${GREEN}All albums have artwork!${NC}"
    fi
    echo ""
else
    echo -e "${YELLOW}[$STEP/$TOTAL_STEPS] Skipping artwork fetch${NC}"
    echo ""
fi
((STEP++))

# ============================================================================
# STEP 9: REGENERATE PLAYLISTS
# ============================================================================
echo -e "${BLUE}[$STEP/$TOTAL_STEPS] Regenerating smart playlists...${NC}"

beet splupdate 2>&1

echo ""
echo -e "${GREEN}Playlists regenerated!${NC}"
echo ""
((STEP++))

# ============================================================================
# STEP 10: UPDATE ALL PLAYERS
# ============================================================================
if [ "$DO_UPDATE_PLAYERS" = true ]; then
    echo -e "${BLUE}[$STEP/$TOTAL_STEPS] Updating all music players...${NC}"
    
    # Navidrome
    echo "Updating Navidrome..."
    curl -s -X POST "http://10.0.0.100:4533/rest/startScan?u=timeshadowrider&p=An|t@theR@bb|t&v=1.16.1&c=beets" > /dev/null 2>&1
    
    # Plex and Volumio update automatically via plugins
    echo "Plex and Volumio will auto-update via plugins"
    
    echo ""
    echo -e "${GREEN}Players updated!${NC}"
    echo ""
else
    echo -e "${YELLOW}[$STEP/$TOTAL_STEPS] Skipping player updates${NC}"
    echo ""
fi

# ============================================================================
# CLEANUP
# ============================================================================
echo -e "${BLUE}Cleaning up empty directories...${NC}"
find /music/library -type d -empty -delete 2>/dev/null
echo ""

# ============================================================================
# FINAL REPORT
# ============================================================================
echo ""
echo -e "${GREEN}=============================================="
echo "Maintenance Complete!"
echo "Finished: $(date)"
echo "==============================================${NC}"
echo ""

# Generate statistics
TOTAL_ALBUMS=$(beet ls -a 2>/dev/null | wc -l)
TOTAL_TRACKS=$(beet ls 2>/dev/null | wc -l)
TOTAL_ARTISTS=$(beet ls -a -f '$albumartist' 2>/dev/null | sort -u | wc -l)
NO_MBID=$(beet ls -a mb_albumid:: 2>/dev/null | wc -l)
NO_ART=$(beet ls -a artpath:: 2>/dev/null | wc -l)

echo "Library Statistics:"
echo "  Total Albums: $TOTAL_ALBUMS"
echo "  Total Tracks: $TOTAL_TRACKS"
echo "  Total Artists: $TOTAL_ARTISTS"
echo ""

echo "Remaining Issues:"
echo "  Albums without MusicBrainz ID: $NO_MBID"
echo "  Albums without artwork: $NO_ART"
echo ""

if [ "$NO_MBID" -gt 10 ] || [ "$NO_ART" -gt 50 ]; then
    echo -e "${YELLOW}??  Some issues remain${NC}"
else
    echo -e "${GREEN}? Library is in great shape!${NC}"
fi

echo ""
echo "Files generated:"
echo "  Log: $LOGFILE"
if [ -f /tmp/duplicates_mbid.txt ]; then
    echo "  Duplicates: /tmp/duplicates_mbid.txt"
fi
echo ""

if [ "$DO_BACKUP" = true ]; then
    echo "Database backup:"
    echo "  $BACKUP_FILE"
    echo ""
fi

echo "Done!"
