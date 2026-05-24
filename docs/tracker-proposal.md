# Tracker Proposal

## Summary

Tracker adds a configurable time-series data layer to VoiceJournal. It lets users define anything they want to observe over time, then capture, infer, visualize, edit, and export that data.

Health, habits, recovery, and symptoms are important early use cases, but Tracker should not be architected as a health module or habit checklist. It should be a broader custom logging system that supports arbitrary repeated observations: routines, workouts, sleep, mood, hydration, sales activity, creative output, app development metrics, spending, focus, family routines, and more.

The core loop is:

```text
Define what to track → collect/infer values → show patterns → ask for missing info → user edits/confirms → export
```

## Product Problem

VoiceJournal already captures a lot of lived context through voice entries, saved chats, topics, entities, tasks, and app activity. But users often want to understand repeated facts over time:

- Am I keeping up with habits?
- How is my recovery trending?
- How have symptoms changed?
- How much did I work out this week?
- How much water did I drink?
- When did I wake up?
- How was I feeling each day?
- Did I pray, drink, smoke, journal, or complete a routine?
- What does the last 7 days or last 30 days look like?

Raw journal entries contain clues, but they do not automatically produce a structured longitudinal view.

Tracker solves this by turning repeated facts into user-defined rows and fields that can be visualized, queried, corrected, completed, and exported.

## Core Product Principle

A tracker is a custom schema plus time-indexed observations.

The app should separate:

1. What the user wants to track
2. How often it should be tracked
3. What fields exist and what type each field is
4. Which values are manual, inferred, system-derived, or unknown
5. Which values are confirmed by the user
6. Which rows are incomplete and need follow-up
7. How the data should be visualized and exported

Tracker should be closer to a user-defined table and visualization layer than a predefined habit module.

## Example Use Cases

### Daily habits and recovery

User says:

> I want a daily log of whether I said my morning prayers, whether I drank or smoked, and whether I had any journal entries.

The app proposes a daily tracker with fields:

- Morning prayers: boolean
- Drank alcohol: boolean
- Smoked: boolean
- Had journal entry: system-derived boolean

Some values can be inferred from entries or app activity. Others should be asked when missing.

### Daily wellness

User says:

> I want to keep daily notes of how much water I drank, when I woke up, how much I slept, and how I was feeling.

The app proposes fields:

- Water intake: number with unit
- Wake time: time
- Sleep duration: duration or number
- Feeling: scale or text
- Notes: text

### Health facts and symptoms

Alpha user says:

> I want to track how I am feeling each day and monitor symptoms.

The app proposes fields:

- Overall feeling: scale
- Symptoms: multi-select or text
- Symptom severity: scale
- Notes: text
- Medication or treatment: optional text

The app should frame this as personal observation, not medical advice.

### Weekly workouts

User says:

> I want a log of how much I worked out each week.

The app proposes a weekly tracker with fields:

- Workout count
- Total workout minutes
- Workout types
- Notes

### Non-health examples

Tracker should also support cases like:

- Weekly sales activity
- Daily creative output
- Time spent on app development
- Family routine completion
- Reading progress
- Spending categories
- Focus level
- Screen time notes
- Practice sessions

## MVP Scope

The MVP should support:

- User-created custom trackers
- Daily and weekly cadences
- User-defined fields
- Field types: boolean, number, text, scale, time, duration, select, multi-select
- Manual row editing
- AI-assisted schema proposal from natural language
- AI-assisted extraction from journal entries and saved chats
- System-derived values where appropriate
- Incomplete row detection
- Follow-up questions for missing or uncertain values
- Calendar visualization
- Last 7 days view
- Last 30 days view
- Table view
- CSV export
- Topic/entity tagging for trackers

## Non-Goals for MVP

Do not start with:

- A hard-coded health schema
- A hard-coded habit schema
- Wearable/device integrations
- Medical diagnosis or medical recommendations
- Advanced statistical analysis
- Complex formulas
- Multi-user shared trackers
- Automated push notifications
- Fully generic dashboard builder

The key non-goal is over-specializing the system around any one use case. Health and habits are presets, not the architecture.

## Proposed Data Model

Add a new module:

```text
VoiceJournal/trackers.py
```

### trackers

```sql
CREATE TABLE IF NOT EXISTS trackers (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  cadence TEXT NOT NULL DEFAULT 'daily',
  timezone TEXT,
  start_date TEXT,
  end_date TEXT,
  prompt_instructions TEXT,
  extraction_instructions TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_trackers_user ON trackers(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_trackers_cadence ON trackers(cadence);
```

Recommended cadence values:

```text
daily | weekly | monthly | ad_hoc
```

### tracker_fields

```sql
CREATE TABLE IF NOT EXISTS tracker_fields (
  id TEXT PRIMARY KEY,
  tracker_id TEXT NOT NULL REFERENCES trackers(id),
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  field_key TEXT NOT NULL,
  field_type TEXT NOT NULL,
  description TEXT,
  required INTEGER NOT NULL DEFAULT 0,
  options_json TEXT,
  unit TEXT,
  min_value REAL,
  max_value REAL,
  inference_policy TEXT NOT NULL DEFAULT 'ask_if_missing',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(tracker_id, field_key)
);

CREATE INDEX IF NOT EXISTS idx_tracker_fields_tracker ON tracker_fields(tracker_id, sort_order);
```

Recommended field types:

```text
boolean | number | text | scale | time | duration | select | multi_select
```

Recommended inference policies:

```text
manual_only | infer_when_explicit | infer_when_likely | system_computed | ask_if_missing
```

Examples:

```text
Smoked today → infer_when_explicit + ask_if_missing
Had journal entry → system_computed
Mood → infer_when_likely
Water intake → infer_when_explicit + ask_if_missing
Workout count → infer_when_explicit or aggregate weekly
```

### tracker_rows

```sql
CREATE TABLE IF NOT EXISTS tracker_rows (
  id TEXT PRIMARY KEY,
  tracker_id TEXT NOT NULL REFERENCES trackers(id),
  user_id INTEGER NOT NULL,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'incomplete',
  source TEXT NOT NULL DEFAULT 'manual',
  source_session_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(tracker_id, period_start)
);

CREATE INDEX IF NOT EXISTS idx_tracker_rows_tracker_period ON tracker_rows(tracker_id, period_start);
CREATE INDEX IF NOT EXISTS idx_tracker_rows_status ON tracker_rows(status);
```

Recommended row statuses:

```text
empty | incomplete | inferred | confirmed | manually_edited
```

### tracker_values

```sql
CREATE TABLE IF NOT EXISTS tracker_values (
  id TEXT PRIMARY KEY,
  row_id TEXT NOT NULL REFERENCES tracker_rows(id),
  field_id TEXT NOT NULL REFERENCES tracker_fields(id),
  user_id INTEGER NOT NULL,
  value_json TEXT,
  confidence TEXT,
  source TEXT NOT NULL DEFAULT 'manual',
  source_session_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(row_id, field_id)
);

CREATE INDEX IF NOT EXISTS idx_tracker_values_row ON tracker_values(row_id);
CREATE INDEX IF NOT EXISTS idx_tracker_values_field ON tracker_values(field_id);
```

Recommended confidence values:

```text
unknown | low | medium | high | user_confirmed
```

Recommended value sources:

```text
manual | inferred | system | chat | mcp
```

### tracker_questions

This table powers the mechanism for asking users to fill missing or uncertain values.

```sql
CREATE TABLE IF NOT EXISTS tracker_questions (
  id TEXT PRIMARY KEY,
  tracker_id TEXT NOT NULL REFERENCES trackers(id),
  row_id TEXT REFERENCES tracker_rows(id),
  field_id TEXT REFERENCES tracker_fields(id),
  user_id INTEGER NOT NULL,
  question TEXT NOT NULL,
  reason TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  answered_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracker_questions_user_status ON tracker_questions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tracker_questions_tracker ON tracker_questions(tracker_id);
```

Recommended question statuses:

```text
open | answered | dismissed | expired
```

## Natural-Language Tracker Creation

The app should support a natural-language setup flow.

User says:

> I want a daily log of whether I said my morning prayers, whether I drank or smoked, and whether I had any journal entries.

The app proposes:

```json
{
  "name": "Daily Habits and Recovery",
  "cadence": "daily",
  "fields": [
    {
      "name": "Morning prayers",
      "field_type": "boolean",
      "required": true,
      "inference_policy": "ask_if_missing"
    },
    {
      "name": "Drank alcohol",
      "field_type": "boolean",
      "required": true,
      "inference_policy": "infer_when_explicit"
    },
    {
      "name": "Smoked",
      "field_type": "boolean",
      "required": true,
      "inference_policy": "infer_when_explicit"
    },
    {
      "name": "Had journal entry",
      "field_type": "boolean",
      "required": false,
      "inference_policy": "system_computed"
    }
  ]
}
```

The user should be able to approve or edit the proposed schema before creating the tracker.

## Manual Editing

Every tracker row should be directly editable.

The table view should let the user:

- Add a missing row
- Edit values
- Mark a row confirmed
- Dismiss an inferred value
- Answer missing questions
- Add notes
- Change the row period

Manual edits should set:

```text
row.status = manually_edited
value.confidence = user_confirmed
value.source = manual
```

Manual correction should be treated as a first-class part of the product, not an exception path.

## AI Extraction

Add to `llm_service.py`:

```python
def propose_tracker_schema(user_id, natural_language_request): ...
def infer_tracker_values(user_id, input_id, date_str, content, trackers): ...
```

Extraction should run after a journal entry or saved chat is created, similar to topic/task extraction.

For each relevant tracker, the model receives:

- Tracker name
- Tracker cadence
- Field definitions
- Inference policies
- Existing values for the period
- Journal/chat content
- Date/context

It returns candidate values and follow-up questions:

```json
{
  "tracker_values": [
    {
      "tracker_id": "abc123",
      "period_start": "2026-05-16",
      "field_key": "drank_alcohol",
      "value": false,
      "confidence": "medium",
      "reason": "The user explicitly said they did not drink today."
    }
  ],
  "questions": [
    {
      "tracker_id": "abc123",
      "period_start": "2026-05-16",
      "field_key": "smoked",
      "question": "Did you smoke today?",
      "reason": "The journal entry did not mention smoking."
    }
  ]
}
```

Extraction rules:

- Do not infer absence unless field instructions allow it.
- Prefer unknown when the source material is insufficient.
- Ask follow-up questions for required missing values.
- Store source links for inferred values.
- Preserve confidence and source metadata.

## Missing Information and Follow-Up Questions

When a row is incomplete or uncertain, the app should create questions.

Examples:

```text
For your Daily Habits tracker, did you say morning prayers today?
For your Health Symptoms tracker, how would you rate your headache severity today?
For your Wellness tracker, about how many hours did you sleep last night?
For your Weekly Workouts tracker, how many sessions did you complete this week?
```

Questions can appear in:

- Tracker detail page
- Home page
- Chat
- Future notification system

For MVP, in-app prompts are enough.

## Visualization Requirements

### Calendar View

Best for daily boolean, scale, and presence/absence fields.

Examples:

- Green/red day dots for habit completion
- Heatmap for symptom severity
- Icons for journaled/worked out/drank/smoked
- Empty-state indicators for missing data

### Last 7 Days

Best for quick feedback.

Examples:

- Checklist-style rows
- Mini bar charts
- Simple field cards
- Trend hints for numeric fields

### Last 30 Days

Best for pattern recognition.

Examples:

- Calendar heatmap
- Line chart for numeric values
- Stacked bars for boolean counts
- Summary metrics

### Table View

Required for accuracy and editing.

Rows represent periods. Columns represent fields.

Example:

```text
Date | Prayers | Drank | Smoked | Journal Entry | Notes | Status
```

### CSV Export

Each tracker should support CSV download.

Route:

```python
@app.route('/trackers/<tracker_id>.csv')
def tracker_csv(tracker_id): ...
```

CSV shape:

```text
period_start,period_end,morning_prayers,drank_alcohol,smoked,had_journal_entry,row_status
```

## App Routes

Add global pages:

```python
@app.route('/trackers')
def trackers_list(): ...

@app.route('/trackers/<tracker_id>')
def tracker_detail(tracker_id): ...

@app.route('/trackers/<tracker_id>.csv')
def tracker_csv(tracker_id): ...
```

Add API routes:

```python
@app.route('/api/trackers', methods=['GET', 'POST'])
def api_trackers(): ...

@app.route('/api/trackers/propose', methods=['POST'])
def api_trackers_propose(): ...

@app.route('/api/trackers/<tracker_id>', methods=['GET', 'PATCH', 'DELETE'])
def api_tracker_detail(tracker_id): ...

@app.route('/api/trackers/<tracker_id>/rows', methods=['GET', 'POST'])
def api_tracker_rows(tracker_id): ...

@app.route('/api/trackers/<tracker_id>/rows/<row_id>', methods=['PATCH'])
def api_tracker_row_update(tracker_id, row_id): ...

@app.route('/api/tracker-questions', methods=['GET'])
def api_tracker_questions(): ...

@app.route('/api/tracker-questions/<question_id>/answer', methods=['POST'])
def api_tracker_question_answer(question_id): ...
```

## Topic Integration

Trackers should be taggable to topics/entities using the existing object tagging system:

```text
object_kind = 'tracker'
object_id = tracker.id
```

Topic pages can show a compact Trackers section:

```text
Trackers
- Daily Habits and Recovery
- Health Symptoms
- Weekly Workouts
- App Development Metrics
```

## Agent Tools and MCP

Add:

```text
VoiceJournal/agent_tools/trackers.py
```

Suggested tools:

```text
create_tracker
propose_tracker_schema
list_trackers
get_tracker_data
update_tracker_row
answer_tracker_question
export_tracker_csv
```

This enables in-app chat and MCP workflows such as:

> Create a daily tracker for prayers, drinking, smoking, and journal entries.

> How did my sleep and mood look over the last 30 days?

> Fill in yesterday: I drank two glasses of water, slept six hours, and felt anxious.

> Create a weekly tracker for app development time and number of shipped changes.

## Example Tracker Schemas

### Daily habits and recovery

```json
{
  "name": "Daily Habits and Recovery",
  "cadence": "daily",
  "fields": [
    {"name": "Morning prayers", "field_type": "boolean", "required": true},
    {"name": "Drank alcohol", "field_type": "boolean", "required": true},
    {"name": "Smoked", "field_type": "boolean", "required": true},
    {"name": "Had journal entry", "field_type": "boolean", "required": false, "inference_policy": "system_computed"}
  ]
}
```

### Health symptoms

```json
{
  "name": "Daily Health Symptoms",
  "cadence": "daily",
  "fields": [
    {"name": "Overall feeling", "field_type": "scale", "min_value": 1, "max_value": 10, "required": true},
    {"name": "Symptoms", "field_type": "multi_select", "options": ["headache", "fatigue", "nausea", "pain", "anxiety", "other"]},
    {"name": "Symptom severity", "field_type": "scale", "min_value": 1, "max_value": 10},
    {"name": "Notes", "field_type": "text"}
  ]
}
```

### Daily wellness

```json
{
  "name": "Daily Wellness",
  "cadence": "daily",
  "fields": [
    {"name": "Water intake", "field_type": "number", "unit": "oz"},
    {"name": "Wake time", "field_type": "time"},
    {"name": "Sleep duration", "field_type": "duration", "unit": "hours"},
    {"name": "Feeling", "field_type": "text"}
  ]
}
```

### Weekly workouts

```json
{
  "name": "Weekly Workouts",
  "cadence": "weekly",
  "fields": [
    {"name": "Workout count", "field_type": "number", "unit": "sessions"},
    {"name": "Total minutes", "field_type": "number", "unit": "minutes"},
    {"name": "Workout types", "field_type": "multi_select", "options": ["strength", "cardio", "mobility", "sport", "walk"]},
    {"name": "Notes", "field_type": "text"}
  ]
}
```

### App development metrics

```json
{
  "name": "App Development Metrics",
  "cadence": "weekly",
  "fields": [
    {"name": "Hours worked", "field_type": "number", "unit": "hours"},
    {"name": "Features shipped", "field_type": "number", "unit": "features"},
    {"name": "Bugs fixed", "field_type": "number", "unit": "bugs"},
    {"name": "Main focus", "field_type": "text"},
    {"name": "Notes", "field_type": "text"}
  ]
}
```

## Privacy and Safety Notes

Health-related trackers should be framed as personal records, not diagnosis.

The app should avoid medical claims such as:

- Diagnosing conditions
- Recommending medication changes
- Telling users symptoms are safe or dangerous without appropriate caveats

Safe framing:

```text
This tracker helps you record and review your own observations over time. It is not medical advice.
```

This does not prevent health tracking. It simply keeps the feature positioned as personal observation and recordkeeping.

## Implementation Plan

### Phase 1: Manual custom trackers

Deliver:

- `trackers.py`
- tracker tables
- `trackers.init_db()` in `app.py`
- `/trackers` list page
- `/trackers/<tracker_id>` detail page
- manual tracker creation
- manual row editing
- table view
- CSV export

### Phase 2: Visualization

Deliver:

- calendar view
- last 7 days view
- last 30 days view
- field-specific simple visualizations
- empty/incomplete state indicators

Use plain server-rendered data and lightweight frontend JS first. Avoid overbuilding a dashboard framework.

### Phase 3: AI schema proposal and extraction

Deliver:

- natural-language tracker setup proposal
- extraction from journal entries/saved chats
- inferred values with confidence
- missing-value question generation
- review page for tracker questions

### Phase 4: Agent/MCP tools

Deliver:

- tracker agent tools
- chat workflows for creating trackers and filling rows
- MCP access to create, update, query, and export tracker data

### Phase 5: Smarter insights

Deliver later:

- trend summaries
- correlation hints
- weekly/monthly rollups
- recurring reviews
- richer charts
- export bundles

## Open Questions

- Should monthly cadence be included in MVP or deferred after daily/weekly?
- Should tracker rows be generated proactively for each period, or lazily when data exists?
- Should field options be editable after rows already exist?
- Should `alternatives` like "unknown" be represented as null value, explicit unknown, or row status?
- Should system-derived fields be implemented in MVP or after manual/inferred values?
- Should trackers have a notes field at the row level in addition to user-defined text fields?
- Should tracker questions appear on the home page immediately, or only inside tracker pages at first?

## Recommendation

Build Tracker as a broad custom data layer, not a health/habit-specific module.

The smallest high-value version is:

```text
Custom tracker schemas
+ daily/weekly rows
+ direct table editing
+ CSV export
+ basic calendar/7-day/30-day views
+ missing-value questions
+ natural-language schema proposal
```

That foundation supports health, habits, recovery, workouts, and many other use cases without locking the app into a narrow category.
