# Repository Guidelines

## Project Structure & Module Organization

Leaf by Leaf is a Python 3 Flask journaling app. Core code lives at the repository root: `app.py` defines routes and startup, while `journal.py`, `tasks.py`, `topics.py`, `trackers.py`, `llm_service.py`, and `ai_models.py` hold domain logic. HTML views are in `templates/`; browser JavaScript, CSS, icons, the PWA manifest, and service worker are in `static/`. Tests live in `tests/`, currently centered on `tests/test_improvements.py`. Deployment files include `leaf-by-leaf.service`, `leaf-by-leaf-deployer.service`, and `deploy_webhook.py`. Treat `app.db`, `logs.db`, and `__pycache__/` as development artifacts.

## Build, Test, and Development Commands

Install dependencies from the repo root:

```powershell
py -m pip install -r requirements.txt
```

Run the local Flask app on port `8002`:

```powershell
py app.py
```

Run unit tests:

```powershell
py -m unittest discover -s tests -v
```

Check Python syntax for changed backend files:

```powershell
py -m py_compile app.py journal.py llm_service.py topics.py tasks.py trackers.py
```

Check edited frontend scripts when Node is available:

```powershell
node --check static/chat.js
```

## Coding Style & Naming Conventions

Use 4-space indentation for Python and keep functions focused. Follow existing `snake_case` naming for functions, variables, database helpers, and test methods. Keep route handlers thin where practical; put reusable data logic in domain modules. Frontend files use plain JavaScript and CSS, with feature-specific filenames such as `tasks.js`, `topics.js`, and `chat.js`. When changing `static/style.css` or static JS, bump the cache name in `static/sw.js`.

## Testing Guidelines

Tests use Python `unittest`. Add regression tests for bug fixes or behavior changes involving journal entries, tasks, topics, trackers, decisions, migrations, chat display, or AI extraction boundaries. Use `TempVoiceJournalDB` from `tests/test_improvements.py` for database-backed tests so tests run against temporary SQLite files, not `app.db` or `logs.db`. Mock AI and network boundaries in unit tests.

## Commit & Pull Request Guidelines

Recent commits use concise Conventional Commit-style subjects, for example `feat: replace pending snapshots with upcoming snapshots screen` and `fix(transcribe): log Whisper error body and reject tiny audio files`. Prefer `feat:`, `fix:`, or scoped forms like `fix(ai_models): ...`. Pull requests should describe the user-facing change, list test commands run, mention schema or deployment impacts, and include screenshots for UI changes.

## Security & Configuration Tips

Do not commit secrets, API keys, production databases, or journal data. Keep auth, OAuth, CSRF, and API-key changes covered by tests. Production configuration is environment-driven; see `CLAUDE.md` for deployment paths and service notes.
