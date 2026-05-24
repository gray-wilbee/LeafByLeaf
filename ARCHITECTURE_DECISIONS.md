# Architecture Decisions — Inputs, Tasks, Entities, Chat

**Status:** working doc, drafted 2026-04-19
**Purpose:** Capture the decisions made for the "journal → personal operating system" evolution, sketch the target schema, and hand off the open work (especially #5, agent/intake deduplication) into Claude Code planning mode.

---

## TL;DR

- The user-facing object is the **input** — a flat, egalitarian capture bucket. No subtyping; a `source` field attributes where it came from.
- **Tasks and appointments are outputs**, not inputs. They're structured work products the app produces from (and can be updated independently of) the inputs that spawned them.
- **Topics and entities are one table** (`tags`) with a `kind` discriminator. Backlinks work for either.
- **Extraction moves to a two-phase pipeline** (candidate emit → disambiguation) so tag-matching scales past the current ~12 active topics into hundreds of entities without blowing up context.
- **Tasks form a graph**, not a tree: flat tasks + a `task_links` join with `part_of`, `blocks`, `related`.
- **Calendar is write-through now, read-mirror later.** Google event IDs are the cross-system keys.
- **One unified chat surface.** Per-topic and per-entity chat are just the unified chat with pre-loaded scope. Saved chats become canonical inputs; no duplicate copies on tagged objects.
- **Every row that can be machine-created carries provenance** (`source`, `source_session_id`) so agent-vs-intake deduplication can be designed on top.

---

## Settled decisions

### 1. Input is a flat supertype; tasks/appointments are outputs

**Decision.** `inputs` is a single flat table. No `kind` discriminator; a `source` column attributes the capture channel (`journal`, `chat_summary`, `chat_transcript`, `quick_thought`, `typed`). Tasks and appointments are separate top-level tables that reference the input(s) they were derived from.

**Why.** The distinction isn't "what type of input is this" — it's "did the user capture this, or did the app produce it." Raw captures are immutable-ish records; work products have lifecycles (status, reschedule, complete). A task can accrue context from multiple inputs over time ("take out trash" → "reminded myself again" → "done"), so the relationship is many-to-many, not 1:1.

**What this replaces.** The current `entries` table becomes `inputs`. The journal/date folder layout stays as an internal storage detail; nothing user-facing keys off calendar date.

### 2. One `tags` table for topics and entities, plus a scalable extraction pipeline

**Decision.** Unify topics and entities into a single `tags` table with `kind ∈ {topic, entity, ...}`. Backlinks between tags (of any kind) live in a `tag_links` table. Extraction moves to two phases.

**Why.** Every feature we'd have to build twice for separate tables (extraction, tagging, highlights, chat scoping, merge, backlinks) ships once. The only real differences between topics and entities are semantic, not structural.

**The extraction pipeline (applies to both kinds and any future kinds):**

1. **Candidate phase.** The LLM reads the input and emits candidate tags — name, kind, and a surrounding snippet — without ever seeing the full tag universe.
2. **Disambiguation phase.** For each candidate, a lightweight step does a vector + lexical lookup against existing tags of that kind and decides match / create / flag-for-user-review.

This keeps the LLM context bounded regardless of how many tags exist. Rolling this out is a prerequisite to entities, since ~300 entities can't fit in context the way 12 topics do. Doing it once now covers custom tag kinds (projects, places, goals) later with no further refactor.

### 3. Tasks are a graph, not a tree

**Decision.** Flat `tasks` table plus a `task_links(from_task_id, to_task_id, kind, sort_order)` join. Kinds:

- `part_of` — soft parent/child; a task is part of a larger task. Acyclic. `sort_order` meaningful here.
- `blocks` — hard dependency; from-task must be done before to-task. Acyclic.
- `related` — loose connection. Cycles allowed.

**Tradeoffs acknowledged.**
- Rollups ("% complete for this big task") require recursive CTEs. Fine at our scale.
- `part_of` and `blocks` need cycle checks at write time.
- UI has to make choices a tree wouldn't — default view groups one level by `part_of`, shows `blocks` as a separate strip. Deeper nesting is accessible but not default.
- Tag inheritance (e.g., tags on a parent flow to children) is opt-in per edge kind, not implicit. If we want it, `part_of` edges propagate; `blocks` and `related` don't.

### 4. Calendar: write-through now, read-mirror later

**Decision.** For v1, the app creates/updates/deletes events via the Google Calendar API and treats Google as source of truth. `appointments` table stores our local mirror keyed by `google_event_id`, with `etag`, `last_synced_at`, `sync_status`.

**The gap we accept for v1.** Events the user creates directly in Google (phone, Siri, invites from others) are invisible until we add a read pipeline (poll or webhook). Schema is designed so that adding reads later is additive, not a migration — `google_event_id` is already the key in both directions.

### 6. Unified chat; tags scope context; one canonical input per saved chat

**Decision.**
- Single chat system. The chat widget on a topic/entity page is the same chat, with that tag pre-loaded into scope.
- Users can tag topics/entities into a chat to add them to context. These are stored per-chat in `chat_scope_tags`.
- Saved chat = one canonical input (full or summary, per the default-summarize-with-options pattern). Intake runs on it like any input.
- No full-copy denormalization onto directly-tagged objects.

**Why not full copies.** The tag filter on a topic/entity page already surfaces the chat-input via its tags. Copying causes multi-tag bloat, edit-sync problems, and weird untagging semantics.

**Preserving the "user pinned this topic" signal.** The `object_tags` join carries a `tag_source` column — `user_context` (user added the tag to scope during the chat), `user_explicit` (user manually tagged after the fact), `intake_inferred` (the scan figured it out), `agent` (the agent did it mid-chat). The topic/entity UI can sort user-asserted chats above inferred ones, or visually mark them.

---

## Schema sketch

This is a sketch to refine during implementation planning, not final DDL. Names are illustrative.

```sql
-- Captures
CREATE TABLE inputs (
    id                TEXT PRIMARY KEY,           -- 8-char hex, persistent
    source            TEXT NOT NULL,              -- journal|chat_summary|chat_transcript|quick_thought|typed
    content           TEXT NOT NULL,
    occurred_at       TEXT NOT NULL,              -- user-facing datetime
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    source_session_id TEXT                        -- upload job id, chat id, etc.
);

-- Tags (topics + entities + future custom kinds)
CREATE TABLE tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,                    -- topic|entity|...
    name        TEXT NOT NULL,
    description TEXT,
    summary     TEXT,
    color       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(kind, name)
);

-- Backlinks between tags (any kind, including cross-kind)
CREATE TABLE tag_links (
    from_tag_id INTEGER NOT NULL REFERENCES tags(id),
    to_tag_id   INTEGER NOT NULL REFERENCES tags(id),
    note        TEXT,
    source      TEXT NOT NULL,                    -- user|agent|scan
    created_at  TEXT NOT NULL,
    PRIMARY KEY (from_tag_id, to_tag_id)
);

-- Polymorphic tagging: inputs, tasks, appointments
CREATE TABLE object_tags (
    object_kind       TEXT NOT NULL,              -- input|task|appointment
    object_id         TEXT NOT NULL,
    tag_id            INTEGER NOT NULL REFERENCES tags(id),
    tag_source        TEXT NOT NULL,              -- user_context|user_explicit|intake_inferred|agent
    source_session_id TEXT,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (object_kind, object_id, tag_id)
);

-- Highlights (key sentences per object/tag)
CREATE TABLE object_highlights (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    object_kind TEXT NOT NULL,
    object_id   TEXT NOT NULL,
    tag_id      INTEGER NOT NULL REFERENCES tags(id),
    sentence    TEXT NOT NULL,
    source      TEXT NOT NULL
);

-- Tasks (outputs)
CREATE TABLE tasks (
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    description       TEXT,
    status            TEXT NOT NULL DEFAULT 'open',  -- open|waiting|done|cancelled
    waiting_reason    TEXT,
    due_at            TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    completed_at      TEXT,
    source            TEXT NOT NULL,              -- scan|agent|user
    source_session_id TEXT
);

CREATE TABLE task_links (
    from_task_id TEXT NOT NULL REFERENCES tasks(id),
    to_task_id   TEXT NOT NULL REFERENCES tasks(id),
    kind         TEXT NOT NULL,                   -- part_of|blocks|related
    sort_order   INTEGER,                         -- only meaningful for part_of
    source       TEXT NOT NULL,
    PRIMARY KEY (from_task_id, to_task_id, kind)
);

CREATE TABLE task_sources (
    task_id  TEXT NOT NULL REFERENCES tasks(id),
    input_id TEXT NOT NULL REFERENCES inputs(id),
    PRIMARY KEY (task_id, input_id)
);

-- Appointments (outputs, mirrored from Google)
CREATE TABLE appointments (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    description         TEXT,
    starts_at           TEXT NOT NULL,
    ends_at             TEXT NOT NULL,
    location            TEXT,
    google_calendar_id  TEXT,
    google_event_id     TEXT UNIQUE,
    etag                TEXT,
    last_synced_at      TEXT,
    sync_status         TEXT,                     -- synced|pending_write|conflict|error
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    source              TEXT NOT NULL,            -- scan|agent|user|google
    source_session_id   TEXT
);

CREATE TABLE appointment_sources (
    appointment_id TEXT NOT NULL REFERENCES appointments(id),
    input_id       TEXT NOT NULL REFERENCES inputs(id),
    PRIMARY KEY (appointment_id, input_id)
);

-- Chats (unified surface)
CREATE TABLE chats (
    id         TEXT PRIMARY KEY,
    title      TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    TEXT NOT NULL REFERENCES chats(id),
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE chat_scope_tags (
    chat_id  TEXT NOT NULL REFERENCES chats(id),
    tag_id   INTEGER NOT NULL REFERENCES tags(id),
    added_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, tag_id)
);

-- Chat actions log — the ledger the intake layer reads to avoid redoing work
CREATE TABLE chat_actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      TEXT NOT NULL REFERENCES chats(id),
    action       TEXT NOT NULL,                   -- created_task|updated_task|created_tag|tagged_object|created_appointment|...
    object_kind  TEXT,
    object_id    TEXT,
    payload_json TEXT,
    created_at   TEXT NOT NULL
);
```

### Migration notes (from current schema)

- `entries` → `inputs`. Add `source = 'journal'` by default. Existing IDs carry over.
- `topics` → `tags` with `kind = 'topic'`. Keep topic IDs stable by migrating with same PK.
- `entry_topic_tags` → `object_tags` with `object_kind = 'input'`, `tag_source = 'intake_inferred'` (we can't reconstruct the original source; inferred is the safe default).
- `entry_highlights` → `object_highlights` with `object_kind = 'input'`.
- `topic_chats` + `topic_chat_messages` → `chats` + `chat_messages`. Each old chat's `topic_id` becomes a `chat_scope_tags` row.
- `journal_topic_tags` is already dead code post-entry-ID migration; drop.
- `topic_entries` (the rolled-up per-topic digest) is a derived artifact — rebuild from `object_highlights` after migration rather than porting.

---

## Decision #5 (handoff to Claude Code planning mode): agent vs. intake deduplication

### The problem in one paragraph

The intake layer scans every new input and extracts structured work — tasks, tags, highlights. The in-chat agent can also create this same structured work (tasks, tag links, appointments, etc.) mid-conversation. When a chat is saved as an input (per decision #6), the intake layer then runs on that chat content. Without coordination, intake will happily re-create tasks the agent already created, re-tag objects the agent already tagged, and generally do redundant or contradictory work. The user-visible symptom is duplicate tasks, duplicate tag extractions, and lost trust.

### What the data model already provides

The schema commits to provenance on every machine-created row:

- `source` enum on `tasks`, `appointments`, `tag_links`, `object_tags`, `object_highlights`, `tags` (where `tags.source` is implicit in first-write attribution; may need to be added as a column during implementation).
- `source_session_id` pointing to the chat or intake job that created the row.
- `chat_actions` is an explicit ledger of everything the agent did during a chat — the primary feed for intake's "don't redo this" awareness.

### Open design questions for CC to work through

1. **Does intake run at all on chat-originated inputs, or does it run with a suppression list?** Three options:
   - (a) Skip intake entirely for chat-originated inputs. Simplest; trusts the agent to have done the extraction work. Risk: anything the agent missed stays missed.
   - (b) Run intake with the full `chat_actions` log in context and a prompt instruction not to repeat. Highest-quality catch of missed items; most complex prompt design.
   - (c) Hybrid: skip task/appointment extraction (assume agent handled), still run tag extraction (cheap, useful). Middle ground.

2. **What's the shape of the "prior actions" payload handed to intake?** A compact JSON summary of `chat_actions` rows, flattened per object? Actual object snapshots? Both? Needs to be small enough not to blow the context budget on long chats.

3. **Who writes to `chat_actions` — the agent itself after each tool call, or a post-chat reconciler?** In-line writes are simpler and give real-time provenance. Post-hoc reconciliation is safer against agent errors but means there's a window where the data exists without the ledger entry.

4. **Idempotency on re-scan.** When a user edits an existing input (not a chat), intake needs to re-run. Should it:
   - (a) Always create new tasks/tags as if the input were new. Accept user cleanup burden.
   - (b) Diff against the prior scan's outputs (which means the prior scan needs to be recorded somewhere, probably a `scan_runs` table).
   - (c) Soft-upsert: tasks with identical title+due are merged; tags are idempotent anyway via `object_tags` PK.

5. **Commit timing for agent actions.** Does the agent commit tasks/tags/appointments as it creates them during chat, or hold them in a staging area until the chat ends (or the user approves)? Staging gives the user a "discard" affordance; immediate commit matches the "no friction, just works" design value. Probably immediate commit, with undo affordances.

6. **User correction during chat.** "No, delete that task you just made." The agent needs to know *which task it just made* — which it does via `chat_actions` if writes are in-line. Validate the round-trip works: agent tool call → DB write → `chat_actions` row → agent reads that row on follow-up turn.

7. **Input-edit vs. input-delete cascade.** If an input is deleted, do its derived tasks/appointments go with it? Default should be no (the work product stands on its own once created), but surface via `task_sources` that the originating input is gone. Confirm.

8. **Tag creation racing.** If intake and agent both try to create tag "Regina" within seconds of each other, one wins. `tags.(kind, name)` unique constraint handles the DB side; prompt/logic needs to handle the retry-and-match on the loser's side.

### Suggested sequencing for CC planning

1. Lock in the prior-actions payload format (#2) first — it constrains everything else.
2. Decide the intake behavior mode (#1). Recommendation: (c) hybrid for v1 — simpler than (b), less lossy than (a).
3. Decide commit timing (#5). Recommendation: immediate commit with undo.
4. Specify the `chat_actions` write contract (#3). Recommendation: in-line, enforced via a small wrapper around every agent tool that mutates state.
5. Build the scan_runs table if going with (b) for #4; otherwise defer.
6. Exercise with an end-to-end test: user chats ("remind me to call Regina tomorrow"), agent creates task, chat is saved, intake runs with the prior-actions payload — verify no duplicate task appears and that the tag "Regina" exists once.

### Non-goals for this round

- Multi-user concurrency. Single-user app for the foreseeable future.
- Full undo history for all agent actions. Basic undo of "the last thing" is enough for v1.
- Cross-chat deduplication (two different chats creating the same task). Accept duplicates across chats for v1; the user can merge.

---

## Deferred (not blocking the schema)

- **Naming/positioning.** "Journal" no longer fits. Candidate framings the entry circled: *personal organizer that just works*, *workspace that molds to your content*, *where your ideas talk back to you*, *turns unstructured thought into structured information*. Revisit after tasks/entities ship and the product's shape is concrete. Codename-friendly for alpha.
- **PWA specifics.** iOS PWA supports push notifications (iOS 16.4+) but share-sheet integration is effectively Android-only. Plan for a PWA with web push; accept iOS share-sheet as a later native-wrapper problem.
- **RAG vs. agentic index-tree navigation for general chat.** Implementation detail of the unified chat surface. Decide after chat is built.
- **Search bar as agent (intent disambiguation, hotkeys).** Nice-to-have UX layer on top of unified chat.
- **Task dependency UI rollup rules.** How `% complete` on a `part_of` parent is displayed. Design-time decision, not schema.

---

## Suggested build sequence

1. Refactor extraction to the two-phase candidate/disambiguation pipeline on existing topics (tests the new pipeline against known-good data).
2. Rename `entries` → `inputs`; migrate schema; keep existing features working.
3. Ship Tasks end-to-end: extraction, detail view, `task_links` UI with `part_of` default grouping. This will surface every ambiguity in the data model while the surface area is still small.
4. Ship unified chat with tag scoping; retire per-topic chat UI (it's now the unified chat pre-scoped). Wire `chat_actions` ledger. Tackle decision #5 here.
5. Ship Entities as a `kind` on tags. Add entity-specific affordances (backlinks UI, entity detail page).
6. Ship Appointments with Google Calendar write-through. Add read-mirror in a later iteration.
