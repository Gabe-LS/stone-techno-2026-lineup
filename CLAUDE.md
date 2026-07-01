# Stone Techno Companion

Multi-event festival companion tool: scraper + enrichment pipeline + static site generator with real-time favorites, push notifications, and cross-device sync.

## Quick Reference

```bash
# Full pipeline (scrape + enrich + photos + generate HTML)
python stone_techno_companion.py

# Regenerate HTML only (fast — no network, no scraping)
python stone_techno_companion.py --render-only --no-photos

# Fetch YouTube sets for all artists (separate step, ~50 min)
python fetch_videos.py

# Deploy content to production (rsync, no container restart needed)
python stone_techno_companion.py --render-only --deploy

# Preview locally (required — file:// won't work)
cd output && python3 -m http.server 8321
# Then open http://localhost:8321/lineup.html

# Run for a specific event
python stone_techno_companion.py --event-id stone-techno-2026 --event-name "Stone Techno" --event-edition "2026"

# Migrate old DB to new schema (one-time, creates backup)
python migrate_db.py

# Run full server locally (lineup + chat)
cd server && export $(cat .env | xargs) && uvicorn api:app --port 8080
# Open http://localhost:8080/ (lineup) and http://localhost:8080/chat (chat)

# Run tests
python -m pytest tests/ -v
```

## Local Development

**Always preview via HTTP, never `file://`.** The page uses `fetch()` for lazy-loaded bios and API calls. Browsers block fetch from `file://` origins (CORS).

**For lineup only**: `cd output && python3 -m http.server 8321` — expected 404s for `/manifest.json`, `/sw.js`, `/api/me`.

**For lineup + chat**: run the full FastAPI server: `cd server && export $(cat .env | xargs) && uvicorn api:app --port 8080`. Symlinks in `server/static/` point to `output/` files so lineup reflects latest build.

**Chat requires auth**: sign in via email magic link at `/chat`. For local dev, set `CHAT_BASE_URL=http://localhost:8080` in `.env` so the magic link points to localhost.

## System Dependencies

Not pip-installable, must be present on the system:

- **Playwright + Chromium**: `pip install playwright && playwright install chromium`
- **libvips**: `brew install vips` (macOS) — required by pyvips for image processing
- **ssimulacra2**: binary must be in PATH — perceptual quality targeting for AVIF encoding

Python dependencies: `playwright`, `beautifulsoup4`, `pyvips` (scraper); `fastapi`, `uvicorn[standard]`, `pywebpush` (server); `yt-dlp` (video discovery); `markdown` (bio rendering); `email-validator` (auth); `maileroo` (magic link emails).

System: `ffmpeg` + `ffprobe` must be in PATH for video upload (frame extraction for moderation).

## Architecture

### Data flow

1. `stone_techno_companion.py` orchestrates: scrape → enrich → process photos → render HTML + timetable.json + bios.json
2. `lineup.db` (SQLite, WAL mode, FK enforcement) is the single source of truth — artists, links, sets, schedule, locations, events
3. `scraper/overrides.toml` provides manual corrections (artist links), editorial data (floor curators), and YouTube video overrides — applied as patches to the DB
4. `fetch_videos.py` discovers YouTube sets via yt-dlp and writes to the `artist_sets` table
5. Output: `lineup.html` (~650 KB) + `bios.json` (~200 KB, lazy-loaded) + `timetable.json` + `photos/*.avif` + `thumbs/*.avif`

### Database schema

```
events            — id, name, edition, source_url, website, start/end_date, timezone, address, lat/lng
venues            — id, name, about, address, lat/lng
stages            — id, name, about, venue_id (FK → venues)
event_stages      — event_id + stage_id (PK), color (RGB), position
stage_notes       — stage_id, date, note, position (daily annotations: curators, hosts)
stage_details     — stage_id, label, value, position (static key-value facts for popup)
artists           — id, name, photo_url, photo_file, bio (markdown)
artist_links      — artist_id + platform (PK), url, follower_count, position
artist_sets       — id, artist_id, platform, url, title, view_count, duration_min, upload_date, position
schedule          — artist_id + event_id + start_time (PK), stage_id, end_time, date, period, set_type
```

Key design decisions:
- **Artists, artist_links, and artist_sets are global** — shared across events
- **Stages are global, reusable across events** — the same physical stage can appear at multiple events. Event-specific config (color, display order) lives in `event_stages` junction
- **Venues** hold physical addresses/coordinates — stages reference their venue via `venue_id`. Single-venue events: one venue, all stages point to it (or NULL, address on events table). Multi-venue events: multiple venues, each stage references its venue
- **artist_links** normalizes all social platforms — adding a new platform is just an INSERT, no schema change
- **artist_sets** normalizes all media sources — `platform` column distinguishes YouTube, SoundCloud, etc.
- **`period`** is a free-text tag (day, night, afterhours, etc.), nullable for events without period splits
- **`set_type`** supports dj, live, hybrid, b2b, talk, or NULL
- **`edition`** on events separates the event name ("Stone Techno") from the instance ("2026", "XV"). Page title derived as `"{name} {edition} Companion"`
- **Stage colors** stored as RGB channels in `event_stages.color` (e.g. `"198, 249, 197"`), CSS generated dynamically at build time. Per-event — same stage can be green at one festival, blue at another
- **Stage notes** hold per-day annotations (curators, hosts) shown below floor pills
- **SQLite pragmas**: `journal_mode=WAL` (concurrent reads), `foreign_keys=ON` (referential integrity)
- **All queries use `sqlite3.Row`** — dict-like access by column name, no positional indexing
- **Schedule PK** is `(artist_id, event_id, start_time)` — safe for multi-event

### Key files

| File | Role |
|---|---|
| `scraper/scrape.py` | Lineup parser + SoundCloud/Instagram/Spotify/Resident Advisor scrapers. Each event needs its own scraper module. |
| `scraper/db.py` | SQLite schema, upserts, overrides, queries — all event-scoped |
| `scraper/images.py` | Photo resize (pyvips lanczos3) + AVIF encode (ssimulacra2 target 78) |
| `scraper/render.py` | HTML generation — line-up list + timetable grid, CSS, JS, modals, hearts, schedule, push notifications. Markdown bio rendering. Dynamic floor color CSS. SVG icons via `<symbol>`/`<use>` sprite |
| `scraper/timetable_json.py` | Generates `timetable.json` — slot UUID → set time mapping for push scheduler and ICS endpoint. Reads timezone from events table. |
| `fetch_videos.py` | YouTube set discovery via yt-dlp. Writes to `artist_sets` table with `platform='youtube'`. |
| `seed_timetable.py` | Seeds fake timetable data (floors + time slots) for development |
| `migrate_db.py` | One-time migration from any old schema version to current. Creates backup, migrates artists + links + sets + locations + notes. |
| `server/api.py` | FastAPI app — favorites + schedule API + WebSocket sync + push scheduler + ICS export + static file routes. Mounts chat module at startup. |
| `server/chat_db.py` | Chat SQLite schema (chat.db) — users, sessions, bans, rooms, messages, meetups, reactions, blocks, reports, strikes |
| `server/chat_moderation.py` | Three-layer moderation: word filter + OpenAI omni-moderation + GPT-5.4-nano drug detection. All via raw httpx. |
| `server/chat_ws.py` | Chat WebSocket server — rooms, optimistic messaging, presence, typing, reactions, replies, meetups, DMs, purge loop |
| `server/chat_api.py` | Chat REST API — auth (Google/Apple/Email), rooms, meetups, DMs, media upload, admin page. Mounts routes + WS into FastAPI. |
| `server/chat/chat.html` | Chat frontend — single HTML file with inline CSS/JS. WhatsApp-style bubbles, reactions, replies, action menus. |
| `server/chat/blocklist.txt` | Word filter blocklist (drug terms, slurs). Editable without deploy. |
| `server/static/sw.js` | Service worker — handles push events and notification click navigation |
| `server/static/manifest.json` | PWA manifest — enables Add to Home Screen and push on iOS |
| `tests/test_chat_db.py` | 45 tests — users, sessions, bans, rooms, messages, meetups, DMs, blocks, reports, strikes |
| `tests/test_chat_moderation.py` | 33 tests — word filter, strike system, AI moderation pipeline |
| `tests/test_chat_ws.py` | 17 tests — WebSocket rooms, messaging, presence, moderation flow |
| `tests/test_chat_api.py` | 31 tests — REST endpoints, auth, rooms, meetups, DMs, admin |

### Two deploy paths

- **Content** (HTML + photos + thumbs + timetable.json + bios.json + sw.js + manifest.json): `--deploy` flag rsyncs to VPS static dir. No container restart — files are volume-mounted.
- **Server code**: push to `main` with changes in `server/` triggers GitHub Actions → SSH → `git pull` + `docker compose up -d --build --force-recreate`.

## Generated Artifacts (gitignored)

- `lineup.db` — SQLite database (all tables)
- `lineup.db.bak` — backup created by migrate_db.py
- `output/lineup.html` — generated page (~650 KB)
- `output/bios.json` — artist bios + sets, lazy-loaded on first artist tap (~200 KB)
- `output/photos/*.avif` — processed artist photos
- `output/timetable.json` — slot UUID → set time mapping for push notifications
- `output/thumbs/*.avif` — YouTube video thumbnails (240px max, AVIF)

These are regenerable. Source of truth is the live website + `overrides.toml` + DB enrichment data.

## Overrides

`scraper/overrides.toml` provides manual corrections. Applied after scraping, before follower fetching.

```toml
# Artist link overrides — field names match platform names in artist_links
[Amoral]
ra = "https://ra.co/dj/amoral"

[ROD]
soundcloud = "https://soundcloud.com/bennyrodrigues"
photo = "https://cdn.example.com/photo.webp"  # "photo" is aliased to photo_url

# YouTube search name aliases
[youtube_names]
"Serge" = "Serge Clone"

# Force specific video IDs (skips search)
[youtube_videos]
"Function" = ["abc123", "def456"]

# Append extra videos after search
[youtube_videos_add]
"Rødhåd" = ["ghi789"]

# Per-day per-floor annotations (shown below floor pill)
[floor_curators]
"2026-07-11.koksofenbatterie" = "curated by Freddy K"
"2026-07-12.werksschwimmbad" = "hosted by Clone Records"
```

Supported link fields: `instagram`, `soundcloud`, `spotify`, `linktree`, `youtube`, `ra`. Setting a field to `false` clears the URL and marks the count as fetched (0).

## Timetable View

Toggled via the command bar. Appears automatically when artists have `start_time`/`end_time` in `schedule`.

- **Desktop**: CSS grid with sticky floor headers and time labels
- **Mobile**: HTML `<table>` with native scroll, sticky `<thead>`, `table-layout: fixed`, dynamic `--row-h` (10px or 14px based on artist density)
- **Scroll position**: saved per view — switching between lineup and timetable restores where you were
- **Popup → Bio**: clicking artist name/photo in the timetable popup closes it and opens the bio modal
- **B2B sets**: multiple artists in same time slot render as one card with per-artist hearts
- **Schedule**: calendar icon on each card, server-synced via API
- **ICS export**: button on each card → server endpoint serves `.ics` file
- **Floor annotations**: "curated by" / "hosted by" from `stage_notes` table, shown below floor pills per day
- **Artist schedule notes**: floor + time on every card, "Also" cross-references for multi-slot artists
- **Hamburger menu**: mobile-only, preserves view in localStorage across reloads

### Design system

- **Colors**: CSS variables in `:root` — `--color-text`, `--color-bg`, `--color-surface`, `--color-surface-hover`, `--color-muted`, `--color-muted-icon`, `--color-accent`, `--color-schedule`, `--color-border`
- **Floor colors**: from `locations.color` in DB (RGB channels). CSS generated at build time — cards `rgba(R,G,B, 0.88)`, pills `rgb(R,G,B)`. Unknown floors fall back to gray.
- **Font scale**: `--font-2xl` (2em) → `--font-xs` (0.75em/12px min). No text below 12px.
- **Shared tokens**: `--shadow-modal`, `--radius-card`, `--radius-modal`, `--transition-fast`, `--fade-gradient`
- **Hover**: all guarded with `@media (hover: hover)` — no sticky hover on touch
- **Contrast**: all text/icon colors pass WCAG 2.1 AA

## Artist Bio Overlay

Clicking artist name/photo opens modal with photo, name, biography (markdown → HTML at build time, booking info stripped), and sets with thumbnails. Bios lazy-loaded from `bios.json` on first tap — fetched once, cached in memory. Falls back to name-only overlay if fetch fails. Body scroll locked via `position: fixed` (iOS Safari compatible).

## HTML Standards

- `<nav>` wraps command bar, `<main>` wraps content
- All buttons have `type="button"`
- Interactive elements have `tabindex="0" role="button"` + keyboard handlers
- Modals: `role="dialog"`, `aria-modal`, `aria-labelledby`; focus returns to trigger on close; tab trapping; Escape closes
- SVG sprite: `aria-hidden="true"`; images have meaningful `alt` text
- PWA meta tags: `apple-mobile-web-app-capable`, `theme-color`, `apple-mobile-web-app-title`
- Social links rendered as a loop from `artist_links` — adding a platform requires only a new SVG icon + a mapping entry in `PLATFORM_ICONS`

## Working on the HTML/CSS/JS

All frontend code lives in `scraper/render.py` as Python string concatenation. No separate HTML/CSS/JS files.

```bash
python stone_techno_companion.py --render-only --no-photos
cd output && python3 -m http.server 8321
# Open http://localhost:8321/lineup.html
```

## Server

FastAPI (`server/api.py`). Sessions via 128-bit URL-safe tokens. Cross-device sync via ephemeral 6-digit PINs (5-min TTL). Real-time sync via WebSocket. Atomic pick/schedule operations via `json_group_array`/`json_each`.

Static file routes (`/bios.json`, `/timetable.json`, `/manifest.json`, `/sw.js`, `/favicon.*`) are explicit endpoints before the catch-all `/{path:path}` (which serves `index.html`). New static files need an explicit route in `api.py`.

Production: Docker on DigitalOcean VPS behind Caddy (auto-TLS). DB at `server/data/hearts.db` volume-mounted.

### Environment Variables (`server/.env`)

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | Chat moderation (omni-moderation + GPT drug detection) |
| `MAILEROO_API_KEY` | Yes | Magic link email delivery (was Resend, switched July 2026) |
| `CHAT_EMAIL_FROM` | No | From address for magic links (default: `no-reply@deftlab.dev`) |
| `CHAT_BASE_URL` | Dev only | Set to `http://localhost:<port>` for local dev. Omit in production. |
| `VAPID_PRIVATE_KEY` | Yes | Push notification signing |
| `VAPID_PUBLIC_KEY` | Yes | Push notification subscription |
| `VAPID_CLAIMS_EMAIL` | Yes | VAPID contact email |
| `GOOGLE_CLIENT_ID` | No | Google OAuth (not wired in frontend yet) |

### DNS for Email (deftlab.dev)

- **SPF**: `v=spf1 include:_spf.mx.cloudflare.net include:_spf.maileroo.com ~all`
- **DKIM**: TXT record at `mta._domainkey.deftlab.dev` (from Maileroo dashboard)
- **DMARC**: existing `_dmarc.deftlab.dev` record works as-is

### Deploy Checklist (when merging chat-prototype)

1. Add `MAILEROO_API_KEY` to production `.env` (replace `RESEND_API_KEY`)
2. Remove `RESEND_API_KEY` from production `.env`
3. Remove `CHAT_BASE_URL` from production `.env` (or don't set it)
4. Update DNS: replace `include:amazonses.com` with `include:_spf.maileroo.com` in SPF
5. Add DKIM record `mta._domainkey.deftlab.dev` if not already done
6. Ensure `ffmpeg` + `ffprobe` are in the Docker image (video upload)
7. Install new Python deps: `pip install email-validator maileroo`
8. `chat/disposable_domains.txt` is committed — ships with the code
9. `chat/uploads/` directory is auto-created — no manual setup needed

## Push Notifications

- **Scheduler**: background task runs every 60s, matches `timetable.json` slots against sessions' schedule, sends via `pywebpush`
- **Dedup**: `sent_notifications` table, pruned after 7 days. Dead subscriptions auto-removed.
- **Re-sync on load**: client re-sends push subscription to recover from DB purges
- **iOS**: Cache Storage flag for notification click navigation (service worker can't access localStorage)

## Multi-Event Support

The DB supports multiple events via the `events` table. Artists, artist_links, artist_sets, stages, and venues are global (shared). Schedule and event_stages are scoped per event. CLI flags: `--event-id`, `--event-name`, `--event-edition`. Each event needs its own scraper module — the scraper output format (`parsed` dict with `artists`, `sections`, `locations`, `assignments`) is the interface.

## Chat System

Privacy-first ephemeral chat integrated into the companion app. Accessible via "Chat" button in the command bar / hamburger menu, or directly at `/chat`.

### Architecture

Extends the existing FastAPI server — no separate service. Two SQLite databases: `hearts.db` (favorites, unchanged) and `chat.db` (ephemeral chat data). Chat module mounted at startup via `chat_api.mount_chat(app)`, registered before the catch-all `/{path:path}` route.

### Chat Database (chat.db)

```
users              — id, provider, provider_id, display_name, country, avatar_url, device_fingerprint, muted_until
sessions           — id, user_id, token, expires_at
email_tokens       — token, email, provider_id, fingerprint, expires_at (DB-backed, survives restart)
avatars            — user_id (PK), data (BLOB, AVIF 128x128)
bans               — id, provider, provider_id, device_fingerprint, reason (survives user deletion)
rooms              — id, event_id, type (general/meetup/dm), name — single room per event
messages           — id, room_id, user_id, type, content, reply_to_id, expires_at (60 min default)
message_reactions  — message_id + user_id + emoji (PK), CASCADE on message delete
meetups            — id, creator_id, stage_id, title, location, meetup_time, expires_at (meetup_time + 30 min)
meetup_attendees   — meetup_id + user_id (PK)
dm_participants    — room_id + user_id (PK)
blocks             — blocker_id + blocked_id (PK)
reports            — id, reporter_id, reported_user_id, message_snapshot, reason, status
strikes            — id, user_id, reason, detail
```

### Auth

Three passwordless providers: Google OAuth, Apple Sign-In, Email magic link (via Maileroo, 3,000/mo free). Disposable domains blocked via 7,860-domain blocklist (`chat/disposable_domains.txt`). Email validation via `email-validator` library (RFC 5322 + DNS MX check). Device fingerprinting for ban enforcement. Session cookies (non-httpOnly for WS access, Secure in production, SameSite=Strict in production / Lax in dev, path=/). Email tokens stored in DB (not memory) — survive server restarts.

### Profile Setup

Mandatory before entering chat: display name, country, avatar photo. Profile prompt shown on first login.

- **Avatar**: circular 128px pan+zoom editor. Click to select image (min 128x128), drag to pan, custom friction slider to zoom. Client crops to 128x128 via `createImageBitmap` with `resizeQuality: 'high'`. Server converts to AVIF and stores as blob in `avatars` table. Served via `/chat/api/avatar/{user_id}` with 24h cache.
- **Country**: searchable dropdown with 195 countries + local name aliases (Deutschland, Italia, Espana, etc.). Search matches from start of word only. Arrow key navigation, Enter to select, first result highlighted.
- **Name**: 3-30 characters.

### Moderation Pipeline

Every message passes through three layers before broadcast:

1. **Word filter** (instant) — in-memory set from `chat/blocklist.txt`. Drug terms, slurs, spam. Character substitution normalization (@→a, 0→o, etc.).
2. **OpenAI omni-moderation-latest** (free) — harassment, hate, violence, sexual content. Supports images (WebP data URI) and video (3 frames at 25/50/75% extracted by ffmpeg). Via raw httpx.
3. **GPT-5.4-nano drug detection** (Responses API, reasoning=none, ~$4.65/festival) — custom prompt catches subtle drug slang (party favors, rolling, just dropped, etc.).

Layers 2 and 3 run in parallel via `asyncio.gather`. Word filter blocks before AI calls (saves API round-trips).

**Optimistic delivery**: message saved to DB immediately, `message_acked` sent to sender, moderation runs in `asyncio.create_task`. If passes: broadcast to others. If fails: delete from DB, send `message_removed` + strike to sender.

**Strike system**: 1st = warning, 2nd = 30-min mute, 3rd = permanent ban. Drug terms escalate: 2nd drug offense = immediate ban. Bans stored by provider_id + device fingerprint.

### Chat UI

Single room per event — auto-opens on login (no room list). Single HTML file (`server/chat/chat.html`).

- **Bubble style**: pastel blue (own) / pastel purple (others), dark text, time bottom-right
- **Replies**: double-click on desktop, swipe toward center on mobile. Quote shown inside bubble.
- **Reactions**: hover-based on desktop (200ms dismiss), long-press on mobile. 6-emoji picker. Button outside bubble with 88px hover zone.
- **Input bar**: + button (meetup, location, photo, video) on left, emoji picker icon inside input, send button on right. All SVG icons.
- **Images**: client-side resize via createImageBitmap (high quality), WebP storage, image viewer overlay on click
- **Videos**: client-side processing via Mediabunny + WebCodecs. HEVC with H.264 fallback, hardware-accelerated. Auto re-encodes if >1080p, >10Mbps, >30fps, or non-AAC audio. Trim editor for >60s. Inline playback (click play/pause, fullscreen icon), expanded viewer with frame sync.
- **Location sharing**: GPS with confirmation dialog, card with map pin icon
- **Meetup creation**: modal with title, date + hour/minute selects (15-min intervals), GPS location, note. Card with calendar icon.
- **Message delete**: right-click bubble (desktop) or long-press (mobile), confirmation in same action sheet, 120s window, server enforced
- **User menu**: action sheet (Send Message, Block User with inline confirmation, Cancel). Centered modal on desktop.
- **Optimistic messaging**: messages appear instantly with pending state, confirmed on ack, removed if moderation rejects
- **Scroll**: messages pushed to bottom via flex justify-content, app hidden until routing completes, ResizeObserver locks scroll for 1.5s after render
- **Desktop**: sidebar + chat panel side-by-side (768px breakpoint)
- **Font scale**: 15px (base) → 13px (secondary) → 11px (tertiary) → 10px (timestamps)
- **Debug**: `dbg()` with timecodes, `verify()` checks DOM state after every action

### Chat Tests

126 tests total: `python -m pytest tests/ -v`
- `test_chat_db.py` (45) — all CRUD, cascade deletes, purge, wipe
- `test_chat_moderation.py` (33) — word filter, AI mocks, strike escalation, drug detection
- `test_chat_ws.py` (17) — WebSocket rooms, messaging, presence, moderation flow
- `test_chat_api.py` (31) — REST endpoints, auth, rooms, meetups, DMs, admin
