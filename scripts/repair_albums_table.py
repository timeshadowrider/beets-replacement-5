#!/usr/bin/env python3
import sqlite3
import os

DB_PATH = "/data/beets-library.blb"

def main():
    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 1. Get all album_ids referenced by items
    c.execute("SELECT DISTINCT album_id FROM items WHERE album_id IS NOT NULL")
    item_album_ids = {row[0] for row in c.fetchall()}

    # 2. Get all album ids that already exist
    c.execute("SELECT id FROM albums")
    existing_album_ids = {row[0] for row in c.fetchall()}

    # 3. Determine missing album ids
    missing = sorted(item_album_ids - existing_album_ids)

    print(f"Found {len(missing)} missing album rows")

    # 4. Rebuild each missing album row
    for album_id in missing:
        # Pull representative track metadata
        c.execute("""
            SELECT
                albumartist,
                album,
                year,
                mb_albumid,
                mb_albumartistid,
                albumtype
            FROM items
            WHERE album_id = ?
            LIMIT 1
        """, (album_id,))
        row = c.fetchone()

        if not row:
            print(f"Skipping album_id {album_id}: no items found")
            continue

        albumartist, album, year, mb_albumid, mb_albumartistid, albumtype = row

        print(f"Rebuilding album_id {album_id}: {albumartist} - {album}")

        # Insert new album row
        c.execute("""
            INSERT INTO albums (
                id,
                albumartist,
                album,
                year,
                mb_albumid,
                mb_albumartistid,
                albumtype
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            album_id,
            albumartist,
            album,
            year,
            mb_albumid,
            mb_albumartistid,
            albumtype
        ))

    conn.commit()
    conn.close()
    print("Album table repair complete.")

if __name__ == "__main__":
    main()
