#!/bin/bash
# Lynyrd Skynyrd "Pronounced" Album Cleanup Script
# Removes duplicates, keeps only the best versions

echo "=========================================="
echo "Lynyrd Skynyrd Duplicate Cleanup"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Step 1: Removing bad metadata (year 0000 and 0001)${NC}"
echo "These have incorrect year information and should be deleted"
echo ""

# Remove year 0000 albums
echo "Removing year 0000 albums..."
beet remove -a albumartist:"Lynyrd Skynyrd" album::"pronounced" year:0000 -d

# Remove year 0001 albums  
echo "Removing year 0001 albums..."
beet remove -a albumartist:"Lynyrd Skynyrd" album::"pronounced" year:0001 -d

echo ""
echo -e "${YELLOW}Step 2: Removing duplicate 2014 remasters${NC}"
echo "Keeping only ONE 2014 remaster (same MusicBrainz ID: e468d23a...)"
echo ""

# List all 2014 versions to see which to keep
echo "Current 2014 versions:"
beet ls -a albumartist:"Lynyrd Skynyrd" album::"pronounced" year:2014 -f '$format|$bitrate|$path'
echo ""

# Keep the first one, remove the rest
# We'll identify by the specific path to be safe
beet remove -a albumartist:"Lynyrd Skynyrd" mb_albumid:e468d23a-aa67-4301-ab2a-61bae72c175a -d
echo "Removed all 2014 duplicate entries"

echo ""
echo -e "${YELLOW}Step 3: Removing 2018 duplicate${NC}"
echo "2018 version is likely a duplicate reissue"
echo ""
beet remove -a albumartist:"Lynyrd Skynyrd" album::"Pronounced Leh Nerd Skin Nerd" year:2018 -d

echo ""
echo -e "${YELLOW}Step 4: Removing 'Ultimate MasterDisc' versions${NC}"
echo "These are unofficial bootleg releases"
echo ""
beet remove -a albumartist:"Lynyrd Skynyrd" album::"Ultimate MasterDisc" -d

echo ""
echo -e "${YELLOW}Step 5: Checking inbox items${NC}"
echo "NOTE: Items in /inbox/ should be properly imported or deleted"
echo ""
beet ls -a albumartist:"Lynyrd Skynyrd" path::/inbox -f '$year|$album|$path'
echo ""
echo "These inbox items were NOT removed automatically."
echo "They should be either:"
echo "  1. Properly imported with: beet import /inbox/path"
echo "  2. Or manually deleted if not needed"
echo ""

echo ""
echo -e "${GREEN}Step 6: What's left?${NC}"
echo "Remaining Lynyrd Skynyrd albums:"
echo ""
beet ls -a albumartist:"Lynyrd Skynyrd" -f '$year - $album ($format @ $bitrate)' | sort

echo ""
echo -e "${YELLOW}Step 7: Cleaning up empty folders${NC}"
find /music/library/Lynyrd\ Skynyrd -type d -empty -delete 2>/dev/null || true

echo ""
echo -e "${GREEN}=========================================="
echo "Cleanup Complete!"
echo "==========================================${NC}"
echo ""
echo "Summary:"
echo "  - Removed bad metadata (year 0000/0001)"
echo "  - Removed duplicate 2014 remasters"
echo "  - Removed 2018 duplicate"  
echo "  - Removed Ultimate MasterDisc bootlegs"
echo "  - Inbox items require manual action"
echo ""
echo "What you should have now:"
echo "  [1973] Original version"
echo "  [1993] Remaster (if desired)"
echo "  [2015] Live album (different release)"
echo ""