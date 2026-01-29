#!/bin/sh

LOG=/data/import_watchdog.log

while true; do
    # Check if import is running
    if ! ps -ef | grep -E "beet .*import" | grep -v grep >/dev/null; then
        
        # Check if inbox or library has work
        if [ "$(find /music/inbox -type f | wc -l)" -gt 0 ] || \
           [ "$(find /music/library -type f | wc -l)" -gt 0 ]; then
            
            echo "$(date) - Import not running, restarting..." >> $LOG
            
            nohup beet -l /data/musiclibrary.db import -A -q --resume /music/library \
                >> /data/last_beets_imports.log 2>&1 &
        fi
    fi

    sleep 30
done
