#!/bin/bash
#
# Patch script to add Quality column to Beets frontend
# This adds bitrate, bitdepth, and samplerate display to the tracklist
#

set -e

HTML_FILE="/app/static/index.html"
BACKUP_FILE="/app/static/index.html.pre-quality-patch"

echo "=========================================="
echo "Beets Frontend Quality Column Patch"
echo "=========================================="
echo ""

# Backup original
echo "[1/3] Creating backup..."
cp "$HTML_FILE" "$BACKUP_FILE"
echo "? Backup created: $BACKUP_FILE"
echo ""

# Patch 1: Add Quality to table headers
echo "[2/3] Adding Quality column header..."
sed -i 's/\["#", "Title", "Artist", "Duration"\]/["#", "Title", "Artist", "Quality", "Duration"]/' "$HTML_FILE"
echo "? Header updated"
echo ""

# Patch 2: Add Quality column rendering
echo "[3/3] Adding Quality column rendering logic..."

# This is complex - we need to add the qualityTd after artistTd
# Find the line with "const durTd = document.createElement" and insert before it

# Create the quality column code
cat > /tmp/quality_column.js << 'EOF'
    const qualityTd = document.createElement("td");
    qualityTd.style.fontSize = "11px";
    qualityTd.style.color = "#0ff";
    qualityTd.style.fontVariantNumeric = "tabular-nums";
    
    // Build quality string
    let qualityStr = t.format || "?";
    
    // Add bit depth and sample rate for lossless formats
    if (t.bitdepth || t.samplerate) {
      const bitdepth = t.bitdepth || "?";
      const samplerate = t.samplerate ? (t.samplerate / 1000).toFixed(1) : "?";
      qualityStr += ` • ${bitdepth}/${samplerate}`;
    }
    // Add bitrate for lossy formats or if available
    else if (t.bitrate) {
      const kbps = Math.round(t.bitrate / 1000);
      qualityStr += ` • ${kbps}kbps`;
    }
    
    qualityTd.textContent = qualityStr;

EOF

# Find the line number where we need to insert
LINE_NUM=$(grep -n "const durTd = document.createElement(\"td\"); durTd.className = \"track-duration\";" "$HTML_FILE" | head -1 | cut -d: -f1)

if [ -z "$LINE_NUM" ]; then
  echo "? Error: Could not find insertion point"
  echo "Restoring backup..."
  cp "$BACKUP_FILE" "$HTML_FILE"
  exit 1
fi

# Insert the quality column code before durTd
sed -i "${LINE_NUM}r /tmp/quality_column.js" "$HTML_FILE"

# Now we need to add qualityTd to the row appendChild section
# Find: tr.appendChild(artistTd);
# Add after it: tr.appendChild(qualityTd);

sed -i '/tr\.appendChild(artistTd);/a\      tr.appendChild(qualityTd);' "$HTML_FILE"

echo "? Quality column rendering added"
echo ""

# Cleanup
rm -f /tmp/quality_column.js

echo "=========================================="
echo "? Patch Complete!"
echo "=========================================="
echo ""
echo "Changes made:"
echo "1. Added 'Quality' to table headers"
echo "2. Added quality column rendering logic"
echo "3. Quality displays format, bitdepth, samplerate"
echo ""
echo "Examples:"
echo "  - FLAC • 16/44.1  (CD quality)"
echo "  - FLAC • 24/192   (Hi-Res)"
echo "  - MP3 • 320kbps   (Lossy)"
echo ""
echo "Backup saved: $BACKUP_FILE"
echo ""
echo "Restart the container to see changes:"
echo "  docker restart beets-single-5"
echo ""