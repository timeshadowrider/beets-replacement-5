#!/usr/bin/env python3
import os, time, logging, threading
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from urllib.parse import quote_plus
import requests
from PIL import Image
from io import BytesIO
import json

MUSIC_ROOT = Path(os.environ.get('MUSIC_ROOT', '/path/to/music'))
LOG_FILE = os.environ.get('COVER_LOG', '/var/log/cover_watcher.log')
MIN_DIM = 200
RATE_LIMIT = 0.5
USER_AGENT = 'cover-watcher/1.0'

logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
session = requests.Session()
session.headers.update({'User-Agent': USER_AGENT})

def is_valid_image(data):
    try:
        img = Image.open(BytesIO(data))
        w,h = img.size
        return w >= MIN_DIM and h >= MIN_DIM
    except Exception:
        return False

def save_cover(path: Path, data: bytes):
    tmp = path.with_suffix('.jpg.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)
    logging.info('Saved cover %s', path)

# reuse fetch functions from periodic script: fetch_from_mbid and search_image_web
# (copy implementations here or import from a shared module)

def find_mbid(folder: Path):
    mbid_file = folder / 'musicbrainz_release_id.txt'
    if mbid_file.exists(): return mbid_file.read_text().strip()
    meta = folder / 'metadata.json'
    if meta.exists():
        try:
            j = json.loads(meta.read_text())
            return j.get('musicbrainz_release_id') or j.get('release_mbid')
        except Exception:
            pass
    return None

def fetch_cover_for_album(folder: Path):
    cover = folder / 'cover.jpg'
    if cover.exists(): return False
    mbid = find_mbid(folder)
    if mbid:
        data = fetch_from_mbid(mbid)
        if data:
            save_cover(cover, data)
            return True
    artist = folder.parent.name if folder.parent else ''
    query = f'{artist} {folder.name} album cover'
    data = search_image_web(query)
    if data:
        save_cover(cover, data)
        return True
    logging.info('No cover for %s', folder)
    return False

class AlbumEventHandler(FileSystemEventHandler):
    def on_created(self, event):
        path = Path(event.src_path)
        # if a directory created, check if it's an album
        if path.is_dir():
            threading.Thread(target=fetch_cover_for_album, args=(path,)).start()
        else:
            # file created, check parent folder
            album_dir = path.parent
            threading.Thread(target=fetch_cover_for_album, args=(album_dir,)).start()

    def on_moved(self, event):
        self.on_created(event)

    def on_closed(self, event):
        # some systems emit close events; treat similarly
        self.on_created(event)

if __name__ == '__main__':
    # initial scan
    for artist in MUSIC_ROOT.iterdir():
        if not artist.is_dir(): continue
        for album in artist.iterdir():
            if album.is_dir():
                try:
                    fetch_cover_for_album(album)
                except Exception as e:
                    logging.exception('initial scan error %s', e)

    # start watcher
    event_handler = AlbumEventHandler()
    observer = Observer()
    observer.schedule(event_handler, str(MUSIC_ROOT), recursive=True)
    observer.start()
    logging.info('Started watcher on %s', MUSIC_ROOT)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
