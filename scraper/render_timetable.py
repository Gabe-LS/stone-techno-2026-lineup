from __future__ import annotations

import html
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .scrape import format_followers

ICONS_DIR = Path(__file__).resolve().parent / "icons"


def _load_icon(name: str) -> str:
    path = ICONS_DIR / f"{name}-square-round.svg"
    if path.exists():
        svg = path.read_text(encoding="utf-8").strip()
        if "<?xml" in svg:
            idx = svg.find("<svg")
            if idx != -1:
                svg = svg[idx:]
        svg = svg.replace('width="24"', 'width="18"').replace(
            'height="24"', 'height="18"'
        )
        if "width=" not in svg:
            svg = svg.replace("<svg", '<svg width="18" height="18"', 1)
        return svg
    return ""


SVG_IG = _load_icon("instagram")
SVG_SC = _load_icon("soundcloud")
SVG_SP = _load_icon("spotify")
SVG_LT = _load_icon("linktree")
SVG_YT = _load_icon("youtube")


def _format_date_heading(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}, {dt.year}"


def _format_date_tab(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%a')} {dt.day}"


def _parse_time(t: str) -> int:
    """Return minutes since midnight from an ISO time string."""
    dt = datetime.fromisoformat(t)
    return dt.hour * 60 + dt.minute


def _format_hhmm(minutes: int) -> str:
    h, m = divmod(minutes % 1440, 60)
    return f"{h:02d}:{m:02d}"


def _artists_json(group: list[dict], photos_prefix: str) -> str:
    import json

    return json.dumps(
        [
            {
                "name": a.get("name", ""),
                "photo": photos_prefix + a["photo_local"]
                if a.get("photo_local")
                else "",
                "ig": a.get("instagram") or "",
                "sc": a.get("soundcloud") or "",
                "sp": a.get("spotify") or "",
                "lt": a.get("linktree") or "",
                "yt": a.get("youtube") or "",
                "igF": format_followers(a.get("ig_followers")) or "",
                "scF": format_followers(a.get("sc_followers")) or "",
                "spL": format_followers(a.get("spotify_listeners")) or "",
            }
            for a in group
        ]
    )


def render_timetable_html(
    title: str,
    ordered_sections: list[dict],
    assignments: dict[str, list[dict]],
    locations: dict[str, dict],
    photos_prefix: str = "photos/",
) -> str:
    def esc(text: str | None) -> str:
        return html.escape(text or "")

    dates_seen: list[str] = []
    sections_by_date: dict[str, list[dict]] = {}
    for sec in ordered_sections:
        sections_by_date.setdefault(sec["date"], []).append(sec)
        if sec["date"] not in dates_seen:
            dates_seen.append(sec["date"])

    timetable_data: list[dict] = []
    for date_str in dates_seen:
        for sec in sections_by_date[date_str]:
            artists = assignments.get(sec["key"], [])
            timed = [a for a in artists if a.get("start_time") and a.get("end_time")]
            if not timed:
                continue

            floor_ids: list[str] = []
            by_floor: dict[str, list[dict]] = {}
            for a in timed:
                fid = a.get("location_id") or "unknown"
                by_floor.setdefault(fid, []).append(a)
                if fid not in floor_ids:
                    floor_ids.append(fid)

            all_starts = [_parse_time(a["start_time"]) for a in timed]
            all_ends = [_parse_time(a["end_time"]) for a in timed]
            is_night = sec["period"] == "night"
            if is_night:
                adjusted_ends = []
                for e in all_ends:
                    adjusted_ends.append(e + 1440 if e < 12 * 60 else e)
                adjusted_starts = []
                for s in all_starts:
                    adjusted_starts.append(s + 1440 if s < 12 * 60 else s)
                grid_start = min(adjusted_starts)
                grid_end = max(adjusted_ends)
            else:
                grid_start = min(all_starts)
                grid_end = max(all_ends)

            grid_start = (grid_start // 60) * 60

            timetable_data.append(
                {
                    "date": date_str,
                    "period": sec["period"],
                    "key": sec["key"],
                    "floor_ids": floor_ids,
                    "by_floor": by_floor,
                    "grid_start": grid_start,
                    "grid_end": grid_end,
                    "is_night": is_night,
                }
            )

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('  <meta charset="UTF-8">')
    parts.append(
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">'
    )
    parts.append(f"  <title>{esc(title)}</title>")
    description = "Explore the Stone Techno 2026 line-up: artist profiles, social links, follower counts. Save your picks and share them with friends."
    parts.append(f'  <meta name="description" content="{esc(description)}">')
    parts.append('  <meta name="robots" content="noindex, nofollow">')
    parts.append(
        '  <script defer src="https://analytics.deftlab.dev/script.js" data-website-id="8f79ad80-e080-421d-91c6-45b7bfc460d2" data-domains="stonetechno.deftlab.dev" data-auto-track="true" data-performance="true"></script>'
    )
    import base64 as _b64

    favicon_b64 = _b64.b64encode((ICONS_DIR / "favicon.svg").read_bytes()).decode()
    parts.append(
        f'  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,{favicon_b64}">'
    )
    parts.append("  <style>")
    parts.append("""
    *, *::before, *::after { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; line-height: 1.5; max-width: 960px; margin: 0 auto; padding: 0 24px; color: #111; background: #fff; }
    h1 { margin-bottom: 32px; font-size: 2em; position: sticky; top: 28px; background: #fff; z-index: 30; padding: 12px 0 8px; border-bottom: 2px solid #222; }

    /* Command bar */
    .cmd-bar { position: sticky; top: 0; z-index: 40; background: #111; color: #fff; display: flex; align-items: stretch; height: 28px; font-size: 0.75em; }
    .cmd-bar button { background: none; color: #999; border: none; cursor: pointer; padding: 0; font-size: 1em; white-space: nowrap; flex: 1; text-align: center; transition: color 0.1s; letter-spacing: 0.03em; }
    .cmd-bar button:hover { color: #fff; }
    .cmd-bar button:focus-visible { outline: 1px solid #fff; outline-offset: -2px; }
    .cmd-bar button:focus:not(:focus-visible) { outline: none; }
    .cmd-bar button.active { color: #fff; }
    .cmd-bar .sep { color: #333; margin: 0; display: flex; align-items: center; }

    /* Filter bar — like h2 in main page */
    .filter-bar { position: sticky; top: 96px; z-index: 20; background: #fff; display: flex; align-items: center; justify-content: space-between; padding: 10px 0 8px; margin-bottom: 8px; gap: 8px; border-bottom: 1px solid #ccc; }
    .day-tabs { display: flex; gap: 2px; }
    .period-tabs { display: flex; gap: 2px; }
    .day-tab, .period-tab { padding: 7px 14px; border: 1px solid #ddd; border-radius: 6px; background: #f5f5f5; cursor: pointer; font-size: 0.82em; font-weight: 600; transition: background 0.15s, border-color 0.15s; }
    .day-tab:hover, .period-tab:hover { background: #eee; }
    .day-tab.active { background: #111; color: #fff; border-color: #111; }
    .period-tab.active { background: #333; color: #fff; border-color: #333; }

    /* Floor headers — like h3 in main page */
    .floor-header-bar { display: grid; position: sticky; top: 146px; z-index: 10; background: #fff; padding: 8px 0 6px; margin: 24px 0 12px; }
    .floor-header-bar.fade-after { position: sticky; }
    .floor-header-bar::after { content: ''; position: absolute; left: 0; right: 0; top: 100%; height: 36px; background: linear-gradient(to bottom, rgba(255,255,255,1) 0%, rgba(255,255,255,0.9) 20%, rgba(255,255,255,0.75) 35%, rgba(255,255,255,0.5) 55%, rgba(255,255,255,0.15) 78%, rgba(255,255,255,0) 100%); pointer-events: none; opacity: 0; transition: opacity 0.15s; }
    .floor-header-bar.stuck::after { opacity: 1; }
    .floor-header-gutter { }
    .floor-header { text-align: center; font-weight: 700; font-size: 0.85em; padding: 8px 12px; border-radius: 999px; margin: 0 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    /* Timetable grid */
    .timetable-panel { display: none; }
    .timetable-panel.active { display: block; }
    .timetable { display: grid; position: relative; margin-bottom: 4px; }
    .time-gutter { grid-column: 1; position: sticky; left: 0; z-index: 5; background: #fff; width: 40px; }
    .time-label { font-size: 0.7em; color: #999; text-align: right; padding-right: 8px; line-height: 1; position: relative; top: calc(-0.5em + 1px); }
    .grid-line { grid-column: 2 / -1; border-top: 1px solid #ccc; pointer-events: none; }
    .grid-line.hour { border-top: 1px solid #aaa; }
    .grid-line.half { border-top: 1px dashed #ccc; }

    /* Artist blocks */
    .tt-block { border-radius: 6px; margin: 5px 3px 4px; padding: 6px 8px; font-size: 0.82em; overflow: hidden; cursor: pointer; position: relative; display: flex; flex-direction: row; align-items: flex-start; border: 1px solid rgba(0,0,0,0.1); transition: opacity 0.15s; min-height: 0; }
    .tt-text { min-width: 0; flex: 1; display: flex; flex-direction: column; padding-right: 18px; overflow: hidden; }
    .tt-block .tt-time { font-size: 0.85em; color: rgba(0,0,0,0.45); white-space: nowrap; line-height: 1.3; margin-bottom: 3px; }
    .tt-artist-row { display: flex; align-items: center; gap: 6px; margin-top: 6px; min-width: 0; overflow: hidden; }
    .tt-photo { width: 28px; height: 28px; border-radius: 4px; object-fit: cover; flex-shrink: 0; }
    .tt-photo-placeholder { width: 28px; height: 28px; border-radius: 4px; background: rgba(0,0,0,0.06); flex-shrink: 0; }
    .tt-block .tt-name { font-weight: 700; font-size: 1em; white-space: nowrap; overflow: hidden; line-height: 1.3; min-width: 0; flex: 1; }

    /* Now line */
    .now-line { grid-column: 2 / -1; border-top: 2px solid #e53e3e; pointer-events: none; z-index: 8; position: relative; }
    .now-line::before { content: 'NOW'; position: absolute; left: -48px; top: -8px; font-size: 9px; font-weight: 700; color: #e53e3e; letter-spacing: 0.05em; }
    .tt-block .tt-heart { position: absolute; top: 3px; right: 3px; background: none; border: none; cursor: pointer; padding: 2px; line-height: 0; }
    .tt-block .tt-heart svg { width: 14px; height: 14px; fill: none; stroke: rgba(0,0,0,0.2); stroke-width: 2; transition: fill 0.15s, stroke 0.15s; }
    .tt-block .tt-heart.active svg { fill: #e53e3e; stroke: #e53e3e; }
    .tt-block .tt-heart:hover:not(.active) svg { stroke: rgba(0,0,0,0.4); }

    /* Floor colors */
    .floor-werksschwimmbad { background: rgba(219, 234, 254, 0.75); }
    .floor-salzlager { background: rgba(254, 243, 199, 0.75); }
    .floor-koksofenbatterie { background: rgba(252, 231, 243, 0.75); }
    .floor-eisbahn { background: rgba(209, 250, 229, 0.75); }
    .floor-listening-floor { background: rgba(237, 233, 254, 0.75); }
    .floor-unknown { background: rgba(243, 244, 246, 0.75); }

    .floor-header.floor-werksschwimmbad { background: #dbeafe; }
    .floor-header.floor-salzlager { background: #fef3c7; }
    .floor-header.floor-koksofenbatterie { background: #fce7f3; }
    .floor-header.floor-eisbahn { background: #d1fae5; }
    .floor-header.floor-listening-floor { background: #ede9fe; }

    /* Artist detail popup */
    .tt-popup { position: fixed; z-index: 200; background: #fff; border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,0.18); padding: 16px; width: 320px; max-width: 90vw; visibility: hidden; opacity: 0; pointer-events: none; }
    .tt-popup.open { visibility: visible; opacity: 1; pointer-events: auto; }
    .tt-popup .popup-meta { font-size: 0.8em; color: #888; margin-bottom: 10px; }
    .tt-popup .popup-artist { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
    .tt-popup .popup-photo { width: 48px; height: 48px; border-radius: 6px; object-fit: cover; flex-shrink: 0; }
    .tt-popup .popup-photo-placeholder { width: 48px; height: 48px; border-radius: 6px; background: #eee; flex-shrink: 0; }
    .tt-popup .popup-name { font-weight: 700; font-size: 1em; }
    .tt-popup .links { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 8px; }
    .tt-popup .links a { display: inline-flex; align-items: center; gap: 4px; text-decoration: none; color: #555; font-size: 0.75em; }
    .tt-popup .links a:hover { color: #111; }

    /* Filter */
    .filter-active .tt-block:not(.hearted) { opacity: 0.15; }

    /* Modals (shared with list view) */
    html.scroll-locked, html.scroll-locked body { overflow:hidden; }
    html.scroll-locked body { position:fixed; left:0; right:0; }
    .modal-overlay { display:none; position:fixed; inset:0; z-index:100; background:rgba(0,0,0,.4); padding:24px; }
    .modal-overlay.open { display:flex; justify-content:center; align-items:center; }
    .modal-box { background:#fff; border-radius:14px; padding:24px; width:420px; max-width:100%; text-align:center; color:#111; box-shadow:0 8px 24px rgba(0,0,0,.12); }
    .modal-box h3 { margin:0 0 6px; font-size:1em; font-weight:600; }
    .modal-box .sub { font-size:.8em; color:#999; margin:0 0 14px; }
    .modal-link { display:block; width:100%; background:#f5f5f5; padding:12px 14px; border-radius:8px; font-size:.82em; font-family:inherit; color:#333; cursor:pointer; transition:background .15s; margin:0; border:none; text-align:left; overflow:hidden; text-overflow:clip; white-space:nowrap; box-sizing:border-box; outline:none; }
    .modal-link:hover { background:#eee; }
    .modal-link.copied { background:#d4edda; text-align:center; }
    .modal-box canvas { display:block; margin:10px auto; border-radius:6px; }
    .modal-box .or-line { display:flex; align-items:center; gap:10px; margin:10px 0; }
    .modal-box .or-line hr { flex:1; border:none; border-top:1px solid #e0e0e0; }
    .modal-box .or-line span { color:#bbb; font-size:.78em; }
    .modal-box .tabs { display:flex; gap:3px; margin-bottom:14px; border-radius:8px; border:1px solid #e0e0e0; padding:3px; background:#f5f5f5; }
    .modal-box .tabs button { flex:1; background:transparent; border:none; padding:7px 4px; cursor:pointer; font-size:.8em; color:#888; border-radius:5px; transition:color .15s,background .15s; }
    .modal-box .tabs button:focus-visible { outline:1px solid #111; outline-offset:-2px; }
    .modal-box .tabs button:focus:not(:focus-visible) { outline:none; }
    .modal-box .tabs button:hover:not(.on) { background:#eee; color:#555; }
    .modal-box .tabs button.on { background:#111; color:#fff; }
    .modal-box .pane { display:none; }
    .modal-box .pane.on { display:block; }
    .modal-box .lbl { font-size:.82em; color:#333; text-align:left; margin:0 0 4px; }
    .modal-box .recv-lbl { font-size:.82em; color:#333; text-align:left; margin:10px 0 4px; }
    .modal-box .steps { counter-reset:s; }
    .modal-box .steps p { text-align:left; font-size:.8em; color:#333; margin:5px 0; padding-left:16px; }
    .modal-box .steps p::before { content:counter(s) ". "; counter-increment:s; font-weight:600; }
    .pin { display:flex; gap:5px; justify-content:center; margin:10px 0; }
    .pin span { width:28px; height:36px; font-size:1.2em; font-weight:700; border:1px solid #ddd; border-radius:5px; background:#f5f5f5; color:#111; display:flex; align-items:center; justify-content:center; line-height:1; }
    .sync-expiry { font-size:.75em; color:#999; text-align:center; margin:8px 0 0; }
    .sync-expiry a { color:inherit; text-decoration:underline; cursor:pointer; }
    .pin-wrap { position:relative; cursor:text; margin:10px 0; -webkit-tap-highlight-color:transparent; }
    .pin-wrap .pin { pointer-events:none; }
    .pin-wrap .pin span.active { border-color:#111; background:#fff; }
    .pin-wrap.focused .pin span.active:empty::after { content:''; width:2px; height:1.2em; background:#111; border-radius:1px; animation:blink 1s step-end infinite; }
    @keyframes blink { 0%,100% { opacity:1; } 50% { opacity:0; } }
    .pin-wrap .pin span.filled { color:#111; }
    .pin-real { position:absolute; inset:0; opacity:0; font-size:16px; width:100%; height:100%; border:none; padding:0; margin:0; -webkit-tap-highlight-color:transparent; }
    .modal-box .btn { background:#111; color:#fff; border:none; padding:7px 18px; border-radius:5px; cursor:pointer; font-size:.82em; margin-top:8px; }
    .modal-box .btn:hover { background:#333; }
    .modal-box .btn:focus-visible { outline:1px solid #111; outline-offset:2px; }
    .modal-box .btn:focus:not(:focus-visible) { outline:none; }
    .qr-wrap { display:block; }
    @media (max-width:480px) { .qr-wrap { display:none; } .modal-box .tabs { flex-direction:column; } }

    @media (max-width: 768px) {
      body { padding: 0 12px; }
      h1 { font-size: 1.3em; }
      .floor-header { font-size: 0.7em; padding: 6px 2px; }
      .tt-block { font-size: 0.72em; padding: 4px 5px; margin: 2px; gap: 5px; }
      .tt-block .tt-heart { display: none; }
      .tt-photo, .tt-photo-placeholder { width: 22px; height: 22px; border-radius: 3px; }
      .day-tab { padding: 6px 10px; font-size: 0.8em; }
    }
    """)
    parts.append("  </style>")
    parts.append("</head>")
    parts.append("<body>")

    # Command bar
    parts.append('  <div class="cmd-bar" id="cmd-bar">')
    parts.append(
        '    <button onmousedown="this.blur()" onclick="toggleFilter(this)" id="btn-filter">Show My Picks</button>'
    )
    parts.append('    <span class="sep">|</span>')
    parts.append(
        '    <button onmousedown="this.blur()" onclick="openShareModal()">Share My Picks</button>'
    )
    parts.append('    <span class="sep">|</span>')
    parts.append(
        '    <button onmousedown="this.blur()" onclick="openSyncModal()">Sync My Picks</button>'
    )
    parts.append("  </div>")

    parts.append(f"  <h1>{esc(title)}</h1>")

    # Filter bar (sticky like h2)
    parts.append('  <div class="filter-bar">')
    parts.append('    <div class="day-tabs" id="day-tabs">')
    for i, date_str in enumerate(dates_seen):
        active = " active" if i == 0 else ""
        parts.append(
            f'      <button class="day-tab{active}" onclick="switchDay(\'{esc(date_str)}\', this)">'
            f"{esc(_format_date_tab(date_str))}</button>"
        )
    parts.append("    </div>")
    parts.append('    <div class="period-tabs" id="period-tabs"></div>')
    parts.append("  </div>")

    # Render timetable panels per section
    for td in timetable_data:
        date_str = td["date"]
        period = td["period"]
        panel_id = f"panel-{date_str}-{period}"
        floor_ids = td["floor_ids"]
        by_floor = td["by_floor"]
        grid_start = td["grid_start"]
        grid_end = td["grid_end"]
        is_night = td["is_night"]
        num_floors = len(floor_ids)

        total_minutes = grid_end - grid_start
        px_per_min = 4
        grid_height = total_minutes * px_per_min

        parts.append(
            f'  <div class="timetable-panel" data-date="{esc(date_str)}" data-period="{esc(period)}" '
            f'data-grid-start="{grid_start}" data-grid-end="{grid_end}" '
            f'data-is-night="{1 if is_night else 0}" id="{esc(panel_id)}">'
        )

        # Floor header bar (sticky inside panel)
        parts.append(
            f'    <div class="floor-header-bar" '
            f'style="grid-template-columns: 40px repeat({num_floors}, 1fr);">'
        )
        parts.append('      <div class="floor-header-gutter"></div>')
        for fid in floor_ids:
            loc_name = locations.get(fid, {}).get("name", fid)
            parts.append(
                f'      <div class="floor-header floor-{esc(fid)}">{esc(loc_name)}</div>'
            )
        parts.append("    </div>")

        parts.append(
            f'    <div class="timetable" style="grid-template-columns: 40px repeat({num_floors}, 1fr); grid-template-rows: repeat({total_minutes}, {px_per_min}px);">'
        )

        # Time labels and grid lines
        hour_start = grid_start // 60
        hour_end = (grid_end + 59) // 60
        for h in range(hour_start, hour_end):
            row = (h * 60 - grid_start) + 1
            display_h = h % 24
            parts.append(
                f'      <div class="time-label" style="grid-column: 1; grid-row: {row};">{display_h:02d}:00</div>'
            )
            parts.append(
                f'      <div class="grid-line hour" style="grid-row: {row};"></div>'
            )
            half_row = row + 30
            if half_row < (grid_end - grid_start) + 1:
                parts.append(
                    f'      <div class="grid-line half" style="grid-row: {half_row};"></div>'
                )

        # Now line placeholder
        parts.append(
            '      <div class="now-line" style="grid-row: 2; display: none;" data-now-line></div>'
        )

        # Artist blocks — group by (floor, start_time, end_time) for B2B
        heart_svg = '<svg viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'

        for col, fid in enumerate(floor_ids, 2):
            floor_artists = by_floor.get(fid, [])
            slots: dict[tuple[str, str], list[dict]] = {}
            for a in floor_artists:
                key = (a["start_time"], a["end_time"])
                slots.setdefault(key, []).append(a)

            for (st, et), group in slots.items():
                start_min = _parse_time(st)
                end_min = _parse_time(et)
                if is_night:
                    if start_min < 12 * 60:
                        start_min += 1440
                    if end_min < 12 * 60:
                        end_min += 1440

                row_start = (start_min - grid_start) + 1
                row_end = (end_min - grid_start) + 1

                s_display = _format_hhmm(start_min)
                e_display = _format_hhmm(end_min)
                loc_name = locations.get(fid, {}).get("name", fid)

                card_key = ":".join(
                    [a.get("overlay_id", "") for a in group] + [date_str, period, fid]
                )
                artist_id = str(uuid.uuid5(uuid.NAMESPACE_URL, card_key))

                names = " b2b ".join(a.get("name", "") for a in group)
                data_attrs = (
                    f'data-artist-id="{esc(artist_id)}" '
                    f'data-name="{esc(names)}" '
                    f'data-time="{esc(s_display)} – {esc(e_display)}" '
                    f'data-floor="{esc(loc_name)}" '
                    f"data-artists='{esc(_artists_json(group, photos_prefix))}'"
                )

                parts.append(
                    f'      <div class="tt-block floor-{esc(fid)}" style="grid-column: {col}; grid-row: {row_start} / {row_end};" {data_attrs}>'
                    f'<div class="tt-text">'
                    f'<span class="tt-time">{esc(s_display)}–{esc(e_display)}</span>'
                )
                for a in group:
                    photo_local = a.get("photo_local") or ""
                    name = a.get("name", "")
                    if photo_local:
                        photo_el = f'<img class="tt-photo" src="{esc(photos_prefix + photo_local)}" alt="" loading="lazy">'
                    else:
                        photo_el = '<div class="tt-photo-placeholder"></div>'
                    parts.append(
                        f'<div class="tt-artist-row">{photo_el}<span class="tt-name">{esc(name)}</span></div>'
                    )
                parts.append(
                    f"</div>"
                    f'<button class="tt-heart" onclick="event.stopPropagation(); toggleHeart(this)" aria-label="Add to favorites" aria-pressed="false">{heart_svg}</button>'
                    f"</div>"
                )

        parts.append("    </div>")  # .timetable
        parts.append("  </div>")  # .timetable-panel

    # Artist detail popup
    parts.append('  <div class="tt-popup" id="tt-popup">')
    parts.append('    <div class="popup-meta" id="popup-meta"></div>')
    parts.append('    <div id="popup-artists"></div>')
    parts.append("  </div>")

    # Share modal
    parts.append(
        '  <div class="modal-overlay" id="m-share" role="dialog" aria-modal="true" aria-labelledby="m-share-title">'
    )
    parts.append('    <div class="modal-box">')
    parts.append('      <h3 id="m-share-title">Share My Picks</h3>')
    parts.append(
        '      <p class="sub" style="color:inherit">Friends can view your picks. Click the link to copy it.</p>'
    )
    parts.append(
        '      <input type="text" readonly class="modal-link" id="share-link">'
    )
    parts.append("    </div>")
    parts.append("  </div>")

    # Sync modal
    parts.append(
        '  <div class="modal-overlay" id="m-sync" role="dialog" aria-modal="true" aria-labelledby="m-sync-title">'
    )
    parts.append('    <div class="modal-box">')
    parts.append('      <h3 id="m-sync-title">Sync Your Devices</h3>')
    parts.append('      <div class="tabs">')
    parts.append(
        '        <button type="button" class="on" onclick="syncTab(\'send\',this)">Send to another device</button>'
    )
    parts.append(
        '        <button type="button" onclick="syncTab(\'recv\',this)">Receive from another device</button>'
    )
    parts.append("      </div>")
    parts.append('      <div class="pane on" id="p-send">')
    parts.append('        <div id="sync-pending">')
    parts.append('          <div class="qr-wrap">')
    parts.append('            <p class="lbl">Scan this QR with your other device:</p>')
    parts.append(
        '            <canvas id="sync-qr" width="360" height="360" style="width:120px;height:120px"></canvas>'
    )
    parts.append('            <div class="or-line"><hr><span>or</span><hr></div>')
    parts.append("          </div>")
    parts.append('          <p class="lbl">On your other device:</p>')
    parts.append('          <div class="steps">')
    parts.append("            <p>Open <strong>stonetechno.deftlab.dev</strong></p>")
    parts.append("            <p>Click <strong>Sync My Picks</strong></p>")
    parts.append(
        "            <p>Click <strong>Receive from another device</strong></p>"
    )
    parts.append("            <p>Enter the code shown below</p>")
    parts.append("          </div>")
    parts.append('          <div class="pin" id="pin-display"></div>')
    parts.append('          <p class="sync-expiry" id="sync-expiry"></p>')
    parts.append("        </div>")
    parts.append(
        '        <div id="sync-done" style="display:none;text-align:center;padding:24px 0">'
    )
    parts.append(
        '          <svg viewBox="0 0 52 52" width="52" height="52"><circle cx="26" cy="26" r="24" fill="none" stroke="#4caf50" stroke-width="3"/><path fill="none" stroke="#4caf50" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" d="M15 27l7 7 15-15"/></svg>'
    )
    parts.append('          <p style="margin:12px 0 0">Device synced successfully</p>')
    parts.append("        </div>")
    parts.append("      </div>")
    parts.append('      <div class="pane" id="p-recv">')
    parts.append('        <p class="lbl">On your other device:</p>')
    parts.append('        <div class="steps">')
    parts.append("          <p>Click <strong>Sync</strong></p>")
    parts.append("          <p>Click <strong>Send to another device</strong></p>")
    parts.append("        </div>")
    parts.append('        <p class="recv-lbl">On this device:</p>')
    parts.append('        <div class="steps"><p>Enter the code</p></div>')
    pin_spans = "<span></span>" * 6
    parts.append(
        f'        <div class="pin-wrap" id="pin-wrap">'
        f'<div class="pin" id="pin-boxes">{pin_spans}</div>'
        f'<input class="pin-real" id="pin-input" type="text" inputmode="numeric" maxlength="6" autocomplete="off"/>'
        f"</div>"
    )
    parts.append(
        '        <button type="button" class="btn" onclick="submitPin()">Connect</button>'
    )
    parts.append("      </div>")
    parts.append("    </div>")
    parts.append("  </div>")

    qr_js = (ICONS_DIR.parent / "qrcode.min.js").read_text(encoding="utf-8")
    parts.append(f"  <script>{qr_js}</script>")
    parts.append("  <script>")

    # Emit timetable section data for JS
    import json

    sections_json = json.dumps(
        [
            {"date": td["date"], "period": td["period"], "key": td["key"]}
            for td in timetable_data
        ]
    )
    parts.append(f"    const TT_SECTIONS = {sections_json};")
    parts.append(f"    const TT_DATES = {json.dumps(dates_seen)};")

    parts.append(
        """
    // Day/period switching
    let currentDate = TT_DATES[0];
    let currentPeriod = null;

    function getPeriodsForDate(date) {
      return TT_SECTIONS.filter(s => s.date === date).map(s => s.period);
    }

    function showPanel(date, period) {
      document.querySelectorAll('.timetable-panel').forEach(p => p.classList.remove('active'));
      const id = 'panel-' + date + '-' + period;
      const panel = document.getElementById(id);
      if (panel) panel.classList.add('active');
      requestAnimationFrame(truncateNames);
      updateNowLine();
    }

    // Sticky fade observers for floor headers (same as main page)
    document.querySelectorAll('.floor-header-bar').forEach(el => {
      const top = parseFloat(getComputedStyle(el).top) || 0;
      const s = document.createElement('div');
      s.style.cssText = 'height:0;width:0;pointer-events:none;visibility:hidden;position:relative;top:-' + top + 'px';
      el.parentNode.insertBefore(s, el);
      new IntersectionObserver(([e]) => {
        el.classList.toggle('stuck', e.intersectionRatio === 0);
      }, {threshold: 0}).observe(s);
    });

    function renderPeriodTabs(date) {
      const periods = getPeriodsForDate(date);
      const div = document.getElementById('period-tabs');
      div.innerHTML = '';
      if (periods.length <= 1) {
        currentPeriod = periods[0] || 'day';
        showPanel(date, currentPeriod);
        return;
      }
      periods.forEach((p, i) => {
        const btn = document.createElement('button');
        btn.className = 'period-tab' + (i === 0 ? ' active' : '');
        btn.textContent = p === 'day' ? 'Day' : 'Night';
        btn.onclick = function() {
          div.querySelectorAll('.period-tab').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          currentPeriod = p;
          showPanel(date, p);
        };
        div.appendChild(btn);
      });
      currentPeriod = periods[0];
      showPanel(date, currentPeriod);
    }

    function switchDay(date, btn) {
      currentDate = date;
      document.querySelectorAll('.day-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderPeriodTabs(date);
    }

    // Init first day
    renderPeriodTabs(TT_DATES[0]);

    // Truncate names in DOM so rendering changes can't un-truncate them
    function truncateNames() {
      document.querySelectorAll('.tt-name').forEach(el => {
        if (el.clientWidth === 0) return;
        const full = el.dataset.full || el.textContent;
        el.dataset.full = full;
        el.textContent = full;
        if (el.scrollWidth > el.clientWidth) {
          let lo = 0, hi = full.length;
          while (hi - lo > 1) {
            const mid = (lo + hi) >> 1;
            el.textContent = full.slice(0, mid) + '…';
            if (el.scrollWidth > el.clientWidth) hi = mid; else lo = mid;
          }
          el.textContent = full.slice(0, lo) + '…';
        }
      });
    }
    truncateNames();
    new ResizeObserver(truncateNames).observe(document.body);

    // Artist popup
    const popup = document.getElementById('tt-popup');

    function _link(href, svg, label) {
      return '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + svg + ' ' + (label || '') + '</a>';
    }

    const SVG_IG = `"""
        + SVG_IG.replace("`", "\\`").replace("${", "\\${")
        + """`;
    const SVG_SC = `"""
        + SVG_SC.replace("`", "\\`").replace("${", "\\${")
        + """`;
    const SVG_SP = `"""
        + SVG_SP.replace("`", "\\`").replace("${", "\\${")
        + """`;
    const SVG_LT = `"""
        + SVG_LT.replace("`", "\\`").replace("${", "\\${")
        + """`;
    const SVG_YT = `"""
        + SVG_YT.replace("`", "\\`").replace("${", "\\${")
        + """`;

    document.querySelectorAll('.tt-block').forEach(block => {
      block.addEventListener('click', e => {
        if (e.target.closest('.tt-heart')) return;
        e.stopPropagation();
        closePopup();
        const d = block.dataset;
        const artists = JSON.parse(d.artists || '[]');
        const px = e.clientX, py = e.clientY;
        const timetable = block.closest('.timetable');
        const tr = timetable ? timetable.getBoundingClientRect() : {left:0, right:window.innerWidth, top:0, bottom:window.innerHeight};
        requestAnimationFrame(() => {
          document.getElementById('popup-meta').textContent = d.time + ' · ' + d.floor;
          let artistsHtml = '';
          artists.forEach(a => {
            const photo = a.photo
              ? '<img class="popup-photo" src="' + a.photo + '" alt="' + a.name + '">'
              : '<div class="popup-photo-placeholder"></div>';
            let links = '';
            if (a.ig) links += _link(a.ig, SVG_IG, a.igF);
            if (a.sc) links += _link(a.sc, SVG_SC, a.scF);
            if (a.sp) links += _link(a.sp, SVG_SP, a.spL);
            if (a.lt) links += _link(a.lt, SVG_LT, '');
            if (a.yt) links += _link(a.yt, SVG_YT, '');
            artistsHtml += '<div class="popup-artist">' + photo + '<div><div class="popup-name">' + a.name + '</div><div class="links">' + links + '</div></div></div>';
          });
          document.getElementById('popup-artists').innerHTML = artistsHtml;
          let left = px + 12;
          let top = py - 20;
          const pw = 320, ph = 250;
          if (left + pw > tr.right) left = px - pw - 12;
          if (left < tr.left) left = tr.left;
          if (top + ph > tr.bottom) top = tr.bottom - ph;
          if (top < tr.top) top = tr.top;
          popup.style.left = left + 'px';
          popup.style.top = top + 'px';
          popup.classList.add('open');
        });
      });
    });

    function closePopup() {
      popup.classList.remove('open');
    }
    document.addEventListener('click', e => {
      if (popup.classList.contains('open') && !e.target.closest('.tt-popup')) closePopup();
    });
    document.addEventListener('scroll', () => closePopup(), {passive: true});
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') closePopup();
    });

    // Hearts
    const API = '/api';
    if (localStorage.getItem('stc_edit_code') && !localStorage.getItem('stc_session_id')) {
      localStorage.setItem('stc_session_id', localStorage.getItem('stc_edit_code'));
      localStorage.removeItem('stc_edit_code');
    }
    if (localStorage.getItem('stc_share_code') && !localStorage.getItem('stc_share_token')) {
      localStorage.setItem('stc_share_token', localStorage.getItem('stc_share_code'));
      localStorage.removeItem('stc_share_code');
    }
    let sessionId = localStorage.getItem('stc_session_id');
    let shareToken = localStorage.getItem('stc_share_token');
    let localPicks; try { localPicks = new Set(JSON.parse(localStorage.getItem('stc_picks') || '[]')); } catch { localPicks = new Set(); localStorage.removeItem('stc_picks'); }
    let readOnly = false;
    let filterActive = false;

    function saveLocal() {
      localStorage.setItem('stc_picks', JSON.stringify([...localPicks]));
      updateUI();
    }

    function updateUI() {
      document.querySelectorAll('[data-artist-id]').forEach(el => {
        el.classList.toggle('hearted', localPicks.has(el.dataset.artistId));
      });
    }

    function applyHearts() {
      document.querySelectorAll('.tt-heart').forEach(btn => {
        const id = btn.closest('[data-artist-id]').dataset.artistId;
        const active = localPicks.has(id);
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', active);
      });
      updateUI();
    }

    function toggleFilter(btn) {
      filterActive = !filterActive;
      track(filterActive ? 'filter-on' : 'filter-off');
      document.body.classList.toggle('filter-active', filterActive);
      btn.classList.toggle('active', filterActive);
    }

    let _sessionPromise = null;
    async function ensureSession() {
      if (sessionId) return;
      if (_sessionPromise) return _sessionPromise;
      _sessionPromise = (async () => {
        try {
          const res = await fetch(API + '/session', {method: 'POST'});
          if (!res.ok) return;
          const data = await res.json();
          sessionId = data.session_id;
          shareToken = data.share_token;
          localStorage.setItem('stc_session_id', sessionId);
          localStorage.setItem('stc_share_token', shareToken);
          connectWS(sessionId);
          for (const id of localPicks) {
            fetch(API + '/session/' + sessionId + '/pick/' + id, {method: 'POST'}).catch(() => {});
          }
        } catch {}
        finally { _sessionPromise = null; }
      })();
      return _sessionPromise;
    }

    function track(event, data) { if (typeof umami !== 'undefined') umami.track(event, data); }

    async function toggleHeart(btn) {
      if (readOnly) return;
      const el = btn.closest('[data-artist-id]');
      const id = el.dataset.artistId;
      const adding = !localPicks.has(id);
      const name = el.dataset.name || id;
      track(adding ? 'heart' : 'unheart', {artist: name});

      if (adding) localPicks.add(id); else localPicks.delete(id);
      btn.classList.toggle('active', adding);
      btn.setAttribute('aria-pressed', adding);
      el.classList.toggle('hearted', adding);
      saveLocal();

      await ensureSession();
      if (!sessionId) return;

      try {
        const method = adding ? 'POST' : 'DELETE';
        const res = await fetch(API + '/session/' + sessionId + '/pick/' + id, {method});
        if (res.status === 404) {
          sessionId = null; shareToken = null;
          localStorage.removeItem('stc_session_id');
          localStorage.removeItem('stc_share_token');
          await ensureSession();
          return;
        }
        if (!res.ok && res.status !== 204) {
          if (adding) localPicks.delete(id); else localPicks.add(id);
          btn.classList.toggle('active', !adding);
          btn.setAttribute('aria-pressed', !adding);
          el.classList.toggle('hearted', !adding);
          saveLocal();
        }
      } catch {}
    }

    async function loadFromServer(code) {
      try {
        const res = await fetch(API + '/session/' + code);
        if (!res.ok) return;
        const data = await res.json();
        localPicks = new Set(data.picks);
        readOnly = data.readonly;
        if (!readOnly) {
          sessionId = data.session_id || null;
          shareToken = data.share_token || null;
          if (sessionId) localStorage.setItem('stc_session_id', sessionId); else localStorage.removeItem('stc_session_id');
          if (shareToken) localStorage.setItem('stc_share_token', shareToken); else localStorage.removeItem('stc_share_token');
          saveLocal();
        }
        applyHearts();
        if (readOnly) {
          document.querySelectorAll('.tt-heart').forEach(b => b.style.pointerEvents = 'none');
          filterActive = true;
          document.body.classList.add('filter-active');
          document.getElementById('btn-filter').style.display = 'none';
        }
      } catch {}
    }

    async function reconcile() {
      if (!sessionId || readOnly) return;
      try {
        const res = await fetch(API + '/session/' + sessionId);
        if (res.status === 404) {
          sessionId = null; shareToken = null;
          localStorage.removeItem('stc_session_id');
          localStorage.removeItem('stc_share_token');
          await ensureSession();
          return;
        }
        if (!res.ok) return;
        const data = await res.json();
        const serverPicks = new Set(data.picks);
        const syncs = [];
        for (const id of localPicks) {
          if (!serverPicks.has(id)) syncs.push(fetch(API + '/session/' + sessionId + '/pick/' + id, {method: 'POST'}).catch(() => {}));
        }
        await Promise.all(syncs);
        for (const id of serverPicks) localPicks.add(id);
        saveLocal();
        applyHearts();
      } catch {}
    }

    // WebSocket real-time sync
    let _ws = null;
    let _wsDelay = 2000;
    function connectWS(code) {
      if (_ws) { try { _ws.close(); } catch {} }
      if (!code) return;
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      _ws = new WebSocket(proto + '//' + location.host + '/ws/' + code);
      _ws.onopen = () => { _wsDelay = 2000; };
      _ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.sync_complete) {
            track('sync-complete');
            if (_syncTimer) { clearInterval(_syncTimer); _syncTimer = null; }
            document.getElementById('sync-pending').style.display = 'none';
            document.getElementById('sync-done').style.display = '';
          }
          if (data.picks) {
            localPicks = new Set(data.picks);
            saveLocal();
            applyHearts();
          }
        } catch {}
      };
      _ws.onclose = (ev) => { if (ev.code === 1008) return; setTimeout(() => { const cur = sessionId || shareToken; if (cur === code) connectWS(code); }, _wsDelay + Math.random() * 1000); _wsDelay = Math.min(_wsDelay * 2, 60000); };
    }

    // Modal system
    let _modalTrigger = null;
    function _fitToViewport() {
      const m = document.querySelector('.modal-overlay.open');
      if (!m || !window.visualViewport) return;
      const box = m.querySelector('.modal-box');
      const vh = visualViewport.height;
      const ot = visualViewport.offsetTop;
      const bh = box.offsetHeight;
      box.style.transform = 'translateY(' + (ot + (vh - bh) / 2 - (window.innerHeight - bh) / 2) + 'px)';
    }
    function openDialog(id) {
      _modalTrigger = document.activeElement;
      document.body.style.top = '-' + window.scrollY + 'px';
      document.documentElement.classList.add('scroll-locked');
      document.getElementById(id).classList.add('open');
      if (window.visualViewport) {
        visualViewport.addEventListener('resize', _fitToViewport);
        visualViewport.addEventListener('scroll', _fitToViewport);
      }
    }
    function closeDialog(id) {
      if (window.visualViewport) {
        visualViewport.removeEventListener('resize', _fitToViewport);
        visualViewport.removeEventListener('scroll', _fitToViewport);
      }
      const m = document.getElementById(id);
      m.classList.remove('open');
      const box = m.querySelector('.modal-box');
      box.style.transform = '';
      if (_syncTimer) { clearInterval(_syncTimer); _syncTimer = null; }
      pinField.value = '';
      syncPinDisplay();
      const scrollY = document.body.style.top;
      document.body.style.top = '';
      document.documentElement.classList.remove('scroll-locked');
      window.scrollTo(0, parseInt(scrollY || '0') * -1);
      if (_modalTrigger) { _modalTrigger.blur(); _modalTrigger = null; }
    }
    document.querySelectorAll('.modal-overlay').forEach(ov => {
      ov.addEventListener('click', e => { if (e.target === ov) closeDialog(ov.id); });
      ov.addEventListener('touchmove', e => {
        if (!e.target.closest('.modal-box')) e.preventDefault();
      }, { passive: false });
    });
    document.addEventListener('keydown', e => {
      const modal = document.querySelector('.modal-overlay.open');
      if (!modal) return;
      if (e.key === 'Escape') { closeDialog(modal.id); return; }
      if (e.key !== 'Tab') return;
      const focusable = [...modal.querySelectorAll('button, input, [href], select, textarea, [tabindex]:not([tabindex="-1"])')];
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

    function loadQR(id, url) {
      const c = document.getElementById(id);
      if (!c || typeof qrcode === 'undefined') return;
      const qr = qrcode(0, 'M');
      qr.addData(url);
      qr.make();
      const count = qr.getModuleCount();
      const size = c.width;
      const cellSize = size / count;
      const ctx = c.getContext('2d');
      ctx.clearRect(0, 0, size, size);
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, size, size);
      ctx.fillStyle = '#000';
      for (let r = 0; r < count; r++)
        for (let col = 0; col < count; col++)
          if (qr.isDark(r, col))
            ctx.fillRect(Math.round(col * cellSize), Math.round(r * cellSize), Math.ceil(cellSize), Math.ceil(cellSize));
    }

    // Share modal
    const shareLink = document.getElementById('share-link');
    shareLink.addEventListener('click', () => {
      shareLink.select();
      const url = shareLink.value;
      navigator.clipboard.writeText(url).then(() => {
        track('share-copy');
        shareLink.classList.add('copied');
        shareLink.value = 'Copied!';
        setTimeout(() => { shareLink.value = url; shareLink.classList.remove('copied'); }, 1500);
      });
    });
    function openShareModal() {
      if (!shareToken) { alert('Heart an artist first.'); return; }
      track('share-open');
      shareLink.value = location.origin + '/?code=' + shareToken;
      openDialog('m-share');
    }

    // Sync modal
    let _syncTimer = null;
    async function generateSyncPin() {
      const d = document.getElementById('pin-display');
      const exp = document.getElementById('sync-expiry');
      const qr = document.getElementById('sync-qr');
      d.innerHTML = '';
      exp.textContent = '';
      if (qr) qr.getContext('2d').clearRect(0, 0, qr.width, qr.height);
      if (_syncTimer) { clearInterval(_syncTimer); _syncTimer = null; }
      try {
        const res = await fetch(API + '/session/' + sessionId + '/sync-pin', {method: 'POST'});
        if (!res.ok) return;
        const data = await res.json();
        for (const ch of data.pin) { const s = document.createElement('span'); s.textContent = ch; d.appendChild(s); }
        loadQR('sync-qr', location.origin + '/?sync=' + data.pin);
        const deadline = Date.now() + 300000;
        function tick() {
          const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
          if (left === 0) {
            clearInterval(_syncTimer); _syncTimer = null;
            d.innerHTML = '';
            if (qr) qr.getContext('2d').clearRect(0, 0, qr.width, qr.height);
            exp.innerHTML = 'QR code and PIN expired. <a onclick="generateSyncPin()">Generate new ones</a>';
            return;
          }
          if (left >= 60) { const m = Math.ceil(left / 60); exp.textContent = 'Valid for ' + m + ' min'; }
          else exp.textContent = 'Valid for ' + left + 's';
        }
        tick();
        _syncTimer = setInterval(tick, 1000);
      } catch {}
    }
    async function openSyncModal() {
      await ensureSession();
      if (!sessionId) { alert('Heart an artist first.'); return; }
      track('sync-open');
      document.getElementById('sync-pending').style.display = '';
      document.getElementById('sync-done').style.display = 'none';
      document.querySelectorAll('#m-sync .tabs button').forEach(b => b.classList.remove('on'));
      document.querySelector('#m-sync .tabs button').classList.add('on');
      document.getElementById('p-send').classList.add('on');
      document.getElementById('p-recv').classList.remove('on');
      await generateSyncPin();
      openDialog('m-sync');
    }
    function syncTab(t, btn) {
      btn.closest('.tabs').querySelectorAll('button').forEach(b => b.classList.remove('on'));
      btn.classList.add('on');
      document.getElementById('p-send').classList.toggle('on', t === 'send');
      document.getElementById('p-recv').classList.toggle('on', t === 'recv');
    }

    // Pin input
    const pinField = document.getElementById('pin-input');
    const pinBoxes = [...document.querySelectorAll('#pin-boxes span')];
    function syncPinDisplay() {
      const val = pinField.value;
      const cursor = val.length >= 6 ? 5 : val.length;
      pinBoxes.forEach((b, i) => {
        b.textContent = val[i] || '';
        b.classList.toggle('filled', i < val.length);
        b.classList.toggle('active', i === cursor);
      });
    }
    pinField.addEventListener('input', () => {
      pinField.value = pinField.value.replace(/\\D/g, '').slice(0, 6);
      syncPinDisplay();
    });
    pinField.addEventListener('focus', () => { document.getElementById('pin-wrap').classList.add('focused'); syncPinDisplay(); });
    pinField.addEventListener('blur', () => { document.getElementById('pin-wrap').classList.remove('focused'); pinBoxes.forEach(b => b.classList.remove('active')); });
    document.getElementById('pin-wrap').addEventListener('click', () => pinField.focus());
    async function submitPin() {
      const pin = pinField.value.replace(/\\D/g, '');
      if (pin.length !== 6) return;
      closeDialog('m-sync');
      pinField.value = '';
      syncPinDisplay();
      await exchangeSyncPin(pin);
    }

    async function exchangeSyncPin(pin) {
      try {
        const res = await fetch(API + '/sync/' + pin, {method: 'POST'});
        if (!res.ok) return;
        const data = await res.json();
        localPicks = new Set(data.picks);
        readOnly = data.readonly;
        if (!readOnly) {
          sessionId = data.session_id || null;
          shareToken = data.share_token || null;
          if (sessionId) localStorage.setItem('stc_session_id', sessionId); else localStorage.removeItem('stc_session_id');
          if (shareToken) localStorage.setItem('stc_share_token', shareToken); else localStorage.removeItem('stc_share_token');
          saveLocal();
        }
        applyHearts();
        if (sessionId) connectWS(sessionId);
      } catch {}
    }

    // Now line
    function updateNowLine() {
      document.querySelectorAll('[data-now-line]').forEach(el => el.style.display = 'none');
      const panel = document.querySelector('.timetable-panel.active');
      if (!panel) return;
      const date = panel.dataset.date;
      const gridStart = parseInt(panel.dataset.gridStart);
      const gridEnd = parseInt(panel.dataset.gridEnd);
      const isNight = panel.dataset.isNight === '1';
      const now = new Date();
      const yyyy = now.getFullYear();
      const mm = String(now.getMonth() + 1).padStart(2, '0');
      const dd = String(now.getDate()).padStart(2, '0');
      const today = yyyy + '-' + mm + '-' + dd;
      const yesterday = new Date(now.getTime() - 86400000);
      const yy = yesterday.getFullYear();
      const ym = String(yesterday.getMonth() + 1).padStart(2, '0');
      const yd = String(yesterday.getDate()).padStart(2, '0');
      const yesterdayStr = yy + '-' + ym + '-' + yd;
      let nowMin = now.getHours() * 60 + now.getMinutes();
      let match = false;
      if (isNight) {
        if (date === today && nowMin >= gridStart && nowMin < 1440) match = true;
        if (date === yesterdayStr && nowMin < 12 * 60) { nowMin += 1440; match = true; }
      } else {
        if (date === today && nowMin >= gridStart && nowMin <= gridEnd) match = true;
      }
      if (!match || nowMin < gridStart || nowMin > gridEnd) return;
      const row = (nowMin - gridStart) + 1;
      const line = panel.querySelector('[data-now-line]');
      if (line) { line.style.display = ''; line.style.gridRow = row + ''; }
    }
    setInterval(updateNowLine, 60000);

    // Init
    (async () => {
      const p = new URLSearchParams(location.search);
      const syncPin = p.get('sync');
      const c = p.get('code');
      if (syncPin) {
        history.replaceState(null, '', location.pathname);
        await exchangeSyncPin(syncPin);
      } else if (c) {
        history.replaceState(null, '', location.pathname);
        await loadFromServer(c); connectWS(c);
      }
      else if (sessionId) { await reconcile(); connectWS(sessionId); }
      else {
        try {
          const res = await fetch(API + '/me');
          if (res.ok) {
            const data = await res.json();
            localPicks = new Set(data.picks);
            sessionId = data.session_id;
            shareToken = data.share_token;
            localStorage.setItem('stc_session_id', sessionId);
            localStorage.setItem('stc_share_token', shareToken);
            saveLocal();
            connectWS(sessionId);
          }
        } catch {}
      }
      applyHearts();
      updateNowLine();
    })();
    """
    )
    parts.append("  </script>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)
