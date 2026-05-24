# Decision Log Proposal

## Summary

Decision Log adds a first-class structured memory layer to VoiceJournal for capturing decisions, open questions, rationale, and items to revisit. It is intended to sit above raw journal entries and chats: entries and conversations remain source material, while Decision Log items become durable records of what has been decided, what is unresolved, and why.

The feature should start as a simple, topic-scoped object system with manual creation and reviewable AI suggestions. Over time it can become a higher-order planning and reasoning layer across journal entries, chats, topics, tasks, and MCP-connected external AI workflows.

## Product Problem

VoiceJournal already captures a large amount of useful thinking through voice entries, saved chats, topics, entities, and task extraction. The weakness is that important conclusions can remain buried inside long entries or conversations.

Decision Log solves this by promoting specific kinds of thinking into an operational layer:

- Decisions made
- Open questions
- Rationale and tradeoffs
- Things to revisit later
- Links back to source entries/chats
- Topic/entity associations

The goal is to help the user avoid rediscovering the same conclusions repeatedly.

## Product Principle

Decision Log should not be treated as another kind of journal note. It should be a structured object type.

A journal entry says: "Here is what I thought or said."

A topic note says: "Here is information related to this topic."

A Decision Log item says: "Here is what is settled, unresolved, or worth revisiting."

That distinction is the product value.

## MVP Scope

The MVP should include three item types:

1. Decision
2. Open Question
3. Revisit Later

The MVP should support:

- Manual creation
- Editing
- Soft deletion or dismissal
- Topic/entity tagging
- Source entry/chat linking
- Global list page
- Topic-scoped display
- Suggested items from AI extraction
- Review flow before AI-generated items become accepted

## Non-Goals for MVP

Do not start with:

- Complex decision timelines
- Full reversal/supersession graph
- Notification/reminder system
- Kanban-style decision boards
- Heavy reporting/dashboarding
- Multi-source provenance graph
- Cross-user collaboration

Those may come later, but they are not needed to prove the core value.

## Proposed Data Model

Add a new module:

```text
VoiceJournal/decisions.py
```

Add a new table:

```sql
CREATE TABLE IF NOT EXISTS decision_log_items (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  item_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  review_status TEXT NOT NULL DEFAULT 'accepted',
  title TEXT NOT NULL,
  content TEXT,
  rationale TEXT,
  alternatives TEXT,
  review_at TEXT,
  confidence TEXT,
  source TEXT NOT NULL DEFAULT 'manual',
  source_session_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  decided_at TEXT,
  soft_deleted INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_decision_log_user ON decision_log_items(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_decision_log_type ON decision_log_items(item_type);
CREATE INDEX IF NOT EXISTS idx_decision_log_status ON decision_log_items(status);
CREATE INDEX IF NOT EXISTS idx_decision_log_review_status ON decision_log_items(review_status);
CREATE INDEX IF NOT EXISTS idx_decision_log_source ON decision_log_items(source_session_id);
```

Recommended enum values:

```text
item_type: decision | open_question | revisit_later
status: open | decided | reversed | deferred
review_status: suggested | accepted | dismissed
source: manual | extraction | chat | mcp
confidence: low | medium | high
```

Use the existing generic object tagging system for topic/entity associations:

```text
object_kind = 'decision'
object_id = decision_log_items.id
```

This keeps Decision Log consistent with tasks and journal inputs, which already participate in topic/entity relationships through object_tags.

## Optional Later Source Table

For MVP, `source_session_id` is enough. Later, if one Decision Log item needs multiple sources, add:

```sql
CREATE TABLE IF NOT EXISTS decision_sources (
  decision_id TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  source_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (decision_id, source_kind, source_id)
);
```

## Core Backend API

Add CRUD helpers in `decisions.py`:

```python
def init_db(): ...
def create_item(user_id, item_type, title, content=None, rationale=None, alternatives=None,
                status='open', review_status='accepted', source='manual',
                source_session_id=None, confidence=None, review_at=None): ...
def get_item(user_id, item_id): ...
def update_item(user_id, item_id, **fields): ...
def soft_delete_item(user_id, item_id): ...
def list_items(user_id, item_type=None, status=None, review_status=None, tag_ids=None): ...
def list_for_tags(user_id, tag_ids, include_children=True): ...
def accept_suggestion(user_id, item_id): ...
def dismiss_suggestion(user_id, item_id): ...
```

Add initialization in `app.py` near the other module init calls:

```python
import decisions

decisions.init_db()
```

## Routes

Add a global Decision Log page:

```python
@app.route('/decisions')
def decisions_list(): ...
```

Add API routes:

```python
@app.route('/api/decisions', methods=['GET', 'POST'])
def api_decisions(): ...

@app.route('/api/decisions/<item_id>', methods=['GET', 'PATCH', 'DELETE'])
def api_decision_detail(item_id): ...

@app.route('/api/decisions/<item_id>/accept', methods=['POST'])
def api_decision_accept(item_id): ...

@app.route('/api/decisions/<item_id>/dismiss', methods=['POST'])
def api_decision_dismiss(item_id): ...
```

## Topic Detail Integration

Decision Log should appear directly inside topic spaces.

On `topic_detail`, load accepted Decision Log items tagged to the current topic, optionally including child topics:

```python
decision_items = decisions.list_for_tags(uid(), [topic_id], include_children=True)
```

Pass to template:

```python
return render_template(
    'topic_detail.html',
    ...,
    decision_items=decision_items,
)
```

Add a section near Summary / Entries / Tasks:

```text
Decision Log
[ Decisions ] [ Open Questions ] [ Revisit Later ]

- Title
  Type · Status · Date · Source
  Rationale preview
```

The topic page should not become overloaded. Start with a compact section and link to the filtered global Decision Log page.

## Global Decision Log UI

Create:

```text
VoiceJournal/templates/decisions.html
```

Suggested tabs:

```text
All
Decisions
Open Questions
Revisit Later
Suggested
```

Suggested filters:

```text
Topic/entity
Status
Review status
Source
Review date
```

Rows should show:

```text
Title
Type
Status
Rationale/content preview
Topic chips
Source link
Created date
```

## Review Flow

AI-generated Decision Log items should not be accepted immediately.

Suggested items should use:

```text
review_status = suggested
```

The review UI should support:

```text
Keep
Edit
Dismiss
```

This mirrors the existing suggested task digestion pattern and prevents noisy AI extraction from polluting the durable Decision Log.

## AI Extraction

Add to `llm_service.py`:

```python
def extract_decision_log_candidates(user_id, entry_id, date_str, content): ...
def extract_decision_log_items(user_id, entry_id, date_str, content): ...
```

Extraction should identify:

- Explicit decisions
- Strongly implied conclusions
- Open questions
- Deferred tradeoffs
- Items the user says they should revisit

The prompt should be conservative. Prefer fewer, higher-quality candidates.

Suggested JSON shape:

```json
{
  "candidates": [
    {
      "type": "decision",
      "title": "Use topic-scoped decision logs",
      "status": "decided",
      "content": "Decision text...",
      "rationale": "Why this was chosen...",
      "alternatives": ["Alternative A", "Alternative B"],
      "open_questions": ["Question still unresolved"],
      "review_at": null,
      "confidence": "medium",
      "tag_ids": [1, 4],
      "source_quotes": ["exact quote from entry"]
    }
  ]
}
```

Prompt rules:

- Return JSON only.
- Do not invent decisions.
- Separate decisions from open questions.
- Use `revisit_later` for unresolved tradeoffs or maybe-later ideas.
- Use existing tag IDs when a topic/entity clearly applies.
- Include exact source quotes where useful.
- Skip weak or vague thoughts.

## Extraction Pipeline Integration

Update `_run_entry_extraction` in `app.py`:

```python
def _run_entry_extraction(user_id: int, entry_id: str, date_str: str, content: str) -> None:
    topic_items = llm_service.extract_topics(user_id, entry_id, date_str, content)
    task_items = llm_service.extract_tasks(user_id, entry_id, date_str, content)
    decision_items = llm_service.extract_decision_log_items(user_id, entry_id, date_str, content)
    all_items = topic_items + task_items + decision_items
    ...
```

Decision extraction should create rows with:

```text
review_status = suggested
source = extraction
source_session_id = entry_id
```

It should also tag the item using `topics.tag_*` equivalent behavior through `object_tags`.

## Chat Save Integration

Saved chat summaries and transcripts already become journal stream inputs and trigger extraction. Decision Log extraction should run on those inputs too.

This makes the workflow possible:

1. User has a useful chat.
2. User saves it as summary or transcript.
3. VoiceJournal suggests decisions/open questions from the saved chat.
4. User accepts, edits, or dismisses them.

## Agent Tools and MCP

Add:

```text
VoiceJournal/agent_tools/decisions.py
```

Suggested tools:

```text
create_decision_log_item
list_decision_log_items
update_decision_log_item
link_decision_to_topic
dismiss_decision_suggestion
```

These should be registered as `ToolDef` instances like the other agent tools.

Because MCP exposes `agent_tools`, this will also make Decision Log available to Claude Code and external MCP clients after deployment.

This matters for the external-AI workflow:

> "Enter this into my Decision Log."

A chat agent or MCP client should be able to create a Decision Log item directly with source metadata and topic links.

## Proposed Agent Tool Schema: create_decision_log_item

```python
ToolDef(
    name='create_decision_log_item',
    description='Create a Decision Log item for a decision, open question, or revisit-later item.',
    input_schema={
        'type': 'object',
        'properties': {
            'item_type': {'type': 'string', 'enum': ['decision', 'open_question', 'revisit_later']},
            'title': {'type': 'string'},
            'content': {'type': 'string'},
            'rationale': {'type': 'string'},
            'alternatives': {'type': 'array', 'items': {'type': 'string'}},
            'status': {'type': 'string', 'enum': ['open', 'decided', 'reversed', 'deferred']},
            'tag_ids': {'type': 'array', 'items': {'type': 'integer'}},
            'review_at': {'type': ['string', 'null']},
            'confidence': {'type': 'string', 'enum': ['low', 'medium', 'high']}
        },
        'required': ['item_type', 'title']
    },
    handler=create_decision_log_item_handler,
    mutates=True,
    object_kind='decision',
)
```

## Implementation Plan

### Phase 1: Durable object and manual UI

Deliver:

- `decisions.py`
- `decision_log_items` table
- `decisions.init_db()` in `app.py`
- `/decisions` page
- create/edit/delete APIs
- topic/entity tagging through `object_tags`
- compact Decision Log section on topic detail page

This proves the object model and product surface.

### Phase 2: Suggested extraction

Deliver:

- `llm_service.extract_decision_log_candidates()`
- `llm_service.extract_decision_log_items()`
- integration into `_run_entry_extraction`
- suggested review tab on `/decisions`
- source links back to entries/chats

This adds the core AI value without automatically cluttering the system.

### Phase 3: Agent and MCP support

Deliver:

- `agent_tools/decisions.py`
- create/list/update tools
- MCP exposure through existing tool registry
- support for external AI ingestion workflows

### Phase 4: Decision evolution

Deliver later:

- supersedes / reversed-by relationships
- decision history timeline
- review reminders
- recurring review queues
- richer source provenance
- decision diffs/change log

## Open Questions

- Should `open_question` use `status='open'` by default while `decision` uses `status='decided'`?
- Should suggested Decision Log items appear in the existing extraction summary on entry detail?
- Should topic detail show all child-topic decisions by default or only direct tags?
- Should decisions support markdown content from day one?
- Should `alternatives` be stored as JSON text or normalized into a separate table?
- Should decisions have first-class links to tasks, or should that wait until later?

## Recommendation

Start with a first-class Decision Log object, not a topic-note convention.

The smallest high-value implementation is:

```text
Manual Decision Log item creation
+ topic-scoped display
+ global Decision Log page
+ suggested extraction review
+ agent/MCP create/list/update tools
```

This gives VoiceJournal a durable layer for conclusions and unresolved questions while preserving the current architecture: journal/chats as source material, topics/entities as organization, tasks as execution, and MCP/agent tools as the automation surface.
