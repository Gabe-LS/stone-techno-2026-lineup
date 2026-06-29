# Stone Techno Companion

Festival lineup scraper + enrichment pipeline + static site generator with a real-time favorites API and push notifications.

## Quick Reference

```bash
# Full pipeline (scrape + enrich + photos + generate HTML)
python stone_techno_companion.py

# Regenerate HTML only (fast — no network, no scraping)
python stone_techno_companion.py --render-only --no-photos

# Deploy content to production (rsync, no container restart needed)
python stone_techno_companion.py --render-only --deploy
```

## System Dependencies

These are not pip-installable and must be present on the system:

- **Playwright + Chromium**: `pip install playwright && playwright install chromium`
- **libvips**: `brew install vips` (macOS) — required by pyvips for image processing
- **ssimulacra2**: binary must be in PATH — used for perceptual quality targeting during AVIF encoding

Python dependencies: `playwright`, `beautifulsoup4`, `pyvips` (scraper); `fastapi`, `uvicorn[standard]`, `pywebpush` (server).

## Architecture

### Data flow

1. `stone_techno_companion.py` orchestrates: scrape → enrich → process photos → render HTML + timetable.json
2. `lineup.db` (SQLite) caches all scraped data — follower counts and photos are only fetched once unless `--refresh-*` flags are used
3. `scraper/overrides.toml` provides manual corrections (artist links) and editorial data (floor curators) applied after scraping, before enrichment
4. Output is a single HTML file (`output/lineup.html`) + AVIF photos (`output/photos/`) + `output/timetable.json`

### Key files

| File | Role |
|---|---|
| `scraper/scrape.py` | Lineup parser + SoundCloud/Instagram/Spotify scrapers |
| `scraper/db.py` | SQLite schema, upserts, overrides, queries |
| `scraper/images.py` | Photo resize (pyvips lanczos3) + AVIF encode (ssimulacra2 target 78) |
| `scraper/render.py` | HTML generation — line-up list + timetable grid, CSS, JS, modals, hearts, schedule, push notifications. SVG icons deduplicated via `<symbol>`/`<use>` sprite |
| `scraper/timetable_json.py` | Generates `timetable.json` mapping schedule slot UUIDs to set times (used by push notification scheduler and ICS endpoint) |
| `seed_timetable.py` | Seeds fake timetable data (floors + time slots) for development |
| `server/api.py` | FastAPI app — favorites + schedule API + WebSocket sync + push notification scheduler + ICS calendar export |
| `server/static/sw.js` | Service worker — handles push events and notification click navigation |
| `server/static/manifest.json` | PWA manifest — enables Add to Home Screen and push on iOS |

### Two deploy paths

- **Content** (HTML + photos + timetable.json + sw.js + manifest.json): `--deploy` flag rsyncs to VPS static dir. No container restart — files are volume-mounted.
- **Server code**: push to `main` with changes in `server/` triggers GitHub Actions → SSH → `git pull` + `docker compose up -d --build`.

## Generated Artifacts (gitignored)

- `lineup.db` — SQLite cache of artists, sections, follower counts
- `output/lineup.html` — generated page (~580KB with timetable)
- `output/photos/*.avif` — processed artist photos (~100 files)
- `output/timetable.json` — slot UUID → set time mapping for push notifications

These are regenerable. The source of truth is the live website + `overrides.toml`.

## Timetable View

The page includes both a line-up list and a timetable grid, toggled via the command bar. The timetable appears automatically when artists have `start_time`/`end_time` data in `artist_sections`.

- **Desktop**: CSS grid with sticky floor headers and time labels
- **Mobile**: HTML `<table>` with native scroll (`overflow: auto` on single `tt-v-scroll` container); sticky `<thead>` for floor headers (no JS sync needed); `table-layout: fixed` with `--row-h` CSS variable for row height; grid lines via CSS `repeating-linear-gradient`; dynamic `--row-h` (10px or 14px) based on artist density per slot
- **B2B sets**: Multiple artists in the same time slot render as one card with per-artist hearts
- **Schedule**: Calendar icon on each card, server-synced via `/api/session/{code}/schedule/{slot_id}`
- **ICS export**: "Add to calendar" link on each card — server endpoint `GET /ics/{slot_id}` serves `.ics` file with `Content-Type: text/calendar` for native iOS/Android calendar integration
- **Fake data**: `python seed_timetable.py` populates 5 day floors + 2 night floors (Grand Hall, Mischanlage) with time slots
- **Hamburger menu**: mobile-only, shows/hides based on current view, preserves view in localStorage across reloads
- **Artist schedule notes**: every list-view card shows floor + time; artists playing multiple slots get an "Also" line with cross-references
- **Floor curators**: "curated by" / "hosted by" annotations below floor name pills, per-day per-floor. Data lives in `[floor_curators]` section of `scraper/overrides.toml` keyed as `"YYYY-MM-DD.location_id"`. Desktop uses `<span>` inside `.floor-header`; mobile uses `<span class="floor-curator">` inside `<th>`. The `.floor-header` div has `background: none !important` to prevent the generic `.floor-X` card color from bleeding onto the container — floor color is on `> span:first-child` only

### Design system

- **Colors**: CSS variables in `:root` — `--color-text`, `--color-muted` (#717171, 4.88:1 AA), `--color-muted-icon` (#888, 3.54:1), `--color-accent`, `--color-schedule`, `--color-line-hour`, `--color-line-half`
- **Floor colors**: 7 pastel colors (`--floor-*`) evenly spaced around the hue wheel (s=0.21), min pair distance 31, rainbow sequence in alphabetical order. Cards use `color-mix()` for 88% opacity
- **Font scale**: 6 steps via variables — `--font-2xl` (2em) through `--font-xs` (0.75em/12px minimum). No text below 12px for accessibility
- **Contrast**: all text/icon colors pass WCAG 2.1 AA

Floor order is defined in `canonical_floor_order` in `render.py` (alphabetical, 7 floors).

## Working on the HTML/CSS/JS

All frontend code lives in `scraper/render.py` as Python string concatenation. There is no separate HTML/CSS/JS file to edit. After changes, regenerate with `--render-only --no-photos` and open `output/lineup.html`.

## Server

The FastAPI server (`server/api.py`) serves static files and provides the favorites + schedule API. Sessions are identified by 128-bit URL-safe tokens (`secrets.token_urlsafe(16)`): `session_id` for read-write, `share_token` for read-only. Cross-device sync uses ephemeral 6-digit PINs (5-min TTL, single-use, one active per session). Picks and schedule are stored as JSON arrays in SQLite with atomic add/remove via `json_each`/`json_group_array`. Real-time sync uses WebSocket at `/ws/{code}`. Schedule endpoints mirror picks: `POST/DELETE /api/session/{code}/schedule/{slot_id}`.

Production: Docker container on a DigitalOcean VPS behind Caddy (auto-TLS). Database at `server/data/hearts.db` is volume-mounted for persistence. VAPID keys for push stored in `.env` on the VPS.

## Push Notifications

See README for full push documentation (platform support, VAPID setup, API endpoints).

Implementation notes:

- **Scheduler**: background task in `api.py` runs every 60s, matches `timetable.json` slots against sessions' schedule arrays, sends via `pywebpush`
- **Dedup**: `sent_notifications` table tracks `(session_id, slot_id)` pairs. Pruned after 7 days. Dead subscriptions (HTTP 404/410) auto-removed on failed send
- **Re-sync on load**: client re-sends its push subscription on every page load to recover from DB purges or PWA reinstalls
- **iOS workaround**: notification click uses a Cache Storage flag to open on the timetable view (service worker can't access localStorage)
