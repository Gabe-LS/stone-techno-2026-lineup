from __future__ import annotations

import sqlite3
from pathlib import Path


OVERRIDE_FIELDS = {
    "instagram",
    "soundcloud",
    "spotify",
    "linktree",
    "youtube",
    "photo_url",
    "ra",
}

OVERRIDE_ALIASES = {"photo": "photo_url"}


def init_db(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
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
        CREATE TABLE IF NOT EXISTS artists (
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
        CREATE TABLE IF NOT EXISTS locations (
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
            location_id TEXT NOT NULL REFERENCES locations(id),
            date        TEXT NOT NULL,
            note        TEXT NOT NULL,
            PRIMARY KEY (location_id, date, note)
        );
        CREATE TABLE IF NOT EXISTS location_details (
            location_id TEXT NOT NULL REFERENCES locations(id),
            label       TEXT NOT NULL,
            value       TEXT NOT NULL,
            position    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (location_id, label)
        );
        CREATE TABLE IF NOT EXISTS schedule (
            artist_id   TEXT NOT NULL REFERENCES artists(id),
            event_id    TEXT NOT NULL REFERENCES events(id),
            location_id TEXT REFERENCES locations(id),
            start_time  TEXT NOT NULL,
            end_time    TEXT NOT NULL,
            date        TEXT NOT NULL,
            period      TEXT,
            set_type    TEXT,
            PRIMARY KEY (artist_id, start_time)
        );
        CREATE INDEX IF NOT EXISTS idx_schedule_event ON schedule(event_id, date, period);
        CREATE TABLE IF NOT EXISTS videos (
            video_id    TEXT PRIMARY KEY,
            artist_id   TEXT NOT NULL REFERENCES artists(id),
            title       TEXT NOT NULL,
            url         TEXT NOT NULL,
            views       INTEGER NOT NULL DEFAULT 0,
            duration    INTEGER NOT NULL DEFAULT 0,
            upload_date INTEGER,
            position    INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_videos_artist ON videos(artist_id);
    """)
    db.commit()


def ensure_event(db: sqlite3.Connection, event_id: str, name: str, **kwargs) -> None:
    db.execute(
        "INSERT INTO events (id, name, url, website, start_date, end_date, timezone, address, latitude, longitude) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url, "
        "website=excluded.website, start_date=excluded.start_date, end_date=excluded.end_date, "
        "timezone=excluded.timezone, address=excluded.address, "
        "latitude=excluded.latitude, longitude=excluded.longitude",
        (
            event_id,
            name,
            kwargs.get("url"),
            kwargs.get("website"),
            kwargs.get("start_date"),
            kwargs.get("end_date"),
            kwargs.get("timezone", "Europe/Berlin"),
            kwargs.get("address"),
            kwargs.get("latitude"),
            kwargs.get("longitude"),
        ),
    )
    db.commit()


def upsert_lineup(db: sqlite3.Connection, parsed: dict, event_id: str) -> None:
    section_lookup = {
        sec["key"]: (sec["date"], sec["period"]) for sec in parsed["sections"]
    }

    for loc_id, loc in parsed["locations"].items():
        db.execute(
            "INSERT INTO locations (id, event_id, name, description) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description",
            (loc_id, event_id, loc["name"], loc.get("description")),
        )
    if parsed["locations"]:
        current_locs = list(parsed["locations"].keys())
        placeholders = ",".join("?" * len(current_locs))
        db.execute(
            f"DELETE FROM locations WHERE event_id = ? AND id NOT IN ({placeholders})",
            [event_id, *current_locs],
        )

    for oid, d in parsed["artists"].items():
        db.execute(
            "INSERT INTO artists (id, name, instagram, soundcloud, spotify, youtube, photo_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "name=excluded.name, instagram=excluded.instagram, soundcloud=excluded.soundcloud, "
            "spotify=excluded.spotify, youtube=excluded.youtube, photo_url=excluded.photo_url, "
            "ig_followers = CASE WHEN instagram IS NOT excluded.instagram THEN NULL ELSE ig_followers END, "
            "sc_followers = CASE WHEN soundcloud IS NOT excluded.soundcloud THEN NULL ELSE sc_followers END, "
            "spotify_listeners = CASE WHEN spotify IS NOT excluded.spotify THEN NULL ELSE spotify_listeners END, "
            "photo_local = CASE WHEN photo_url IS NOT excluded.photo_url THEN NULL ELSE photo_local END",
            (
                oid,
                d["name"],
                d.get("instagram"),
                d.get("soundcloud"),
                d.get("spotify"),
                d.get("youtube"),
                d.get("photo"),
            ),
        )

    if parsed["assignments"]:
        db.execute("DELETE FROM schedule WHERE event_id = ?", (event_id,))
        for assignment in parsed["assignments"]:
            ts_key = assignment["timestamp_key"]
            date, period = section_lookup.get(ts_key, ("", None))
            db.execute(
                "INSERT OR IGNORE INTO schedule "
                "(artist_id, event_id, location_id, start_time, end_time, date, period) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    assignment["overlay_id"],
                    event_id,
                    assignment.get("location_id"),
                    ts_key,
                    "",
                    date,
                    period,
                ),
            )
    elif parsed["artists"]:
        print(
            "WARNING: Scrape returned artists but no assignments - keeping existing lineup data"
        )
    db.commit()


def apply_overrides(
    db: sqlite3.Connection, overrides_path: Path, event_id: str | None = None
) -> None:
    if not overrides_path.exists():
        return
    import tomllib

    with open(overrides_path, "rb") as f:
        overrides = tomllib.load(f)
    if not overrides:
        return

    NON_ARTIST_SECTIONS = {
        "floor_curators",
        "youtube_names",
        "youtube_videos",
        "youtube_videos_add",
    }

    applied = 0
    for artist_name, fields in overrides.items():
        if artist_name in NON_ARTIST_SECTIONS:
            continue
        row = db.execute(
            "SELECT id FROM artists WHERE name = ?", (artist_name,)
        ).fetchone()
        if not row:
            print(f"  Override skipped: artist '{artist_name}' not found in DB")
            continue
        aid = row["id"]
        for field, value in fields.items():
            col = OVERRIDE_ALIASES.get(field, field)
            if col not in OVERRIDE_FIELDS:
                print(f"  Override skipped: unknown field '{field}' for {artist_name}")
                continue
            if value is False:
                value = ""
            dependent_col = {
                "instagram": "ig_followers",
                "soundcloud": "sc_followers",
                "spotify": "spotify_listeners",
                "photo_url": "photo_local",
                "ra": "ra_followers",
            }.get(col)
            current = db.execute(
                f"SELECT {col} FROM artists WHERE id = ?", (aid,)
            ).fetchone()[col]
            if current != value:
                if col == "ra":
                    count_val = 0 if value == "" else None
                    db.execute(
                        "UPDATE artists SET ra = ?, ra_followers = ?, ra_bio = ? WHERE id = ?",
                        (value, count_val, None if count_val is None else "", aid),
                    )
                elif dependent_col:
                    count_val = 0 if value == "" else None
                    db.execute(
                        f"UPDATE artists SET {col} = ?, {dependent_col} = ? WHERE id = ?",
                        (value, count_val, aid),
                    )
                else:
                    db.execute(
                        f"UPDATE artists SET {col} = ? WHERE id = ?",
                        (value, aid),
                    )
                applied += 1

    if event_id:
        curators = overrides.get("floor_curators", {})
        if curators:
            db.execute(
                "DELETE FROM location_notes WHERE location_id IN "
                "(SELECT id FROM locations WHERE event_id = ?)",
                (event_id,),
            )
            for key, note in curators.items():
                date, loc_id = key.split(".", 1)
                db.execute(
                    "INSERT OR IGNORE INTO location_notes (location_id, date, note) "
                    "VALUES (?, ?, ?)",
                    (loc_id, date, note),
                )
            applied += len(curators)

    if applied:
        db.commit()
        print(f"Applied {applied} override(s) from overrides.toml")


def load_floor_curators(db: sqlite3.Connection, event_id: str) -> dict[str, str]:
    return {
        f"{row['date']}.{row['location_id']}": row["note"]
        for row in db.execute(
            "SELECT ln.location_id, ln.date, ln.note FROM location_notes ln "
            "JOIN locations l ON l.id = ln.location_id "
            "WHERE l.event_id = ?",
            (event_id,),
        )
    }


def load_location_colors(db: sqlite3.Connection, event_id: str) -> dict[str, str]:
    return {
        row["id"]: row["color"]
        for row in db.execute(
            "SELECT id, color FROM locations WHERE event_id = ? AND color IS NOT NULL",
            (event_id,),
        )
    }


def get_event(db: sqlite3.Connection, event_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def get_missing(
    db: sqlite3.Connection, url_col: str, count_col: str
) -> list[sqlite3.Row]:
    return db.execute(
        f"SELECT id, {url_col} FROM artists "
        f"WHERE {url_col} IS NOT NULL AND {url_col} != '' AND {count_col} IS NULL"
    ).fetchall()


def get_artists_without_ra(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT * FROM artists WHERE (ra IS NULL OR ra = '') AND ra_followers IS NULL"
    ).fetchall()


def get_artists_missing_photos(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT id, photo_url FROM artists "
        "WHERE photo_url IS NOT NULL AND photo_local IS NULL"
    ).fetchall()


def save_photo_local(db: sqlite3.Connection, artist_id: str, filename: str) -> None:
    db.execute(
        "UPDATE artists SET photo_local = ? WHERE id = ?",
        (filename, artist_id),
    )
    db.commit()


def load_sections_from_db(db: sqlite3.Connection, event_id: str) -> list[dict]:
    return [
        {
            "key": f"{row['date']}:{row['period'] or 'all'}",
            "date": row["date"],
            "period": row["period"],
        }
        for row in db.execute(
            "SELECT DISTINCT date, period FROM schedule "
            "WHERE event_id = ? "
            "ORDER BY date, CASE period WHEN 'day' THEN 0 WHEN 'night' THEN 1 ELSE 2 END",
            (event_id,),
        )
    ]


def load_locations_from_db(db: sqlite3.Connection, event_id: str) -> dict[str, dict]:
    return {
        row["id"]: {
            "name": row["name"],
            "description": row["description"],
            "color": row["color"],
            "about": row["about"],
        }
        for row in db.execute(
            "SELECT id, name, description, color, about FROM locations WHERE event_id = ?",
            (event_id,),
        )
    }


def _load_artist_all_slots(
    db: sqlite3.Connection, event_id: str
) -> dict[str, list[dict]]:
    slots: dict[str, list[dict]] = {}
    for row in db.execute(
        "SELECT s.artist_id, s.date, s.period, s.location_id, l.name AS location_name, "
        "s.start_time, s.end_time "
        "FROM schedule s "
        "LEFT JOIN locations l ON l.id = s.location_id "
        "WHERE s.event_id = ? "
        "ORDER BY s.date, CASE s.period WHEN 'day' THEN 0 WHEN 'night' THEN 1 ELSE 2 END, "
        "s.start_time",
        (event_id,),
    ):
        slots.setdefault(row["artist_id"], []).append(
            {
                "date": row["date"],
                "period": row["period"],
                "location_id": row["location_id"],
                "location_name": row["location_name"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
            }
        )
    return slots


def load_assignments_from_db(
    db: sqlite3.Connection, event_id: str
) -> dict[str, list[dict]]:
    all_slots = _load_artist_all_slots(db, event_id)
    assignments: dict[str, list[dict]] = {}
    for row in db.execute(
        "SELECT a.id, a.name, a.instagram, a.soundcloud, a.spotify, a.linktree, "
        "a.youtube, a.photo_local, a.ig_followers, a.sc_followers, a.spotify_listeners, "
        "a.ra, a.ra_followers, a.ra_bio, "
        "s.date, s.period, s.location_id, s.start_time, s.end_time, s.set_type "
        "FROM schedule s "
        "JOIN artists a ON a.id = s.artist_id "
        "WHERE s.event_id = ? "
        "ORDER BY s.date, CASE s.period WHEN 'day' THEN 0 WHEN 'night' THEN 1 ELSE 2 END, "
        "s.start_time, a.name",
        (event_id,),
    ):
        section_key = f"{row['date']}:{row['period'] or 'all'}"
        assignments.setdefault(section_key, []).append(
            {
                "name": row["name"],
                "instagram": row["instagram"],
                "soundcloud": row["soundcloud"],
                "spotify": row["spotify"],
                "linktree": row["linktree"],
                "youtube": row["youtube"],
                "photo_local": row["photo_local"],
                "ig_followers": row["ig_followers"],
                "sc_followers": row["sc_followers"],
                "spotify_listeners": row["spotify_listeners"],
                "location_id": row["location_id"],
                "id": row["id"],
                "all_slots": all_slots.get(row["id"], []),
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "ra": row["ra"],
                "ra_followers": row["ra_followers"],
                "ra_bio": row["ra_bio"],
                "set_type": row["set_type"],
            }
        )
    return assignments


def load_all_videos(db: sqlite3.Connection) -> dict[str, list[dict]]:
    videos: dict[str, list[dict]] = {}
    for row in db.execute(
        "SELECT video_id, artist_id, title, url, views, duration, upload_date, position "
        "FROM videos ORDER BY artist_id, position"
    ):
        videos.setdefault(row["artist_id"], []).append(
            {
                "id": row["video_id"],
                "title": row["title"],
                "url": row["url"],
                "views": row["views"],
                "duration": row["duration"],
                "date": row["upload_date"],
            }
        )
    return videos


_VALID_FIELDS = {
    "ig_followers",
    "sc_followers",
    "spotify_listeners",
    "ra_followers",
    "ra_bio",
    "instagram",
    "soundcloud",
    "spotify",
    "linktree",
    "youtube",
    "ra",
    "photo_local",
}


def update_artist_field(
    db: sqlite3.Connection, artist_id: str, field: str, value
) -> None:
    if field not in _VALID_FIELDS:
        raise ValueError(f"Invalid field: {field}")
    db.execute(f"UPDATE artists SET {field} = ? WHERE id = ?", (value, artist_id))
    db.commit()


def get_artist(db: sqlite3.Connection, artist_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM artists WHERE id = ?", (artist_id,)).fetchone()
