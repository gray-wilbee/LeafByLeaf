# VoiceJournal Testing Protocol

## Run The Suite

From the repo root:

```powershell
py -m unittest discover -s VoiceJournal\tests -v
py -m py_compile VoiceJournal\app.py VoiceJournal\journal.py VoiceJournal\llm_service.py VoiceJournal\topics.py VoiceJournal\tasks.py VoiceJournal\tests\test_improvements.py
node --check VoiceJournal\static\chat.js
node --check VoiceJournal\static\topics.js
node --check VoiceJournal\static\entities.js
node --check VoiceJournal\static\tasks.js
```

## Test Isolation Pattern

Use `TempVoiceJournalDB` from `tests/test_improvements.py` for database-backed unit tests.

It provides:

- A temporary SQLite app database.
- A temporary journal directory.
- Real schema initialization through `topics.init_db()`, `tasks.init_db()`, and `journal.init_entries_db()`.
- Automatic restoration of `db.get_db` and `journal.JOURNAL_DIR`.

Tests must not use `VoiceJournal/app.db`, `VoiceJournal/logs.db`, or the real journal data directory.

## Mock External AI And Network Calls

Unit tests should patch AI/network boundaries, including:

- `llm_service.extract_candidates_resilient`
- `llm_service.extract_task_candidates`
- `llm_service.get_embedding`
- `llm_service.ai_call`
- `llm_service.update_tag_metadata`

The unit suite should be deterministic and should pass without the AI gateway running.

## What Belongs In Regression Tests

Add a regression test whenever fixing a bug or changing behavior in:

- Intake extraction rules, tag assignment, backlinks, or task creation.
- Chat message display shaping, especially tool-turn rendering.
- SQLite schema migrations or idempotent column additions.
- Journal entry timestamp/title formatting.
- Task filtering and descendant-topic expansion.
- Routes that mutate tags, tasks, chats, entries, or extraction items.

Keep regression tests narrow: arrange a minimal temp database state, run the exact function or Flask route, and assert the behavior that previously failed.

## When To Add Browser/E2E Coverage

Use browser-level checks for workflows that depend on DOM behavior or responsive layout:

- Mobile chat list versus active-chat view.
- Collapsible extraction report.
- Topic/entity Connections add/remove controls.
- Task detail modal deep links from entry extraction.

These should complement unit regressions, not replace them.
