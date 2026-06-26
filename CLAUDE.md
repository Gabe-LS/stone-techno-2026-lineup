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
3. `scraper/overrides.toml` provides manual corrections applied after scraping, before enrichment
4. Output is a single HTML file (`output/lineup.html`) + AVIF photos (`output/photos/`) + `output/timetable.json`

### Key files

| File | Role |
|---|---|
| `scraper/scrape.py` | Lineup parser + SoundCloud/Instagram/Spotify scrapers |
| `scraper/db.py` | SQLite schema, upserts, overrides, queries |
| `scraper/images.py` | Photo resize (pyvips lanczos3) + AVIF encode (ssimulacra2 target 78) |
| `scraper/render.py` | HTML generation — line-up list + timetable grid, CSS, JS, modals, hearts, schedule, push notifications |
| `scraper/timetable_json.py` | Generates `timetable.json` mapping schedule slot UUIDs to set times (used by push notification scheduler) |
| `seed_timetable.py` | Seeds fake timetable data (floors + time slots) for development |
| `server/api.py` | FastAPI app — favorites + schedule API + WebSocket sync + push notification scheduler |
| `server/static/sw.js` | Service worker — handles push events and notification click navigation |
| `server/static/manifest.json` | PWA manifest — enables Add to Home Screen and push on iOS |

### Two deploy paths

- **Content** (HTML + photos + timetable.json + sw.js + manifest.json): `--deploy` flag rsyncs to VPS static dir. No container restart — files are volume-mounted.
- **Server code**: push to `main` with changes in `server/` triggers GitHub Actions → SSH → `git pull` + `docker compose up -d --build`.

## Generated Artifacts (gitignored)

- `lineup.db` — SQLite cache of artists, sections, follower counts
- `output/lineup.html` — generated page (~6000+ lines with timetable)
- `output/photos/*.avif` — processed artist photos (~100 files)
- `output/timetable.json` — slot UUID → set time mapping for push notifications

These are regenerable. The source of truth is the live website + `overrides.toml`.

## Timetable View

The page includes both a line-up list and a timetable grid, toggled via the command bar. The timetable appears automatically when artists have `start_time`/`end_time` data in `artist_sections`.

- **Desktop**: CSS grid with sticky floor headers and time labels
- **Mobile**: HTML `<table>` with sticky `<th>`/`<td>` (no diagonal scroll); two nested divs (`tt-v-scroll` + `tt-h-scroll`) with custom JS touch handler (axis locking, momentum with friction 0.965, ease-in acceleration); floor header bar synced via `scrollLeft`
- **B2B sets**: Multiple artists in the same time slot render as one card with per-artist hearts
- **Schedule**: Calendar icon on each card, server-synced via `/api/session/{code}/schedule/{slot_id}`
- **ICS export**: "Add to calendar" link on each card — generates .ics with timezone (Europe/Berlin), 10-min alarm, floor as location
- **Fake data**: `python seed_timetable.py` populates 5 day floors + 2 night floors (Grand Hall, Mischanlage) with time slots
- **Hamburger menu**: mobile-only, shows/hides based on current view, preserves view in localStorage across reloads
- **Artist schedule notes**: every list-view card shows floor + time; artists playing multiple slots get an "Also" line with cross-references

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

Web Push notifications alert users 10 minutes before their scheduled sets start.

### How it works

1. User schedules sets via the calendar icon on timetable cards
2. User enables notifications (bell icon on desktop, "Enable notifications" in mobile hamburger menu)
3. Browser creates a push subscription (VAPID-authenticated) and sends it to the server
4. Server background scheduler runs every 60s, checks `timetable.json` for upcoming sets, matches against sessions' schedule arrays, and sends push via `pywebpush`
5. Service worker receives push → shows native notification with artist name, floor, and time
6. Notification click opens the app on the timetable view (uses Cache Storage flag as iOS workaround)

### Platform support

| Platform | Status |
|---|---|
| Chrome / Edge (desktop) | Works out of the box |
| Brave (desktop) | Requires "Use Google services for push messaging" in settings — app shows instructions modal |
| Firefox (desktop) | Works out of the box (uses Mozilla push service) |
| Safari (iOS PWA) | Works after Add to Home Screen (iOS 16.4+) |
| Chrome / Firefox (iOS) | Works after Add to Home Screen |
| Brave (iOS) | Must switch to Safari first — app shows copy-link + instructions modal |

### VAPID keys

Generated once via `server/generate_vapid_keys.py`. Stored as env vars on the VPS (`VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT`) in `server/.env`, read by Docker Compose.

### Push API endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/push/vapid-key` | Returns VAPID public key for client subscription |
| `POST` | `/api/session/{code}/push/subscribe` | Store a push subscription |
| `DELETE` | `/api/session/{code}/push/subscribe` | Remove a push subscription |
| `GET` | `/api/session/{code}/push/status` | Check if subscriptions exist |

### Deduplication

`sent_notifications` table tracks `(session_id, slot_id)` pairs to prevent re-sending. Pruned after 7 days. Dead push subscriptions (HTTP 404/410) are automatically removed on failed send.

### Re-sync on load

On every page load, if push is enabled, the client re-sends its subscription to the server. This recovers from server DB purges or PWA reinstalls without requiring the user to toggle notifications off and on.
