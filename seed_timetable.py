#!/usr/bin/env python3
"""Seed fake timetable data: 5 floors with realistic time slots for all artists."""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "lineup.db"

FLOORS = [
    ("werksschwimmbad", "Werksschwimmbad", None),
    ("salzlager", "Salzlager", None),
    ("koksofenbatterie", "Koksofenbatterie", None),
    ("eisbahn", "Eisbahn", None),
    ("listening-floor", "Listening Floor", None),
]

DAY_START_HOUR = 12
DAY_END_HOUR = 24
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 31  # 07:00 next day

SET_LENGTHS = [60, 75, 90, 105, 120]


def seed(db: sqlite3.Connection) -> None:
    for loc_id, name, desc in FLOORS:
        db.execute(
            "INSERT INTO locations (location_id, name, description) VALUES (?, ?, ?) "
            "ON CONFLICT(location_id) DO UPDATE SET name=excluded.name, description=excluded.description",
            (loc_id, name, desc),
        )

    sections = db.execute(
        "SELECT timestamp_key, date, period FROM sections ORDER BY position"
    ).fetchall()

    for ts_key, date, period in sections:
        artists = db.execute(
            "SELECT overlay_id FROM artist_sections WHERE timestamp_key = ?",
            (ts_key,),
        ).fetchall()
        if not artists:
            continue

        overlay_ids = [r[0] for r in artists]
        random.shuffle(overlay_ids)

        is_night = period == "night"
        if is_night:
            floor_ids = [f[0] for f in FLOORS[:3]]
            start_hour = NIGHT_START_HOUR
            end_hour = NIGHT_END_HOUR
        else:
            floor_ids = [f[0] for f in FLOORS]
            start_hour = DAY_START_HOUR
            end_hour = DAY_END_HOUR

        per_floor = len(overlay_ids) // len(floor_ids)
        remainder = len(overlay_ids) % len(floor_ids)
        chunks: list[list[str]] = []
        idx = 0
        for i in range(len(floor_ids)):
            n = per_floor + (1 if i < remainder else 0)
            chunks.append(overlay_ids[idx : idx + n])
            idx += n

        for floor_id, chunk in zip(floor_ids, chunks):
            if not chunk:
                continue
            total_minutes = (end_hour - start_hour) * 60
            slot_minutes = total_minutes // len(chunk)
            slot_minutes = max(60, min(slot_minutes, 120))

            # Group some artists into B2B pairs
            slots: list[list[str]] = []
            i = 0
            while i < len(chunk):
                if i + 1 < len(chunk) and random.random() < 0.15:
                    slots.append([chunk[i], chunk[i + 1]])
                    i += 2
                else:
                    slots.append([chunk[i]])
                    i += 1

            total_minutes = (end_hour - start_hour) * 60
            slot_minutes = total_minutes // len(slots)
            slot_minutes = max(60, min(slot_minutes, 120))

            cursor = start_hour * 60
            for group in slots:
                length = (
                    random.choice([m for m in SET_LENGTHS if m <= slot_minutes + 15])
                    if slot_minutes >= 60
                    else slot_minutes
                )
                s_h, s_m = divmod(cursor, 60)
                e_h, e_m = divmod(cursor + length, 60)
                start_time = f"{date}T{s_h % 24:02d}:{s_m:02d}"
                end_time = f"{date}T{e_h % 24:02d}:{e_m:02d}"

                for oid in group:
                    db.execute(
                        "UPDATE artist_sections SET location_id = ?, start_time = ?, end_time = ? "
                        "WHERE overlay_id = ? AND timestamp_key = ?",
                        (floor_id, start_time, end_time, oid, ts_key),
                    )
                cursor += length

    db.commit()
    print("Seeded fake timetable data.")


if __name__ == "__main__":
    db = sqlite3.connect(str(DB_PATH))
    try:
        from scraper.db import init_db

        init_db(db)
        seed(db)
    finally:
        db.close()
