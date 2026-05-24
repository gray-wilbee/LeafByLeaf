# VoiceJournal

Personal voice memo journaling system. iPhone → iOS Shortcut → upload `.m4a` → Whisper transcription → Claude formatting → markdown journal. Web UI for browsing and AI chat.

## Data model
- **Entries** are the primary unit. Each entry has a **persistent random ID** (8-char hex), a datetime stamp (date + time), and content.
- Entry IDs are stored in `entries.db` (`entry_index` table) and never change, even if date/time is edited.
- Entries are sequenced by datetime and completely egalitarian — no grouping by day in the UI or API.
- Storage is plain markdown files organized by date for convenience (`YYYY-MM-DD.md`), but this is an implementation detail. No "day" object is exposed to users.
- **Topic tags are per-entry**, not per-date. `entry_topic_tags` in `topics.db` links `entry_id → topic_id`.

## Stack
- Python 3 + Flask
- OpenAI Whisper API (`whisper-1`) for transcription, routed through the AI gateway
- Claude Haiku via AI Gateway for transcript formatting and chat
- Plain markdown files as entry storage (one file per calendar date, internal only)
- Async upload: 202 + job_id, poll `/status/<job_id>`

## Endpoints
- `POST /upload` — API-key-authenticated, accepts multipart `audio` field
- `GET /status/<job_id>` — poll transcription job status
- `GET /` — journal index: all entries in reverse-datetime order (login required)
- `GET /entry/<entry_id>` — individual entry view (8-char hex ID, permanent)
- `GET /entry/<date>/<time_slug>` — legacy redirect (301) to new entry URL
- `POST /entry/<entry_id>/edit` — update entry content, date, or time (ID is preserved)
- `GET /chat` — AI chat page (recent entry context)
- `POST /api/chat` — SSE streaming chat
- `GET /logs` — upload log history; opening this page clears the error badge

## Auth
- Multi-user: users register via `/register`, stored in `users` table (app.db)
- Session-based auth with `session["user_id"]`
- Per-user API keys stored in DB (shown on `/settings` page, used by iOS Shortcut)
- Legacy fallback: `UPLOAD_API_KEY` env var still accepted for migration period
- Migration script: `migrate_multiuser.py` seeds existing data under user_id=1

## Deployment
- Service: `voice-journal.service` → `/etc/systemd/system/`
- App dir: `/home/will/apps/voice-journal/`
- Journal data: `/home/will/data/journal/`
- Caddy: `journal.wilbeevibes.com` → `localhost:8002`
- Requires `ffmpeg` on the server for Whisper audio decoding

```bash
# First deploy
scp -r . wilbee:/home/will/apps/voice-journal/
ssh wilbee "mkdir -p /home/will/data/journal"
ssh wilbee "cd /home/will/apps/voice-journal && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"

# Generate SECRET_KEY (run on server)
python3 -c "import secrets; print(secrets.token_hex(32))"

# Run migration (first deploy with multi-user support)
# Set PASSWORD_HASH and UPLOAD_API_KEY env vars, then:
python3 migrate_multiuser.py

# Install & start service
ssh wilbee "sudo cp /home/will/apps/voice-journal/voice-journal.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now voice-journal"

# Add to Caddy
# journal.wilbeevibes.com { reverse_proxy localhost:8002 }
ssh wilbee "sudo nano /etc/caddy/Caddyfile && sudo systemctl reload caddy"

# Restart after changes
ssh wilbee "sudo systemctl restart voice-journal"

# Logs
ssh wilbee "journalctl -u voice-journal -f"
```

## Deploy checklist
- **Any change to `style.css` or static JS files**: bump the version in `static/sw.js` (`const CACHE = 'journal-vN'`) before committing, then copy it to the server with the other files.

## Notes
- Transcription uses OpenAI Whisper API via the AI gateway — no local model, no disk/RAM overhead
- No API keys in this app — all keys live in the gateway (`/keys/set` via DropletDash)
- OpenAI key must be configured in the gateway for Whisper; Anthropic key for Claude formatting/chat
- Single gunicorn worker — background threads are safe (no multi-process issues)
- Jobs are in-memory; a server restart clears pending jobs (the audio file is also deleted on restart since it's in /tmp)
