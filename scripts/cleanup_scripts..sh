#!/bin/bash
#
# cleanup_scripts.sh
#
# This script safely archives unused/backup scripts from your scripts directory
# Run this on your host machine (not in Docker)
#

set -e

SCRIPTS_DIR="/srv/dev-disk-by-uuid-306c14f2-0239-4b1e-8775-915dfdd88bd0/NVME/Docket_Configs/beets-replacement-5/scripts"
ARCHIVE_DIR="${SCRIPTS_DIR}/archive_$(date +%Y%m%d_%H%M%S)"

# Color output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Scripts Directory Cleanup${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if scripts directory exists
if [ ! -d "$SCRIPTS_DIR" ]; then
    echo -e "${YELLOW}Error: Scripts directory not found: $SCRIPTS_DIR${NC}"
    exit 1
fi

echo -e "${GREEN}Current scripts directory:${NC} $SCRIPTS_DIR"
echo ""

# Create archive directory
mkdir -p "$ARCHIVE_DIR"
echo -e "${GREEN}Created archive directory:${NC} $ARCHIVE_DIR"
echo ""

# Files to KEEP (actively used by app.py)
KEEP_FILES=(
    "regenerate_albums.py"
    "recompute_recent.py"
    "fetch_cover.py"
    "smart_regenerate.py"
)

# Files to ARCHIVE (backups, unused utilities)
ARCHIVE_FILES=(
    "regenerate_albums.py.bak"
    "regenerate_albums.py.bak.02012026.727pm"
    "fetch_cover.py.bak"
    "beets_rebuild_clean.py"
    "rebuild_library.sh"
    "rename_library_folders.sh"
    "rename_library_folders - Copy.sh"
    "smart_duplicate_cleanup.sh"
    "cleanup_duplicates.sh"
    "watchdog_watcher.py"
    "regenerate_albums.sh"
)

echo -e "${GREEN}Files that will be KEPT (actively used):${NC}"
for file in "${KEEP_FILES[@]}"; do
    if [ -f "$SCRIPTS_DIR/$file" ]; then
        echo -e "  ? $file"
    else
        echo -e "  ${YELLOW}? $file (not found - you may need to add it)${NC}"
    fi
done
echo ""

echo -e "${YELLOW}Files that will be ARCHIVED (backups/unused):${NC}"
archived_count=0
for file in "${ARCHIVE_FILES[@]}"; do
    if [ -f "$SCRIPTS_DIR/$file" ]; then
        echo -e "  ? $file"
        archived_count=$((archived_count + 1))
    fi
done
echo ""

if [ $archived_count -eq 0 ]; then
    echo -e "${GREEN}No files to archive. Directory is already clean!${NC}"
    rmdir "$ARCHIVE_DIR"
    exit 0
fi

# Ask for confirmation
echo -e "${YELLOW}This will move $archived_count files to the archive directory.${NC}"
read -p "Continue? (y/N): " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Cancelled.${NC}"
    rmdir "$ARCHIVE_DIR"
    exit 0
fi

# Move files to archive
echo ""
echo -e "${GREEN}Archiving files...${NC}"
for file in "${ARCHIVE_FILES[@]}"; do
    if [ -f "$SCRIPTS_DIR/$file" ]; then
        mv "$SCRIPTS_DIR/$file" "$ARCHIVE_DIR/"
        echo -e "  ? Archived: $file"
    fi
done

# Create README in archive
cat > "$ARCHIVE_DIR/README.txt" << 'EOF'
ARCHIVED SCRIPTS
================

These files were archived because they are not actively used by the
beets-replacement application.

Archive Date: $(date)

Files in this archive:
- Backup files (.bak)
- Standalone utilities not called by app.py
- Old/duplicate scripts

Active scripts (kept in parent directory):
- regenerate_albums.py    - Full/targeted album regeneration
- recompute_recent.py     - Update recent albums list
- fetch_cover.py          - Fetch missing cover art
- smart_regenerate.py     - Background smart regeneration service

If you need any of these archived files, you can safely copy them back
to the scripts directory.
EOF

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}? Cleanup complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${GREEN}Archived $archived_count files to:${NC}"
echo -e "  $ARCHIVE_DIR"
echo ""
echo -e "${BLUE}Active scripts remaining:${NC}"
for file in "${KEEP_FILES[@]}"; do
    if [ -f "$SCRIPTS_DIR/$file" ]; then
        echo -e "  ? $file"
    fi
done
echo ""
echo -e "${YELLOW}Note: You can delete the archive directory at any time if you don't need the old files.${NC}"