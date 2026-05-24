# Guided Journal Redesign Proposal

## Context

VoiceJournal currently includes a guided journaling feature behind the **Prompt Me** button on the main journal screen.

The current implementation works, but it is still shaped like a modal helper layered onto the journal index. The next version should feel more like a focused guided journaling session, especially on mobile and especially when using the microphone.

This proposal covers three related improvements:

1. Improve the mobile and microphone visual experience.
2. Improve the prompt-generation logic so questions are more contextual and purposeful.
3. Add support for reusable prompting **Play Books**.

A key product constraint: **typing must remain fast, direct, and frictionless.** Any voice-first redesign must not make the user jump through extra steps when they simply want to type an answer.

---

## Current Implementation Summary

Relevant files:

- `VoiceJournal/templates/index.html`
- `VoiceJournal/app.py`
- `VoiceJournal/llm_service.py`
- `VoiceJournal/static/style.css`

Current behavior:

- The journal index includes a **Prompt Me** button.
- The button opens a guided journal modal.
- The first question is: `What is your objective for this journaling session?`
- The browser keeps guided-session state in the inline `GuidedJournal` JavaScript object.
- The backend endpoint `/api/guided-journal/question` receives:
  - `objective`
  - `answers`
  - `skipped_questions`
- The backend calls `llm_service.guided_journal_question(...)`.
- The LLM receives recent journal context and returns one question as plain text.
- The microphone flow records audio in the browser, sends it to `/api/guided-journal/transcribe`, inserts the transcript into the textarea, and lets the user submit it.

The current version is a good v1, but the architecture and UX are not yet strong enough for a richer guided journaling experience.

---

## Main Product Principle

The redesigned flow should support two equally valid behaviors:

1. **Quick typing** — the user opens Prompt Me, sees a question, types an answer immediately, and submits with minimal friction.
2. **Voice journaling** — the user taps the mic, gets a polished listening/transcribing/review experience, and continues through the guided session.

The redesign should not treat typing as a secondary fallback.

### Non-negotiable typing constraint

Do **not** replace the main answer box with a voice-only interaction.

Do **not** require a separate “review mode” for typed answers.

Do **not** make users choose between “type mode” and “voice mode” before answering.

The user should always be able to:

1. Read the prompt.
2. Put the cursor in the answer field.
3. Type immediately.
4. Press submit.

The voice UI should enhance this experience, not interrupt it.

---

## Recommended UX Direction

### Preserve the fast textarea path

The current textarea is valuable because it creates a very low-friction path for typed answers. Keep that path.

Recommended layout:

```text
Prompt card / question

Answer textarea

[Mic] [Submit] [Skip] [Done]
```

The improvement should be visual and structural, not frictional.

The textarea can be styled more beautifully and supported by voice-specific states, but it should remain immediately available.

### Make the microphone experience stateful and polished

The current code already has a recording overlay, live audio feedback, waveform bars, and a full-screen mobile treatment. The issue is not lack of microphone functionality; the issue is that the states are implicit and visually muddled.

Model the guided UI around explicit states:

```js
"objective"
"idle"
"recording"
"transcribing"
"reviewing_answer"
"generating_question"
"saving"
"error"
```

These states should affect labels, button disabled states, status text, and visual treatment.

### Important nuance: reviewing should not add friction

For dictated answers, a lightweight review state is useful because transcription may need correction.

For typed answers, there should be no separate review step.

Recommended behavior:

- User types answer → clicks **Submit** → next question.
- User dictates answer → transcript appears in the same answer field → user may edit or immediately click **Submit**.
- No modal-within-modal.
- No required confirmation step.
- No separate “Keep Answer” step unless later added as an optional affordance.

The answer field itself is the review surface.

---

## Proposed Mobile Layout

Mobile should feel dedicated and calm, but still fast.

Recommended hierarchy:

```text
Guided Journal / Play Book name
Question X of Y

Prompt card

Answer box

Status line

[Mic] [Submit]
[Skip] [Done]
```

The main improvement is not to remove the textarea. The improvement is to give the prompt, answer field, mic state, and controls a clearer hierarchy.

### Mobile typing path

The mobile typing path should be:

1. Tap **Prompt Me**.
2. See the first question.
3. Keyboard can appear immediately or after tapping the answer field.
4. Type.
5. Tap **Submit**.

No pre-mode selection should be required.

### Mobile voice path

The mobile voice path should be:

1. Tap **Prompt Me**.
2. Tap the mic button.
3. See a clear listening state.
4. Tap to stop.
5. See `Transcribing your answer...`.
6. Transcript appears in the same answer box.
7. Edit or submit.

---

## Microphone State Improvements

### Idle

Show the prompt and the answer field. The mic button is available but not visually dominant over typing.

Suggested status copy:

```text
Type your answer or tap the mic to dictate.
```

### Recording

The current waveform and animated mic orb are a strong foundation. Keep the visual energy here.

Suggested copy:

```text
Listening...
Tap when finished
```

### Transcribing

Currently the status text says `processing audio`. Replace with warmer, clearer copy:

```text
Transcribing your answer...
```

This state should make it visually clear that the app is working and the user did not lose their recording.

### Review after dictation

After transcription, insert the transcript into the same answer field.

Suggested status copy:

```text
Transcript added. Edit anything you want, then submit.
```

This preserves control without adding a separate review step.

### Generating next question

Current copy: `Choosing the next question...`

Suggested copy:

```text
Finding the next helpful question...
```

### Saving

Current copy: `Saving journal entry...`

This is fine, though it could become:

```text
Saving your guided journal entry...
```

---

## Architectural Recommendation: Add a Guided Session Model

The current session exists only in browser memory.

Current front-end state includes:

```js
let objective = '';
let loggedAnswers = [];
let skippedQuestions = [];
let currentQuestion = objectiveQuestion;
let isObjective = true;
```

This is workable for v1, but limits future features.

Problems:

- Refreshing loses the session.
- The backend has no durable session ID.
- The backend does not explicitly know the session step.
- Play Books would have to be repeatedly passed from the browser.
- Debugging prompt quality is difficult.
- Resuming a guided session is impossible.

Recommended future data model:

```python
GuidedJournalSession = {
    "id": str,
    "user_id": int,
    "objective": str,
    "playbook_id": str | None,
    "status": "active | completed | abandoned",
    "current_step": int,
    "target_question_count": int,
    "created_at": str,
    "updated_at": str,
}

GuidedJournalExchange = {
    "id": str,
    "session_id": str,
    "question": str,
    "answer": str,
    "playbook_step": int | None,
    "question_type": str | None,
    "created_at": str,
}
```

This does not need to be the first implementation step, but the design should move in this direction.

---

## Prompt Generation Review

Current `guided_journal_question(...)` is a plain-text question generator. It receives:

- time context
- objective
- previous session answers
- already asked questions
- skipped questions
- last 7 days of journal context
- example morning/midday/evening prompts

This is a reasonable starting point, but the prompt is too flat for the desired experience.

Current limitation:

- The examples are not ordered.
- There is no concept of a prompt sequence.
- There is no Play Book.
- The model is not told what step the session is on.
- The model is not told whether to clarify, deepen, summarize, or close.
- The endpoint returns only a string, so the app cannot inspect why a question was chosen.

---

## Proposed Prompt Contract

The LLM should return structured JSON rather than only a question string.

Suggested response:

```json
{
  "question": "What part of this feels heaviest right now?",
  "question_type": "emotional_check_in",
  "playbook_step": 3,
  "is_closing_question": false,
  "debug_reason": "The user has described the situation but not the emotional weight."
}
```

Only `question` should be shown in the UI.

The other fields can be stored or used for debugging.

---

## Improved Prompt Draft

```python
system = (
    "You are a guided journaling facilitator. "
    "Return JSON only. Do not include markdown."
)

user_prompt = f"""
Current local time:
{time_context}

User objective:
{objective.strip() or "(assistant should choose a helpful direction)"}

Current question number:
{len(answers) + 1}

Previous questions and answers:
{session_context or "(none yet)"}

Already asked questions:
{already_asked}

Skipped questions:
{skipped_context}

Recent journal context:
{recent_context.strip()[:8000] or "(no recent journal context)"}

Your task:
Generate the next single journaling question.

Rules:
- Ask exactly one question.
- The question should be brief and easy to answer by voice.
- The question should also be natural to answer by typing.
- Use the user's objective and previous answers.
- Use recent journal context only when it is clearly relevant.
- Do not repeat or closely paraphrase already asked or skipped questions.
- If the user is vague, ask a clarifying question.
- If the user has already reflected deeply, move toward insight or next action.
- Avoid therapy jargon.
- Avoid generic self-help language.
- Do not ask multiple questions at once.

Return JSON only in this shape:
{{
  "question": "one brief question",
  "question_type": "objective | clarification | reflection | emotional_check_in | perspective | action | closing",
  "debug_reason": "one short sentence explaining why this question fits"
}}
"""
```

---

## Prompting Play Books

A Play Book is a reusable guided prompt strategy.

It should not be just a saved prompt. It should be an ordered sequence of prompt moves.

Example:

```python
DEFAULT_GUIDED_PLAYBOOKS = {
    "clear_my_head": {
        "title": "Clear My Head",
        "description": "Untangle scattered thoughts and find the next small point of clarity.",
        "target_question_count": 5,
        "steps": [
            {
                "name": "Surface the noise",
                "purpose": "Help the user name what is currently taking up mental space.",
                "examples": [
                    "What has been taking up the most space in your mind today?"
                ],
            },
            {
                "name": "Separate facts from feelings",
                "purpose": "Help the user distinguish what happened from what they feel about it.",
                "examples": [
                    "What are the facts of the situation, and what feelings are attached to them?"
                ],
            },
            {
                "name": "Find the weight",
                "purpose": "Identify the most emotionally loaded part.",
                "examples": [
                    "What part of this feels heavier than the rest?"
                ],
            },
        ],
    }
}
```

### Example ordering

When using example questions from a Play Book, the app should use them in order by step.

The model may adapt the wording to the user's objective and prior answers, but it should not randomly select examples from the whole Play Book.

Rule:

```text
Use the current Play Book step as the primary guide. Do not jump ahead unless the user's latest answer clearly requires a clarification or grounding question first.
```

---

## Recommended Default Play Books

Ship a small set of high-quality default Play Books before building custom Play Book editing.

### Clear My Head

Purpose: untangle scattered thoughts.

Sequence:

1. What is taking up space?
2. What happened?
3. What feels heaviest?
4. What do I need?
5. What would make things lighter?

### Process Something Hard

Purpose: emotional reflection.

Sequence:

1. What happened?
2. What did it bring up?
3. What part still feels unresolved?
4. What would compassion say?
5. What do I want to carry forward?

### Plan My Day

Purpose: practical clarity.

Sequence:

1. What matters most today?
2. What must get done?
3. What could derail me?
4. What is the smallest next action?
5. How do I want to end the day?

### End-of-Day Review

Purpose: reflection and closure.

Sequence:

1. What happened today?
2. What went well?
3. What felt off?
4. What did I learn?
5. What can I release tonight?

### Decision Journal

Purpose: think through a choice.

Sequence:

1. What decision am I facing?
2. What are the real options?
3. What am I optimizing for?
4. What am I afraid of?
5. What choice seems wisest right now?

---

## First-Screen Recommendation

The current first screen asks:

```text
What is your objective for this journaling session?
```

Recommended refinement:

```text
What do you want this journaling session to help with?
```

Below that, show optional Play Book chips.

Example:

```text
What do you want this journaling session to help with?

[Answer field]

Saved Play Books
[Clear My Head]
[Plan My Day]
[Process Something Hard]
[Decision Journal]
[End-of-Day Review]

[Mic] [Start]
```

Important: selecting a Play Book should be optional.

The user should still be able to type a custom objective and start immediately.

---

## Implementation Order

### Phase 1: Improve current UX without changing data model

- Add explicit front-end UI states.
- Improve status copy.
- Preserve the main textarea as the immediate answer surface.
- Improve mobile hierarchy.
- Make dictated transcript review happen inside the same answer field.
- Keep typing path exactly as fast as it is now.

### Phase 2: Change question generation to structured JSON

- Update `llm_service.guided_journal_question(...)` to request JSON.
- Parse the model response safely.
- Return metadata from `/api/guided-journal/question`.
- Display only the question.

### Phase 3: Add built-in Play Books

- Add built-in Play Book definitions in Python.
- Let the front end pass `playbook_id`.
- Pass current Play Book step to the LLM.
- Use examples in order.

### Phase 4: Persist sessions

- Add a guided-session table.
- Add guided-session-answer table.
- Save generated question metadata.
- Allow resume or abandonment later.

### Phase 5: Add saved/custom Play Books

- Let users save custom Play Books.
- Let users choose saved Play Books on the first screen.
- Add simple create/edit/delete UI only after the default Play Books feel useful.

---

## Implementation Guardrails

- Do not make the user select typing vs voice mode.
- Do not add a required review step for typed answers.
- Do not hide the answer field unless actively recording.
- Do not make the mic UI visually dominate the typed-answer path while idle.
- Do not overcomplicate the first release with a custom Play Book editor.
- Do not remove the existing low-friction textarea workflow.
- Do bump the service worker cache version in `static/sw.js` after changing CSS or inline JS behavior.

---

## Summary

The guided journal feature should evolve from a modal helper into a focused guided journaling session experience.

The most important product tension is balancing voice-first polish with typing-first speed. The correct answer is not to replace the textarea-centered workflow entirely. The correct answer is to preserve the instant typing path while making the microphone states feel much more intentional.

The next best technical move is to introduce explicit UI states and improve the prompt-generation contract. Play Books should come next, starting as built-in ordered prompt sequences before adding full custom Play Book management.
