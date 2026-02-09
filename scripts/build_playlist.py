#!/usr/bin/env python3
import csv
import json
import sys
import os
from pathlib import Path

LIBRARY_ROOT = "/music/library"

def log(msg):
    """Log to stderr only"""
    print(msg, file=sys.stderr)

def build_index():
    log("Building library index...")
    index = []
    for root, dirs, files in os.walk(LIBRARY_ROOT):
        for f in files:
            if f.lower().endswith(('.flac', '.mp3', '.m4a', '.wav', '.ogg')):
                full_path = os.path.join(root, f)
                index.append(full_path)
    log(f"Indexed {len(index)} files")
    return index

def normalize(text):
    return text.lower().strip()

def find_match(index, track, artist="", album=""):
    track_norm = normalize(track)
    artist_norm = normalize(artist)
    
    for path in index:
        path_norm = normalize(path)
        if track_norm in path_norm:
            if not artist_norm or artist_norm in path_norm:
                return path
    return None

def main():
    if len(sys.argv) < 2:
        log("No CSV file provided")
        sys.exit(1)
    
    csv_file = sys.argv[1]
    
    if not os.path.exists(csv_file):
        log(f"File not found: {csv_file}")
        sys.exit(1)
    
    index = build_index()
    playlist = []
    
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            track = row.get('Track Name', row.get('Title', row.get('track', ''))).strip()
            artist = row.get('Artist Name(s)', row.get('Artist', row.get('artist', ''))).strip()
            album = row.get('Album Name', row.get('Album', row.get('album', ''))).strip()
            
            if not track:
                continue
            
            log(f"Searching: {track} - {artist}")
            
            match = find_match(index, track, artist, album)
            
            if match:
                log(f"  ? Found: {match}")
                playlist.append({
                    "uri": match,
                    "title": track,
                    "artist": artist or "Unknown",
                    "album": album or "Unknown"
                })
            else:
                log(f"  ? Not found")
    
    log(f"Matched {len(playlist)}/{len(list(csv.DictReader(open(csv_file))))} tracks")
    
    # Output ONLY JSON to stdout
    print(json.dumps(playlist, indent=2))

if __name__ == "__main__":
    main()