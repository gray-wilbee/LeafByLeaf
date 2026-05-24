# VoiceJournal Admin, Tasks, Extraction, and Topic Context Updates

## Summary

Build an admin dashboard, approval/reset flow, richer usage reporting, hierarchical topic context, stronger task/tag workflows, entry extraction feedback, and per-user timezone support. Reuse the existing Flask/templates/SQLite structure, existing tag/task tables, and AI Gateway usage logging where possible.

## Key Changes

- Add admin/account fields to `users`: `is_admin`, `approved_at`, `approved_by`, `disabled_at`; migrate user id `1` to admin and approved.
- Change registration to create a pending account and block login until approved; admins can approve users from `/admin`.
- Add one-use password reset tokens with expiry; admin can generate a reset URL and send it manually.
- Add a Settings timezone selector with four choices: Pacific, Mountain, Central, Eastern.
- Use the selected timezone for user-facing dates/times, upload entry timestamps, task intake defaults, and recurring-task next-date calculation.
- Add admin pages:
  - user table with username, approved status, timezone, API-key configured status, created date, quick approve/reset actions.
  - user detail/usage view with timeframe controls for logins, entries, words, upload logs, task counts, and AI usage/cost.
  - admin log view must show only received/status/time/words/error, with no entry links and no content access.
- Add login event tracking so admin can count logins by timeframe.
- Verify user-specific upload API keys: retain hashed per-user keys, show only configured/not configured in admin, and ensure uploads via a user key resolve to that user.

## Implementation Changes

### Timezone

- Store timezone in settings as an IANA value, defaulting existing users to `America/Chicago`.
- Settings labels map to `America/Los_Angeles`, `America/Denver`, `America/Chicago`, and `America/New_York`.
- Replace hardcoded `CST` user-time calculations with a helper that resolves the current user's timezone.

### AI Usage

- Update every VoiceJournal gateway call, including transcription and LLM calls, to send `X-User: <user_id>`.
- Extend AI Gateway usage endpoints to support explicit `start`/`end` timestamps in addition to `days`, and filter by `app=voice-journal` and `user=<id>`.
- Historical rows without `user` remain aggregate-only.

### Topic Chat Context

- For scoped topic chats, load the selected topic, all ancestors up to the root, and all descendants below it.
- Do not load sibling branches or unrelated branches.

### Task Tags and Filters

- Refactor task extraction to tag created tasks with relevant extracted topic/entity tags.
- Replace the task tag filter dropdown with a searchable modal, tabbed `Topics`/`Entities`, defaulting to root topics.
- Topic filter applies the selected topic plus all descendants, not ancestors.
- Make due dates clickable/editable from task list rows using the same save path as task detail.

### Recurring Tasks

- Add a lightweight natural-language recurrence rule field on tasks.
- When a recurring task is marked done, call a Haiku parsing helper with the user's selected timezone, current date/time, prior due date, and rule; create the next task occurrence with the returned due date.
- Store recurrence provenance linking occurrences; no background scheduler in v1.

### Topic Detail Task Tab

- Add `Entries`/`Tasks` tabs on topic detail.
- Tasks tab shows tasks tagged to the topic or descendant topics, using the same task row/status/due-date controls as `/tasks`, without the tag filter.

### Entry Extraction Feedback

- Store extraction run summary and extracted task/topic/entity item records per entry.
- Show the extraction panel on entry detail: 1-2 paragraph summary, extracted tasks with X/restore, and extracted topics/entities.
- X soft-deletes extracted tasks from task lists and crosses out the item in the extraction panel; restore clears the soft-delete.
- Add an entry-scoped follow-up chat that has the entry, extraction result, and normal agent tools in context.

### Related Tag Backlinks

- Extend extraction to propose related topic/entity links and persist them in existing `tag_links`.
- Exclude direct parent/child and sibling topic relationships from "related topics"; allow cross-branch/cousin relationships.
- Show related topics/entities on both topic and entity detail pages using the existing connections model.

## Public Interfaces

- New routes: `/admin`, `/admin/users/<id>`, `/admin/users/<id>/logs`, `/reset-password/<token>`.
- New admin APIs: approve user, generate reset link, fetch usage by timeframe.
- New task APIs: inline due-date update, recurrence rule update, restore soft-deleted extracted task.
- New tag/search APIs: searchable task filter modal data with topic root/default and descendant expansion.
- New extraction APIs: entry extraction result fetch and entry-scoped follow-up chat creation.
- Settings adds a `timezone` key with one of the four supported IANA timezone values.

## Test Plan

### Auth/Admin

- New registrations cannot log in until approved.
- Non-admin users cannot access `/admin` or admin APIs.
- Admin can approve a user and generate a one-use reset link; expired/used tokens fail.

### Timezone

- Settings saves each supported timezone.
- Upload timestamps, task default due dates, and recurrence next dates follow the selected timezone.
- Existing users default to Central.

### Privacy

- Admin user logs do not include entry links or entry content.
- Admin usage views show counts/costs only.

### API Keys and Usage

- Each user has a distinct hashed upload API key.
- Upload with user A key creates logs/entries for user A.
- Gateway usage rows include `app=voice-journal` and `user=<id>` after the header change.

### Tasks

- Extracted tasks get topic/entity tags.
- Topic task filter includes descendant topics only.
- Due date edits work from list rows and detail modal.
- Completing a recurring task creates exactly one next occurrence.
- Soft-deleted extracted tasks disappear from task lists and restore correctly.

### Topics/Entities

- Topic chat context includes ancestors and descendants, excluding siblings.
- Topic detail tasks tab matches `/tasks` controls.
- Related links exclude parent/child/sibling topic links and appear on both sides.

## Assumptions

- Username is the displayed "name" for admin tables because registration currently has no separate display-name field.
- First user/admin migration is acceptable: user id `1` becomes admin automatically.
- Password reset links are manually copied by the admin; no email delivery is added.
- Recurrence v1 stores natural-language rules and uses Haiku to calculate the next due date on completion.
- Historical AI Gateway usage cannot be accurately split per user if old rows lack `user`.
