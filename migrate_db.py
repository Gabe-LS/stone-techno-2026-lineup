#!/usr/bin/env python3
"""One-time migration: old lineup.db schema -> new schema with events, videos, location_notes."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "lineup.db"
BACKUP_PATH = Path(__file__).resolve().parent / "lineup.db.bak"
VIDEOS_JSON = Path(__file__).resolve().parent / "output" / "videos.json"
OVERRIDES_PATH = Path(__file__).resolve().parent / "scraper" / "overrides.toml"

DEFAULT_EVENT_ID = "stone-techno-2026"
DEFAULT_EVENT_NAME = "Stone Techno 2026"
DEFAULT_EVENT_URL = "https://www.stone-techno.com/"
DEFAULT_TIMEZONE = "Europe/Berlin"

FLOOR_COLORS = {
    "eisbahn": "198, 249, 197",
    "grand-hall": "197, 249, 241",
    "koksofenbatterie": "197, 213, 249",
    "listening-floor": "226, 197, 249",
    "mischanlage": "249, 197, 228",
    "salzlager": "249, 211, 197",
    "werksschwimmbad": "243, 249, 197",
}


def migrate() -> None:
    if not DB_PATH.exists():
        print("No lineup.db found — nothing to migrate.")
        return

    shutil.copy2(DB_PATH, BACKUP_PATH)
    print(f"Backed up to {BACKUP_PATH}")

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = OFF")

    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    has_old_artists = "artists" in tables and "overlay_id" in {
        row[1] for row in db.execute("PRAGMA table_info(artists)")
    }
    has_old_sections = "sections" in tables
    has_old_artist_sections = "artist_sections" in tables
    has_new_schedule = "schedule" in tables
    has_events = "events" in tables

    if has_events and has_new_schedule:
        event_cols = {row[1] for row in db.execute("PRAGMA table_info(schedule)")}
        if "event_id" in event_cols:
            print("Already fully migrated. Skipping.")
            db.close()
            return

    section_lookup: dict[str, tuple[str, str]] = {}
    if has_old_sections:
        for row in db.execute("SELECT timestamp_key, date, period FROM sections"):
            section_lookup[row["timestamp_key"]] = (row["date"], row["period"])

    db.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            url        TEXT,
            website    TEXT,
            start_date TEXT,
            end_date   TEXT,
            timezone   TEXT NOT NULL DEFAULT 'Europe/Berlin',
            address    TEXT,
            latitude   REAL,
            longitude  REAL
        );
        CREATE TABLE IF NOT EXISTS artists_new (
            id                TEXT PRIMARY KEY,
            name              TEXT NOT NULL,
            photo_url         TEXT,
            photo_local       TEXT,
            instagram         TEXT,
            soundcloud        TEXT,
            spotify           TEXT,
            youtube           TEXT,
            linktree          TEXT,
            ra                TEXT,
            ig_followers      INTEGER,
            sc_followers      INTEGER,
            spotify_listeners INTEGER,
            ra_followers      INTEGER,
            ra_bio            TEXT
        );
        CREATE TABLE IF NOT EXISTS locations_new (
            id          TEXT PRIMARY KEY,
            event_id    TEXT NOT NULL REFERENCES events(id),
            name        TEXT NOT NULL,
            color       TEXT,
            description TEXT,
            about       TEXT,
            address     TEXT,
            latitude    REAL,
            longitude   REAL
        );
        CREATE TABLE IF NOT EXISTS location_notes (
            location_id TEXT NOT NULL REFERENCES locations_new(id),
            date        TEXT NOT NULL,
            note        TEXT NOT NULL,
            PRIMARY KEY (location_id, date, note)
        );
        CREATE TABLE IF NOT EXISTS location_details (
            location_id TEXT NOT NULL REFERENCES locations_new(id),
            label       TEXT NOT NULL,
            value       TEXT NOT NULL,
            position    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (location_id, label)
        );
        CREATE TABLE IF NOT EXISTS schedule_new (
            artist_id   TEXT NOT NULL REFERENCES artists_new(id),
            event_id    TEXT NOT NULL REFERENCES events(id),
            location_id TEXT REFERENCES locations_new(id),
            start_time  TEXT NOT NULL,
            end_time    TEXT NOT NULL,
            date        TEXT NOT NULL,
            period      TEXT,
            set_type    TEXT,
            PRIMARY KEY (artist_id, start_time)
        );
        CREATE TABLE IF NOT EXISTS videos_new (
            video_id    TEXT PRIMARY KEY,
            artist_id   TEXT NOT NULL REFERENCES artists_new(id),
            title       TEXT NOT NULL,
            url         TEXT NOT NULL,
            views       INTEGER NOT NULL DEFAULT 0,
            duration    INTEGER NOT NULL DEFAULT 0,
            upload_date INTEGER,
            position    INTEGER NOT NULL DEFAULT 0
        );
    """)

    db.execute(
        "INSERT OR IGNORE INTO events (id, name, url, timezone) VALUES (?, ?, ?, ?)",
        (DEFAULT_EVENT_ID, DEFAULT_EVENT_NAME, DEFAULT_EVENT_URL, DEFAULT_TIMEZONE),
    )
    print(f"Created event: {DEFAULT_EVENT_ID}")

    artist_table = "artists"
    id_col = "overlay_id" if has_old_artists else "id"
    photo_col = "photo" if has_old_artists else "photo_url"
    artist_cols = {row[1] for row in db.execute(f"PRAGMA table_info({artist_table})")}

    for row in db.execute(f"SELECT * FROM {artist_table}"):
        db.execute(
            "INSERT OR IGNORE INTO artists_new "
            "(id, name, photo_url, photo_local, instagram, soundcloud, spotify, "
            "youtube, linktree, ra, ig_followers, sc_followers, spotify_listeners, "
            "ra_followers, ra_bio) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row[id_col],
                row["name"],
                row[photo_col] if photo_col in artist_cols else None,
                row["photo_local"] if "photo_local" in artist_cols else None,
                row["instagram"],
                row["soundcloud"],
                row["spotify"],
                row["youtube"] if "youtube" in artist_cols else None,
                row["linktree"] if "linktree" in artist_cols else None,
                row["ra"] if "ra" in artist_cols else None,
                row["ig_followers"],
                row["sc_followers"],
                row["spotify_listeners"]
                if "spotify_listeners" in artist_cols
                else None,
                row["ra_followers"] if "ra_followers" in artist_cols else None,
                row["ra_bio"] if "ra_bio" in artist_cols else None,
            ),
        )
    artists_count = db.execute("SELECT COUNT(*) FROM artists_new").fetchone()[0]
    print(f"Migrated {artists_count} artists")

    used_locs = set()
    source_schedule = None
    if has_old_artist_sections:
        source_schedule = "artist_sections"
        used_locs = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT location_id FROM artist_sections WHERE location_id IS NOT NULL"
            ).fetchall()
        }
    elif has_new_schedule:
        source_schedule = "schedule"
        used_locs = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT location_id FROM schedule WHERE location_id IS NOT NULL"
            ).fetchall()
        }

    loc_table = "locations"
    loc_id_col = (
        "location_id"
        if "location_id"
        in {row[1] for row in db.execute(f"PRAGMA table_info({loc_table})")}
        else "id"
    )

    for row in db.execute(f"SELECT * FROM {loc_table}"):
        loc_id = row[loc_id_col]
        if used_locs and loc_id not in used_locs:
            print(f"  Skipping orphan location: {loc_id} ({row['name']})")
            continue
        color = FLOOR_COLORS.get(loc_id)
        db.execute(
            "INSERT OR IGNORE INTO locations_new (id, event_id, name, color, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (loc_id, DEFAULT_EVENT_ID, row["name"], color, row["description"]),
        )
    locs_count = db.execute("SELECT COUNT(*) FROM locations_new").fetchone()[0]
    print(f"Migrated {locs_count} locations with colors")

    sched_count = 0
    if source_schedule == "artist_sections":
        as_cols = {row[1] for row in db.execute("PRAGMA table_info(artist_sections)")}
        has_start = "start_time" in as_cols
        has_end = "end_time" in as_cols
        for row in db.execute("SELECT * FROM artist_sections"):
            oid = row["overlay_id"]
            ts_key = row["timestamp_key"]
            loc_id = row["location_id"]
            start = row["start_time"] if has_start and row["start_time"] else ts_key
            end = row["end_time"] if has_end and row["end_time"] else ""
            date, period = section_lookup.get(ts_key, ("", None))
            db.execute(
                "INSERT OR IGNORE INTO schedule_new "
                "(artist_id, event_id, location_id, start_time, end_time, date, period) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (oid, DEFAULT_EVENT_ID, loc_id, start, end, date, period),
            )
            sched_count += 1
    elif source_schedule == "schedule":
        sched_cols = {row[1] for row in db.execute("PRAGMA table_info(schedule)")}
        for row in db.execute("SELECT * FROM schedule"):
            db.execute(
                "INSERT OR IGNORE INTO schedule_new "
                "(artist_id, event_id, location_id, start_time, end_time, date, period) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["artist_id"],
                    DEFAULT_EVENT_ID,
                    row["location_id"],
                    row["start_time"],
                    row["end_time"],
                    row["date"],
                    row["period"],
                ),
            )
            sched_count += 1
    print(f"Migrated {sched_count} schedule entries")

    vid_count = 0
    if "videos" in tables:
        for row in db.execute("SELECT * FROM videos"):
            db.execute(
                "INSERT OR IGNORE INTO videos_new "
                "(video_id, artist_id, title, url, views, duration, upload_date, position) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["video_id"],
                    row["artist_id"],
                    row["title"],
                    row["url"],
                    row["views"],
                    row["duration"],
                    row["upload_date"],
                    row["position"],
                ),
            )
            vid_count += 1
    elif VIDEOS_JSON.exists():
        video_data = json.loads(VIDEOS_JSON.read_text(encoding="utf-8"))
        for artist_id, vids in video_data.items():
            for pos, v in enumerate(vids):
                db.execute(
                    "INSERT OR IGNORE INTO videos_new "
                    "(video_id, artist_id, title, url, views, duration, upload_date, position) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        v["id"],
                        artist_id,
                        v.get("title", ""),
                        v.get("url", ""),
                        v.get("views", 0),
                        v.get("duration", 0),
                        v.get("date"),
                        pos,
                    ),
                )
                vid_count += 1
    print(f"Migrated {vid_count} videos")

    if OVERRIDES_PATH.exists():
        import tomllib

        with open(OVERRIDES_PATH, "rb") as f:
            overrides = tomllib.load(f)
        curators = overrides.get("floor_curators", {})
        for key, note in curators.items():
            date, loc_id = key.split(".", 1)
            db.execute(
                "INSERT OR IGNORE INTO location_notes (location_id, date, note) "
                "VALUES (?, ?, ?)",
                (loc_id, date, note),
            )
        if curators:
            print(f"Migrated {len(curators)} floor curator notes")

    for old_table in (
        "artists",
        "locations",
        "schedule",
        "videos",
        "artist_sections",
        "sections",
    ):
        if old_table in tables or old_table in (
            "artists",
            "locations",
            "schedule",
            "videos",
        ):
            db.execute(f"DROP TABLE IF EXISTS {old_table}")
    db.execute("ALTER TABLE artists_new RENAME TO artists")
    db.execute("ALTER TABLE locations_new RENAME TO locations")
    db.execute("ALTER TABLE schedule_new RENAME TO schedule")
    db.execute("ALTER TABLE videos_new RENAME TO videos")

    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedule_event ON schedule(event_id, date, period)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_videos_artist ON videos(artist_id)")

    db.execute("PRAGMA foreign_keys = ON")
    db.commit()

    fk_errors = db.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        print(f"WARNING: {len(fk_errors)} foreign key violations!")
        for err in fk_errors[:5]:
            print(f"  {err}")
    else:
        print("Foreign key check passed")

    db.execute("VACUUM")
    db.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
