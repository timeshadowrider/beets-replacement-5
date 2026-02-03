# **Beets Replacement 5 — Fingerprint‑Driven Music Library Management**

A modern, deterministic, fingerprint‑aware replacement for the Beets Web API — built for large libraries, reproducible automation, and complete auditability.

This project wraps Beets inside a FastAPI backend, adds a structured JSON frontend layer, and introduces a set of tools for deduplication, metadata enrichment, and library cleanup. It is designed for home‑lab environments where reliability, transparency, and repeatability matter more than “magic”.

---

## **Features**

### **🎵 Fingerprint‑Based Duplicate Detection**
- Uses the Beets **chroma** plugin to generate AcoustID fingerprints.
- Stores fingerprints in the `acoustid_fingerprint` column for deterministic matching.
- Detects duplicates across:
  - multiple editions  
  - re‑rips  
  - compilations  
  - multi‑disc sets  
- Supports full‑library dedupe sweeps using:
  ```
  beet duplicates -f -t -d
  ```

### **📦 Dockerized Beets Environment**
- Fully containerized Beets instance.
- Persistent config and database.
- Safe schema migrations (including fingerprint column fixes).
- Supports large libraries without blocking or corruption.

### **⚡ FastAPI “Beets Replacement API”**
A custom backend that:
- Exposes structured JSON endpoints.
- Replaces the legacy Beets Web plugin.
- Generates `albums.json`, `recent.json`, and other frontend‑ready metadata.
- Integrates with your media dashboard UI.

### **🖼️ Automatic Cover Art Fetching**
- Background watcher fetches `cover.jpg` for every album.
- Ensures visual completeness across the entire library.
- Runs safely in a background thread without blocking imports.

### **🧹 Automated Cleanup Routines**
- Interval‑based cleanup of inbox/import directories.
- Deletes only folders with **no audio files** and **no UNPACK markers**.
- Never blocks imports or watchers.
- Fully audit‑friendly and idempotent.

### **🛠️ Deterministic Import Pipeline**
- Predefined import flags for silent, non‑interactive imports.
- Automatic fingerprinting.
- Automatic metadata enrichment (genre, lyrics, MB data).
- Edition‑aware path handling.

### **📊 Frontend Integration**
- Generates canonical `albums.json` for your UI.
- Ensures consistent album ordering, cover art presence, and metadata completeness.
- Rebuilds instantly after dedupe or import.

---

## **Architecture Overview**

```
+------------------------+
|   Inbox / Downloads    |
+-----------+------------+
            |
            v
+------------------------+
|   Beets (Docker)       |
|  - chroma              |
|  - fetchart            |
|  - duplicates          |
|  - metadata plugins    |
+-----------+------------+
            |
            v
+------------------------+
|  Beets Replacement API |
|   (FastAPI backend)    |
+-----------+------------+
            |
            v
+------------------------+
|   Frontend JSON Layer  |
|  albums.json, recent   |
+------------------------+
```

Everything is modular, reproducible, and validated at each step.

---

## **Setup**

### **1. Clone the repository**
```
git clone https://github.com/<yourname>/beets-replacement-5
cd beets-replacement-5
```

### **2. Configure Beets**
Your config includes:
- chroma  
- fetchart  
- embedart  
- lastgenre  
- lyrics  
- duplicates  
- convert  
- mbsync  
- smartplaylist  
- and more  

### **3. Start the Docker stack**
```
docker compose up -d
```

### **4. Verify Beets is working**
```
docker exec -it beets-single-5 beet version
docker exec -it beets-single-5 beet config -p
```

---

## **Fingerprinting**

To fingerprint the entire library:

```
docker exec -it beets-single-5 beet -c /config/config.yaml fingerprint
```

To verify fingerprints:

```
beet ls -f '$path $acoustid_fingerprint'
```

---

## **Duplicate Removal**

Preview duplicates:

```
docker exec -it beets-single-5 beet -c /config/config.yaml duplicates -f -t
```

Delete duplicates and their files:

```
docker exec -it beets-single-5 beet -c /config/config.yaml duplicates -f -t -d
```

---

## **Frontend Metadata Generation**

Regenerate `albums.json`:

```
docker exec -it beets-single-5 python3 /app/scripts/regenerate_albums.py
```

---

## **Inbox Cleanup**

Delete folders with **no audio files**:

```
find "/path/to/inbox" -mindepth 1 -type d \
  -print0 | while IFS= read -r -d '' dir; do
    if ! find "$dir" -maxdepth 1 -type f \( \
        -iname "*.mp3" -o -iname "*.flac" -o -iname "*.m4a" -o \
        -iname "*.wav" -o -iname "*.ogg" -o -iname "*.aac" \
      \) | grep -q .; then
        rm -rf "$dir"
    fi
done
```

---

## **Philosophy**

This project is built around:

- **Determinism**  
  Every action is explicit, logged, and reproducible.

- **Auditability**  
  No silent mutations. Every change can be validated.

- **Safety**  
  No destructive operations without fingerprint‑based certainty.

- **Completeness**  
  Every album has cover art, metadata, and a canonical representation.

- **Home‑lab reliability**  
  Designed to survive rebuilds, upgrades, and lifecycle events.

---

## **Roadmap**

- Full album‑level fingerprint grouping  
- Web UI for dedupe review  
- Real‑time import dashboard  
- Playlist generation engine  
- Multi‑library support  

---

## **License**

This project is licensed under the **MIT License**, the same license used by the original Beets project.

A full copy of the MIT License is included in the `LICENSE` file.
