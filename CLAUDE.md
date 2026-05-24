# Leaf by Leaf

Personal voice memo journaling app. iPhone → iOS Shortcut → upload `.m4a` → Whisper transcription → Claude formatting → markdown journal. Web UI for browsing entries, AI chat, task management, trackers, and decision log.

## Data model
- **Entries** are the primary unit. Each entry has a **persistent random ID** (8-char hex), a datetime stamp, and content.
- Entry IDs are stored in `app.db` (`entry_index` table) and never change, even if date/time is edited.
- Entries are sequenced by datetime — no grouping by day in the UI or API.
- Storage is plain markdown files organized by date (`YYYY-MM-DD.md`) — implementation detail only.
- **Topic tags are per-entry**, not per-date. `entry_topic_tags` links `entry_id → topic_id`.

## Stack
- Python 3 + Flask
- OpenAI Whisper API (`whisper-1`) for transcription, routed through the AI gateway
- Claude Haiku/Sonnet via AI Gateway for formatting, extraction, and chat
- Plain markdown files as entry storage
- Async upload: 202 + job_id, poll `/status/<job_id>`

## Endpoints
- `POST /upload` — API-key-authenticated, accepts multipart `audio` field
- `GET /status/<job_id>` — poll transcription job status
- `GET /` — journal index: all entries in reverse-datetime order (login required)
- `GET /entry/<entry_id>` — individual entry view (8-char hex ID, permanent)
- `POST /entry/<entry_id>/edit` — update entry content, date, or time
- `GET /chat` — AI chat page
- `POST /api/chat` — SSE streaming chat
- `GET /tasks`, `/topics`, `/trackers`, `/decisions` — feature pages
- `GET /logs` — upload log history

## Auth
- Multi-user: register via `/register`, session-based auth
- Per-user API keys on `/settings` page (used by iOS Shortcut)
- `UPLOAD_API_KEY` env var accepted as legacy fallback

## Deployment
- **Service:** `leaf-by-leaf.service` → `/etc/systemd/system/`
- **Deploy webhook:** `leaf-by-leaf-deployer.service` → gunicorn on `127.0.0.1:9001`
- **App dir:** `/home/will/apps/leaf-by-leaf/`
- **Journal data:** `/home/will/data/leaf-by-leaf/`
- **Port:** `8005`
- **Caddy:** `app.leafbyleaf.net` → `localhost:8005`
- **Webhook URL:** `https://deploy.leafbyleaf.net/webhook` (GitHub push → auto-deploy)
- **Env file:** `/home/will/.env/leaf-by-leaf`
- Requires `ffmpeg` on the server for Whisper audio decoding

```bash
# Restart service
ssh wilbee "sudo systemctl restart leaf-by-leaf"

# Tail logs
ssh wilbee "journalctl -u leaf-by-leaf -f"

# Pull latest manually (webhook handles this automatically on push)
ssh wilbee "cd /home/will/apps/leaf-by-leaf && git pull && sudo systemctl restart leaf-by-leaf"
```

## Deploy checklist
- **Any change to `style.css` or static JS**: bump the cache version in `static/sw.js` (`const CACHE = 'lbl-vN'`) before committing.

## Notes
- Single gunicorn worker — background threads are safe
- Jobs are in-memory; a server restart clears pending jobs
- OpenAI key must be configured in the AI gateway for Whisper; Anthropic key for Claude

---

## Design system

The full Leaf by Leaf Design System lives at `visual assets/`. The zip is gitignored but the extracted contents are not tracked — treat this directory as local reference only.

```
visual assets/
├── README.md               ← Full brand guide: voice, palette, typography, layout rules
├── SKILL.md                ← Agent skill manifest — load this for Claude to become
│                              a Leaf by Leaf design expert
├── colors_and_type.css     ← Every token: colors, type scale, spacing, shadows, motion
├── assets/                 ← Brand marks and logos (PNG)
│   ├── logo-app-icon.png           PWA/app icon source (1254×1254)
│   ├── logo-primary-lockup.png     Full logo with wordmark (horizontal)
│   ├── logo-primary-lockup-stacked.png
│   ├── logo-wordmark-horizontal.png
│   ├── logo-mark.png               Standalone leaf+page mark (color)
│   ├── logo-mark-mono.png          Same mark, single-color forest
│   ├── logo-circle-badge.png       Circular badge lockup
│   ├── logo-monogram.png           LxL monogram
│   ├── icon-leaf-circle.png        Leaf in circle — used as favicon source
│   ├── icon-leaf-in-book.png       Leaf+page mark (alias of logo-mark-mono)
│   ├── icon-sprout.png             For new entries, growth states
│   ├── icon-sunrise.png            For morning prompts, today view
│   └── brand-board.png             Full brand overview (palette, type, mockups)
├── preview/                ← HTML reference cards for every token group and component
├── ui_kits/
│   ├── mobile_app/         ← Interactive iOS prototype (Today, Journal, Garden, Compose)
│   └── marketing_site/     ← Public-facing site mock
└── uploads/                ← Raw PNG exports from the original brand assets
```

### Key design tokens (from `colors_and_type.css`)

| Token | Value | Role |
|---|---|---|
| `--color-forest` | `#3F5B46` | Primary brand — headings, buttons, logo |
| `--color-burnt-orange` | `#C25D2A` | Accent — one touch per screen max |
| `--color-paper` | `#F2E9DD` | App background (never white) |
| `--color-sage` | `#A7B494` | Tags, secondary fills |
| `--color-ink` | `#2B2B2B` | Body text (not pure black) |
| `--font-serif` | Playfair Display | Headings, display |
| `--font-body` | Lora | Body text |
| `--font-script` | Sacramento | Taglines, quoted prompts only — never UI labels |
| `--font-mono` | JetBrains Mono | Dates, metadata, code |

### Hard rules
- Background is always Paper (`#F2E9DD`), never white.
- No gradients. No textures in screen UI.
- One burnt-orange touch per screen, max.
- No emoji. No unicode glyphs as icons. Use Phosphor Duotone for UI icons.
- No sans-serif fonts — the system stays entirely in serif/script.
- Sentence case everywhere, except the wordmark *LEAF BY LEAF*.
- Script font (Sacramento) only for feeling, never for UI controls, never below 18px.
- No bouncy motion — `cubic-bezier(0.22, 1, 0.36, 1)` out, 140/220/360ms durations.

### Working with the design system in Claude Code
To work on UI as a design-aware agent, read `visual assets/SKILL.md` first — it provides full brand context, component patterns, and hard rules. For production CSS token usage, reference `visual assets/colors_and_type.css` directly.
