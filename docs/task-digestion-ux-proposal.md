# Task Digestion UX Proposal

## Purpose

VoiceJournal already has a capable task system: quick add, natural-language intake, due dates, priorities, status, tags, dependencies, grouping, filtering, search, and bulk actions. The next leap is not to add more organization controls. The next leap is to make tasks feel effortless.

The core product idea:

> The user should be able to pour chaos into VoiceJournal without turning the task list itself into chaos.

This proposal describes a user experience layer that turns journal and voice input into a calm task assistant: one that captures freely, triages intelligently, protects the active task list, and helps the user decide what actually deserves attention.

---

## Current UX Problem

VoiceJournal encourages natural capture. That is good. But natural capture produces messy input:

> “I need to fix that mobile task thing, maybe clean up the onboarding screen, remember to follow up with Ricardo, and I’m kind of annoyed that tasks keep piling up.”

A system that converts too much of that into first-class tasks creates a backlog problem. Every thought starts to become a commitment. The user gets the relief of capture, but later pays for it with clutter.

The product should distinguish between:

- a thought
- a note
- an idea
- a possible task
- a real commitment
- a someday/maybe item
- a duplicate
- a worry or reflection

The current task system is strong once something is already a task. The missing layer is what happens before that.

---

## North Star

The desired experience:

> The user speaks freely. VoiceJournal listens, extracts, classifies, and calmly decides what should become a task, what should be reviewed, what should be parked, and what should simply remain a note.

A great response after a messy voice entry might be:

```text
Captured.

I found:
- 2 real tasks
- 3 ideas
- 1 reflection
- 1 possible duplicate

I added one task to Today:
“Fix mobile task row layout”

I held the rest for review.
```

The magic is not better filtering. The magic is that the system absorbs disorder without becoming disorderly.

---

## Guiding Principle

> Thoughts are cheap. Tasks are commitments.

This should be the central UX rule.

A journal app should allow the user to ramble. A task system should remain selective. VoiceJournal should make that distinction visible and trustworthy.

---

## Two Mental Spaces

### 1. Capture Space

Capture space is where the user can say anything.

The user should not have to think:

- Is this a task?
- Should I tag this?
- Is this a project?
- Should this be due today?
- Am I creating more clutter?

They should just speak or type.

Example:

```text
I need to remember to update the task page, but really the bigger problem is that I keep creating too many tasks from journal entries. Maybe there should be some kind of review queue.
```

VoiceJournal should respond with something calm:

```text
Captured.

I found 1 likely task and 1 product idea.
I’ll hold both for review.
```

The important part: capture does not automatically equal commitment.

### 2. Commitment Space

Commitment space is the real task list.

This is where accepted, scheduled, or intentionally added tasks live. It should feel protected. The user should trust that if something is in this space, it has earned its place.

The active task list should not be a dumping ground for every extracted action phrase.

---

## Proposed Navigation Model

The task experience should be divided into three primary views:

```text
Today | Review | All Tasks
```

### Today

Today is the calm execution surface.

It should answer:

> What actually needs my attention now?

It should not show every open task. It should not behave like a database. It should behave like an assistant’s recommendation.

Example:

```text
Today

Must
□ Fix mobile task row layout
□ Follow up with Ricardo

Should
□ Review suggested tasks from yesterday

Tiny Wins
□ Rename confusing button
□ Add due date to grocery task
```

The user should be able to complete the day from this view without opening the full task backlog.

### Review

Review is the digestion surface.

It should contain:

- suggested tasks from recent journal entries
- possible duplicates
- stale tasks
- vague tasks
- ideas that might not be tasks
- tasks that need dates or decisions
- tasks the assistant recommends parking, merging, or discarding

Review is where chaos gets processed.

### All Tasks

All Tasks is the current power-user task list.

This is where the existing functionality remains valuable:

- tag filtering
- group by topic
- sort and filters
- done/cancelled sections
- bulk select
- task detail modal
- dependencies
- recurrence

The current task page is closest to this All Tasks experience. It should remain available, but it should no longer be the default emotional center of task management.

---

## Main Page Experience

When the user opens Tasks, the page should not immediately say, “Here is your pile.”

It should summarize the situation:

```text
Today

3 things need your attention.
5 suggested tasks are waiting for review.
12 tasks are safely parked.
```

Then it should provide a small recommended focus list:

```text
Recommended Focus

1. Fix mobile task row layout
2. Follow up with Ricardo
3. Review suggested tasks from yesterday’s journal
```

The user should feel:

> The system already looked at the mess and gave me the sane version.

---

## Task Review Queue

Suggested tasks should appear as cards, not as active tasks.

Example:

```text
Suggested from your journal

□ Fix the mobile task row layout
  From: yesterday’s voice note
  Why I suggested it: You mentioned it as a concrete bug.

  [Keep] [Edit] [Later] [Not a task]

□ Clean up onboarding screen
  From: yesterday’s voice note
  Why I suggested it: Sounds like a possible improvement, but no urgency.

  [Keep] [Later] [Not a task]

□ Tasks keep piling up
  Reflection, not a task.

  [Save as note] [Dismiss]
```

The most important action is:

```text
Not a task
```

This should be a first-class action throughout the product.

In a journal-driven app, many things sound actionable but are really just thinking out loud. The UX should make it easy to say, “No, that was just a thought.”

---

## Task Gatekeeper Behavior

VoiceJournal should classify extracted items before they reach the active list.

Possible destinations:

```text
Add to Today
Add to Tasks
Hold for Review
Save as Note
Park for Later
Ignore
```

The user should not have to make this decision every time. The assistant should make a default judgment and explain it briefly.

Examples:

```text
I kept this out of your task list because it sounded like an idea, not a commitment:
“Maybe build a nicer dashboard someday.”
```

```text
I added this to Today because it has a clear deadline:
“Send the renewal email before 3 PM.”
```

```text
I held this for review because it may duplicate an existing task:
“Make task UX calmer.”
```

This makes the system feel intelligent without feeling opaque.

---

## Start Today Flow

The daily experience should be small and guided.

A “Start Today” flow might say:

```text
Good morning.

You have:
- 2 overdue tasks
- 4 open high-priority tasks
- 6 suggested tasks from recent journals
- 3 recurring tasks due today

I recommend these 3 commitments:
1. Send Ricardo the automation follow-up
2. Fix the task row mobile bug
3. Review yesterday’s suggested tasks

Moved out of the way:
- 4 vague ideas
- 3 someday tasks
- 2 stale tasks from last week
```

Primary actions:

```text
[Accept Plan] [Make Lighter] [Edit] [Show Me Why]
```

### Make Lighter

“Make Lighter” should be a first-class interaction.

Most productivity systems are good at adding and organizing. Few are good at reducing. VoiceJournal should let the user say:

> This is too much. Make it sane.

Example:

```text
Made lighter.

Kept for Today:
1. Send Ricardo the automation follow-up
2. Fix the task row mobile bug

Moved to Later:
- Review all old task tags
- Rework the task dashboard
- Clean up onboarding ideas
```

This would make the system feel humane.

---

## Task Sweep

The system should periodically offer a lightweight cleanup ritual.

Not a scary backlog review. More like a digest.

Example:

```text
Task Sweep

I found 18 open tasks.

Suggested cleanup:
- 5 look stale
- 4 are probably duplicates
- 3 are vague ideas, not tasks
- 2 should be merged into existing projects
- 4 look active and useful

Recommended action:
Keep 4, park 6, merge 4, discard 4.
```

Then the user reviews small batches.

Example duplicate card:

```text
Possible duplicate

“Fix tasks getting out of hand”
“Improve task organization”
“Make task UX effortless”

Suggested merge:
“Design a calmer task review flow”

[Merge] [Keep Separate] [Discard All]
```

This turns existing bulk actions into an assistant-led workflow instead of requiring the user to hunt manually.

---

## Later Should Feel Safe

“Later” should not feel like deletion. It should feel like trust.

When the user chooses Later, the system should offer simple choices:

```text
Later today
Tomorrow
This weekend
Next week
Someday
```

For vague ideas, default to Someday.

Example:

```text
Parked until next week.
It won’t appear in your active list unless it becomes relevant.
```

The active task list should feel protected. The parked list should feel recoverable.

---

## Inline Actions Over Detail Editing

The current task detail modal is useful for deeper editing, but the effortless path should require fewer modal opens.

Common actions should be available inline:

```text
Keep
Done
Later
Not a task
Merge
Add date
Tag
```

The detail modal should become the place for rich editing, not the normal flow.

Normal workflow should be fast triage.

---

## Assistant Explanations

The assistant should briefly explain its decisions when helpful.

Good explanations are short:

```text
I marked this low priority because there’s no deadline or consequence.
```

```text
I held this for review because it sounds like an idea, not a concrete next action.
```

```text
I grouped these because they all relate to task cleanup.
```

This builds trust without creating a verbose AI experience.

---

## Example End-to-End Flow

### User records a voice note

```text
I need to clean up the task page. There are too many filters and I think what I really need is a review queue. Also remind me to follow up with Ricardo tomorrow. And maybe someday I should make the app do a daily planning ritual.
```

### VoiceJournal responds

```text
Captured.

I found:
- 1 task for Tomorrow
- 1 product idea
- 1 possible improvement to the task system

Added to Tomorrow:
“Follow up with Ricardo”

Held for Review:
“Design a review queue for extracted tasks”
“Explore a daily planning ritual”
```

### User opens Review

```text
Suggested Tasks

Design a review queue for extracted tasks
Why: This came up as the concrete solution to your task clutter problem.

[Keep] [Edit] [Later] [Not a task]

Explore a daily planning ritual
Why: This sounded useful, but not urgent.

[Keep] [Later] [Save as idea] [Not a task]
```

### User clicks Make Lighter on Today

```text
Made lighter.

Today now has 2 commitments:
1. Fix mobile task row layout
2. Follow up with Ricardo

I parked 3 lower-urgency items for later.
```

---

## What This Should Feel Like

VoiceJournal should feel like a calm assistant who says:

> I heard everything. Only two things actually need action right now.

The user should trust that:

- they can capture freely
- the task list will not explode
- vague ideas will not become obligations
- real commitments will be surfaced
- stale tasks will be cleaned up
- “later” does not mean lost
- they can always inspect the full backlog when they want to

---

## Product Summary

Current task tools help the user organize tasks after they exist.

This proposal adds the missing layer before and around the task list:

```text
Capture → Classify → Review → Commit → Focus → Sweep
```

The most important product shift:

> Protect the active task list.

The active list should be scarce, calm, and intentional. The review queue can absorb the mess. The journal can remain infinite.

That is how VoiceJournal can turn task management from another burden into something that feels effortless.