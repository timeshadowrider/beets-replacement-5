#!/bin/bash

echo "=== Inbox Path Verification ==="
echo ""

echo "1. Checking if /inbox exists:"
if [ -d "/inbox" ]; then
    echo "   ? /inbox directory exists"
    ls -ld /inbox
else
    echo "   ? /inbox directory NOT FOUND"
fi

echo ""
echo "2. Checking /inbox permissions:"
if [ -w "/inbox" ]; then
    echo "   ? /inbox is writable"
else
    echo "   ? /inbox is NOT writable"
fi

echo ""
echo "3. Checking /inbox contents:"
echo "   Audio files in /inbox:"
find /inbox -type f \( -iname "*.mp3" -o -iname "*.flac" -o -iname "*.m4a" -o -iname "*.ogg" \) 2>/dev/null | wc -l

echo ""
echo "4. Checking app.py INBOX_PATH setting:"
grep "INBOX_PATH" /app/app.py 2>/dev/null || grep "INBOX_PATH" /mnt/user-data/uploads/app.py

echo ""
echo "5. Checking if old /music/inbox exists:"
if [ -d "/music/inbox" ]; then
    echo "   ? WARNING: /music/inbox still exists - may cause confusion"
    ls -ld /music/inbox
else
    echo "   ? /music/inbox does not exist (good)"
fi

echo ""
echo "6. Testing beets import command (dry run):"
beet -c /config/config.yaml import -A /inbox 2>&1 | head -5

echo ""
echo "=== Verification Complete ==="