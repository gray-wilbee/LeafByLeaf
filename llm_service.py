from __future__ import annotations

import json
import hashlib
import logging
import os
import threading
import difflib
import time
import re
import requests as req_lib
from datetime import datetime, timedelta

import utils
import ai_models
import journal
import topics
import tasks as tasks_db
import logs as logs_db
import decisions as decisions_db
import trackers as trackers_db

logger = logging.getLogger(__name__)

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8001")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")


def _headers(user_id=None, anthropic_version=True, anthropic_beta=None):
    headers = {"X-Internal-Token": INTERNAL_TOKEN, "X-App": "voice-journal"}
    if user_id is not None:
        headers["X-User"] = str(user_id)
    if anthropic_version:
        headers["anthropic-version"] = "2023-06-01"
    if anthropic_beta:
        headers["anthropic-beta"] = anthropic_beta
    return headers


def _bucket_for_legacy_model(model: str | None, default: str = "lite") -> str:
    if model and "sonnet" in model:
        return "regular"
    if model and "opus" in model:
        return "regular"
    return default


def _ai_text_call(
    *,
    system: str = "",
    user: str,
    bucket: str,
    max_tokens: int,
    user_id=None,
    timeout: int = 120,
    thinking: bool = False,
    anthropic_beta: str | None = None,
    func_key: str | None = None,
) -> str:
    if func_key and user_id is not None:
        preset = ai_models.get_func_preset(func_key, user_id)
    else:
        preset = ai_models.get_preset(bucket, user_id)

    def _call():
        r = ai_models.text_request(
            gateway_url=GATEWAY_URL,
            headers=_headers(
                user_id,
                anthropic_version=preset.provider == "anthropic",
                anthropic_beta=anthropic_beta if preset.provider == "anthropic" else None,
            ),
            preset=preset,
            system=system,
            user=user,
            max_tokens=max_tokens,
            thinking=thinking and preset.provider == "anthropic",
            timeout=timeout,
        )
        r.raise_for_status()
        return ai_models.response_text(preset.provider, r.json())

    return utils.with_retry(_call)


def format_transcript(raw_text: str, user_id=None) -> str:
    """Clean up a raw Whisper transcript. Splits large inputs into chunks automatically."""
    system_prompt = (
        "You are a transcript formatter. The user will provide a raw voice memo transcript. "
        "Your job is to reformat it into clean, readable markdown paragraphs.\n\n"
        "Rules:\n"
        "- Do not paraphrase or summarize. Preserve the speaker's exact words and meaning.\n"
        "- Break into natural paragraphs at topic shifts or sentence clusters.\n"
        "- Remove obvious filler words (um, uh, like, you know) only where they add no meaning.\n"
        "- Fix punctuation and capitalization.\n"
        "- Do not add headings, bullets, or any structure beyond paragraphs.\n"
        "- Return only the formatted text, nothing else."
    )

    def _format_chunk(chunk: str) -> str:
        return _ai_text_call(
            system=system_prompt,
            user=chunk,
            bucket="lite",
            max_tokens=8000,
            user_id=user_id,
            timeout=120,
            func_key="transcript_format",
        )

    return '\n\n'.join(_format_chunk(c) for c in utils.chunk_text(raw_text, max_words=4000))


def get_embedding(text: str, user_id=None) -> list[float]:
    """Embed text using OpenAI text-embedding-3-small via the AI gateway."""
    def _call():
        r = req_lib.post(
            f"{GATEWAY_URL}/proxy/openai/v1/embeddings",
            json={"model": "text-embedding-3-small", "input": text},
            headers=_headers(user_id, anthropic_version=False),
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]
    return utils.with_retry(_call)


def extract_candidates(chunk: str, date_str: str, existing_tags: list | None = None,
                        user_profile: str = "", sort_instructions: str = "",
                        user_id=None) -> list[dict]:
    tag_catalog = ""
    if existing_tags:
        lines = []
        for t in existing_tags[:120]:
            desc = (t.get("description") or "")[:70]
            desc_part = f" — {desc}" if desc else ""
            lines.append(f"  [{t['kind']}] {t['name']}{desc_part}")
        tag_catalog = (
            "\n\nExisting tags in this journal (use these exact names when the entry discusses the same concept):\n"
            + "\n".join(lines)
            + "\n"
        )

    user_msg = (
        f"Journal entry for {date_str}:\n\n{chunk}\n\n"
        "Identify all topics AND named entities meaningfully discussed.\n"
        "Return JSON only (no markdown wrapper):\n"
        '{"candidates": [{'
        '"name": "Short name (1-4 words)", '
        '"kind": "topic or entity", '
        '"description": "2-3 sentence description — who or what this is, key context, why it matters", '
        '"content": "detailed markdown of pertinent content from this entry", '
        '"key_sentences": ["exact verbatim quote from the entry text"], '
        '"parent_name": "name of a broader topic this is a sub-aspect of — use exact catalog name OR exact name of another candidate in this list; omit if no suitable parent exists", '
        '"related_names": ["names of other extracted or existing tags with a strong direct relationship"]'
        "}]}\n"
        "Rules:\n"
        "- kind='topic': a theme, subject, project, or recurring idea.\n"
        "- kind='entity': a specific named person, organization, place, or product/tool.\n"
        "- Only include candidates with substantial discussion — skip passing mentions.\n"
        "- key_sentences: 1-3 exact character-for-character quotes from the entry text above.\n"
        "- content: thorough markdown summarizing everything relevant in the entry.\n"
        "- parent_name: OPTIONAL but strongly encouraged. Set when this topic is a specific instance, event, session, or sub-aspect of a broader recurring theme. Examples: 'Family Evening' → parent 'Family'; 'Morning Run' → parent 'Exercise'; 'Q2 Budget Review' → parent 'Work'. You may reference another candidate in this extraction by its exact name, or an existing catalog entry. Do NOT invent a parent that appears in neither. Err toward using parent_name rather than leaving specific topics flat.\n"
        "- related_names: OPTIONAL. Include only tags with a strong direct relationship, such as a project-to-workflow, person-to-family topic, product-to-project, or topic-to-specific initiative relationship. Do not include tags merely because they appear in the same entry."
        + tag_catalog
        + (f"\n\nAdditional sorting rules:\n{sort_instructions.strip()}" if sort_instructions and sort_instructions.strip() else "")
    )

    profile_block = (
        f"\n\nAbout the journal author:\n{user_profile.strip()}"
        if user_profile and user_profile.strip() else ""
    )
    system_prompt = (
        "You are analyzing a journal entry to identify topics, entities, and extract structured summaries. "
        "Focus on what is actually discussed in depth, not surface-level mentions."
        + profile_block
    )

    result_text = _ai_text_call(
        system=system_prompt,
        user=user_msg,
        bucket="regular",
        max_tokens=8000,
        user_id=user_id,
        timeout=180,
        thinking=True,
        anthropic_beta="interleaved-thinking-2025-05-14",
        func_key="topic_extraction",
    )
    if result_text.startswith("```"):
        result_text = result_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(result_text).get("candidates", [])


def extract_candidates_resilient(chunk: str, date_str: str, existing_tags: list | None = None,
                                  user_profile: str = "", sort_instructions: str = "",
                                  min_words: int = 350, user_id=None) -> list[dict]:
    try:
        return extract_candidates(
            chunk,
            date_str,
            existing_tags=existing_tags,
            user_profile=user_profile,
            sort_instructions=sort_instructions,
            user_id=user_id,
        )
    except json.JSONDecodeError:
        word_count = len(chunk.split())
        if word_count <= min_words:
            logger.exception("Topic extraction JSON failed for %s-word chunk on %s; skipping chunk", word_count, date_str)
            return []

        import math
        subchunk_size = max(min_words, math.ceil(word_count / 2))
        candidates: list[dict] = []
        for subchunk in utils.chunk_text(chunk, max_words=subchunk_size):
            candidates.extend(extract_candidates_resilient(
                subchunk,
                date_str,
                existing_tags=existing_tags,
                user_profile=user_profile,
                sort_instructions=sort_instructions,
                min_words=min_words,
                user_id=user_id,
            ))
        return candidates


def merge_candidates(candidates: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for c in candidates:
        key = utils.normalize_tag_name(c["name"])
        if key in merged:
            merged[key]["content"] += "\n\n" + c.get("content", "")
            merged[key]["key_sentences"].extend(c.get("key_sentences") or [])
            merged[key]["related_names"].extend(c.get("related_names") or [])
            if not merged[key].get("parent_name"):
                merged[key]["parent_name"] = c.get("parent_name")
        else:
            merged[key] = {
                "name": c["name"],
                "kind": c.get("kind", "topic"),
                "description": c.get("description", ""),
                "content": c.get("content", ""),
                "key_sentences": list(c.get("key_sentences") or []),
                "parent_name": c.get("parent_name"),
                "related_names": list(c.get("related_names") or []),
            }
    return list(merged.values())


def ai_confirm_match(candidate: dict, shortlist: list, kind: str, user_id=None) -> int | None:
    candidates_for_ai = [
        {"id": t["id"], "name": t["name"],
         "description": t.get("description") or "",
         "keywords": t.get("keywords") or ""}
        for t in shortlist[:6]
    ]
    prompt = (
        f"I extracted a {kind} from a journal entry and need to match it to an existing record, or mark it as new.\n\n"
        f"Extracted {kind}:\n"
        f"  name: {candidate['name']}\n"
        f"  description: {candidate.get('description', '(none)')}\n\n"
        f"Existing {kind}s:\n"
        + json.dumps(candidates_for_ai, indent=2)
        + "\n\nIf the extracted item clearly refers to the same real-world person, place, organization, or concept "
        "as one of the existing records, reply with ONLY that record's numeric id (e.g. '42'). "
        "If it is genuinely different or you are not confident, reply with ONLY the word 'new'."
    )

    try:
        result = _ai_text_call(user=prompt, bucket="lite", max_tokens=16, user_id=user_id, timeout=30)
        if result.isdigit():
            return int(result)
    except Exception:
        logger.exception("AI match confirmation failed for candidate: %s", candidate['name'])
    return None


def disambiguate_candidate(user_id: int, candidate: dict, existing_topics: list, existing_entities: list) -> int:
    kind = candidate.get("kind", "topic")
    name = candidate["name"]
    normalized = utils.normalize_tag_name(name)

    existing = existing_entities if kind == "entity" else existing_topics
    create_fn = lambda name, desc=None: (topics.create_entity(user_id, name, desc) if kind == "entity"
                                          else topics.create_topic(user_id, name, desc))

    # Fast path: case-insensitive exact name match — catches capitalization variants
    # like "Family evening" matching an existing "Family Evening"
    for tag in existing:
        if utils.normalize_tag_name(tag["name"]) == normalized:
            return tag["id"]

    candidate_text = name
    if candidate.get("description"):
        candidate_text += ". " + candidate["description"]

    candidate_vec = None
    try:
        candidate_vec = get_embedding(candidate_text, user_id=user_id)
    except Exception:
        logger.exception("Embedding failed for candidate: %s", name)

    haiku_shortlist = []
    import difflib

    if candidate_vec is not None:
        embedded = []
        unembedded = []
        for tag in existing:
            if tag.get("embedding"):
                embedded.append(tag)
            else:
                unembedded.append(tag)

        scored = []
        for tag in embedded:
            try:
                tag_vec = json.loads(tag["embedding"])
                score = utils.cosine_sim(candidate_vec, tag_vec)
                scored.append((score, tag))
            except Exception:
                unembedded.append(tag)

        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            best_score, best_tag = scored[0]
            if best_score >= 0.85:
                return best_tag["id"]
            if best_score >= 0.72:
                haiku_shortlist = [t for score, t in scored if score >= 0.72][:6]

        for tag in unembedded:
            tag_norm = utils.normalize_tag_name(tag["name"])
            score = difflib.SequenceMatcher(None, normalized, tag_norm).ratio()
            if score >= 0.82:
                return tag["id"]
            if score >= 0.5 or normalized in tag_norm or tag_norm in normalized:
                haiku_shortlist.append(tag)

    else:
        for tag in existing:
            tag_norm = utils.normalize_tag_name(tag["name"])
            score = difflib.SequenceMatcher(None, normalized, tag_norm).ratio()
            if score >= 0.82:
                return tag["id"]
            if score >= 0.5 or normalized in tag_norm or tag_norm in normalized:
                haiku_shortlist.append(tag)

    if haiku_shortlist:
        result = ai_confirm_match(candidate, haiku_shortlist, kind, user_id=user_id)
        if result is not None:
            return result

    return create_fn(name, candidate.get("description") or None)


def update_tag_metadata(user_id: int, tag_id: int, kind: str, name: str, description: str, content: str) -> None:
    keywords = None
    kw_prompt = (
        f"Generate a list of alternate names, nicknames, aliases, or search keywords for this {kind}.\n\n"
        f"Name: {name}\n"
        f"Description: {description}\n"
        f"Context snippet: {content[:600]}\n\n"
        "Return ONLY a comma-separated list of 5-10 short keywords or alternate names. No explanation, no punctuation beyond commas."
    )

    try:
        keywords = _ai_text_call(user=kw_prompt, bucket="lite", max_tokens=120, user_id=user_id, timeout=30)
        update_fn = topics.update_entity if kind == "entity" else topics.update_topic
        update_fn(user_id, tag_id, keywords=keywords)
    except Exception:
        logger.exception("Keyword generation failed for tag_id: %s", tag_id)

    embed_text = name
    if description:
        embed_text += ". " + description
    if keywords:
        embed_text += ". Also known as: " + keywords
    try:
        vec = get_embedding(embed_text, user_id=user_id)
        topics.store_tag_embedding(tag_id, json.dumps(vec))
    except Exception:
        logger.exception("Operation failed")


def backfill_tag_embeddings() -> None:
    try:
        missing = topics.list_tags_missing_embeddings()
        for tag in missing:
            embed_text = tag["name"]
            if tag.get("description"):
                embed_text += ". " + tag["description"]
            if tag.get("keywords"):
                embed_text += ". Also known as: " + tag["keywords"]
            try:
                vec = get_embedding(embed_text, user_id=tag.get("user_id"))
                topics.store_tag_embedding(tag["id"], json.dumps(vec))
            except Exception:
                logger.exception("Embedding generation failed for tag_id: %s", tag['id'])
    except Exception:
        logger.exception("Tag embedding backfill failed")


def generate_extraction_summary(content: str, items: list[dict], user_id=None) -> str:
    item_lines = "\n".join(f"- {i['item_type']}: {i['name']}" for i in items[:30])
    return ai_call(
        "You summarize journal intake work. Return one concise paragraph, no heading.",
        f"Journal entry excerpt:\n\n{content[:2000]}\n\nExtracted items:\n{item_lines or '(none)'}\n\n"
        "Summarize what was captured and organized for the user.",
        bucket="lite",
        max_tokens=180,
        user_id=user_id,
        func_key="intake_summary",
    )


def _link_strong_related_tags(user_id: int, relations: list[tuple[int, int]]) -> None:
    tag_ids = sorted({tid for pair in relations for tid in pair})
    parent_map = topics.get_parent_tag_ids(tag_ids)
    for a, b in relations:
        if a == b:
            continue
        if parent_map.get(a) == b or parent_map.get(b) == a:
            continue
        pa, pb = parent_map.get(a), parent_map.get(b)
        if pa is not None and pa == pb:
            continue
        topics.add_tag_link(user_id, min(a, b), max(a, b), source="extraction")


def extract_topics(user_id: int, entry_id: str, date_str: str, content: str) -> list[dict]:
    extraction_items: list[dict] = []
    try:
        chunks = utils.chunk_text(content, max_words=1200)
        existing_topics = topics.list_topics(user_id)
        existing_entities = topics.list_entities(user_id)
        existing_tags_ctx = [
            {"name": t["name"], "kind": t["kind"], "description": t.get("description")}
            for t in existing_topics + existing_entities
        ]
        user_profile = topics.get_setting(user_id, "user_profile")
        sort_instructions = topics.get_setting(user_id, "sort_instructions")

        raw_candidates: list[dict] = []
        for chunk in chunks:
            raw_candidates.extend(extract_candidates_resilient(
                chunk, date_str,
                existing_tags=existing_tags_ctx,
                user_profile=user_profile,
                sort_instructions=sort_instructions,
                user_id=user_id,
            ))
        candidates = merge_candidates(raw_candidates)
        tag_ids_used: list[int] = []
        candidate_tag_ids: dict[str, int] = {}
        pending_parents: list[tuple[int, str]] = []  # (tag_id, parent_name) resolved after loop
        for candidate in candidates:
            tag_id = disambiguate_candidate(user_id, candidate, existing_topics, existing_entities)
            candidate_tag_ids[utils.normalize_tag_name(candidate["name"])] = tag_id
            # Collect parent assignments for later — resolved after all candidates are
            # disambiguated so co-extracted topics (e.g. "Family" + "Family Evening" both
            # new) can parent each other correctly.
            if candidate.get("parent_name") and candidate.get("kind", "topic") == "topic":
                pending_parents.append((tag_id, candidate["parent_name"]))
            topics.upsert_topic_entry(user_id, tag_id, date_str, candidate["content"])
            tag_ids_used.append(tag_id)
            kind = candidate.get("kind", "topic")
            extraction_items.append({"item_type": kind, "item_id": str(tag_id), "name": candidate["name"]})
            if candidate.get("key_sentences"):
                topics.store_highlights(user_id, entry_id, tag_id, candidate["key_sentences"])
            threading.Thread(
                target=update_tag_metadata,
                args=(user_id, tag_id, kind, candidate["name"],
                      candidate.get("description", ""), candidate.get("content", "")),
                daemon=True,
            ).start()

        if tag_ids_used:
            topics.tag_entry(user_id, entry_id, tag_ids_used)

        # Build full lookup: existing topics/entities + topics created this run
        tag_lookup = {
            utils.normalize_tag_name(t["name"]): t["id"]
            for t in existing_topics + existing_entities
        }
        tag_lookup.update(candidate_tag_ids)

        # Resolve pending parent assignments now that tag_lookup is complete
        for tag_id, parent_name in pending_parents:
            parent_id = tag_lookup.get(utils.normalize_tag_name(parent_name))
            if parent_id and parent_id != tag_id:
                current = topics.get_topic(user_id, tag_id)
                if current and current.get("parent_tag_id") is None:
                    topics.update_topic(user_id, tag_id, parent_tag_id=parent_id)
        strong_relations: list[tuple[int, int]] = []
        for candidate in candidates:
            from_id = candidate_tag_ids.get(utils.normalize_tag_name(candidate["name"]))
            if not from_id:
                continue
            for related_name in candidate.get("related_names") or []:
                to_id = tag_lookup.get(utils.normalize_tag_name(str(related_name)))
                if to_id:
                    strong_relations.append((min(from_id, to_id), max(from_id, to_id)))
        if strong_relations:
            _link_strong_related_tags(user_id, list(set(strong_relations)))
    except Exception:
        logger.exception("Topic extraction failed for entry %s user_id=%s", entry_id, user_id)
    return extraction_items


def extract_task_candidates(chunk: str, date_str: str, tag_context: list | None = None, user_id=None) -> list[dict]:
    tags_json = json.dumps([
        {"id": t["id"], "name": t["name"], "kind": t["kind"]}
        for t in (tag_context or [])[:120]
    ])
    user_msg = (
        f"Journal entry for {date_str}:\n\n{chunk}\n\n"
        "Identify all actionable tasks, to-dos, or commitments mentioned.\n"
        "Return JSON only (no markdown wrapper):\n"
        '{"candidates": [{'
        '"title": "Verb-first action (2-8 words)", '
        '"emoji": "single most fitting emoji for this task", '
        '"priority": "high or medium or low", '
        '"due_at": "YYYY-MM-DD if a specific date is mentioned, else null", '
        '"description": "brief context or notes from the entry", '
        '"tag_ids": [numeric ids from the provided tag catalog that directly relate to this task]'
        "}]}\n"
        "Rules:\n"
        "- Only include clear action items — skip vague intentions or general discussion.\n"
        "- Title must start with a verb (Call, Email, Buy, Finish, Schedule, etc.).\n"
        "- emoji: choose one emoji that best represents the task (e.g., 📞 for calls, 🚗 for car, ⚾ for sports).\n"
        "- priority: infer from urgency/importance language; default medium.\n"
        "- If no tasks are found, return {\"candidates\": []}.\n"
        f"- Tag catalog for tag_ids: {tags_json}"
    )

    result_text = _ai_text_call(
        system=(
            "You are a task extraction assistant. "
            "Identify concrete action items and to-dos from journal entries."
        ),
        user=user_msg,
        bucket="lite",
        max_tokens=2048,
        user_id=user_id,
        timeout=60,
        func_key="task_extraction",
    )
    if result_text.startswith("```"):
        result_text = result_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(result_text).get("candidates", [])


def ai_confirm_duplicate_task(candidate: dict, shortlist: list[dict], user_id=None) -> str | None:
    existing = [
        {
            "id": t["id"],
            "title": t["title"],
            "description": t.get("description") or "",
            "due_at": t.get("due_at"),
            "status": t.get("status"),
        }
        for t in shortlist[:8]
    ]
    prompt = (
        "Decide whether an extracted task is already represented by an existing active task.\n\n"
        f"Extracted task:\n{json.dumps(candidate, indent=2)}\n\n"
        f"Existing tasks:\n{json.dumps(existing, indent=2)}\n\n"
        "Return ONLY the matching existing task id if it is the same real-world action. "
        "Return ONLY new if it should be added as a separate task."
    )
    try:
        result = _ai_text_call(
            system="You are a strict task deduplication assistant.",
            user=prompt,
            bucket="lite",
            max_tokens=32,
            user_id=user_id,
            timeout=45,
            func_key="task_dedup",
        ).strip()
        if any(t["id"] == result for t in shortlist):
            return result
    except Exception:
        logger.exception("Task duplicate confirmation failed")
    return None


def find_duplicate_task(user_id: int, candidate: dict, existing_tasks: list[dict]) -> str | None:
    title = (candidate.get("title") or "").strip()
    norm = utils.normalize_tag_name(title)
    if not norm:
        return None

    shortlist = []
    for task in existing_tasks:
        task_norm = utils.normalize_tag_name(task.get("title") or "")
        if not task_norm:
            continue
        if norm == task_norm:
            return task["id"]
        score = difflib.SequenceMatcher(None, norm, task_norm).ratio()
        if score >= 0.88 or norm in task_norm or task_norm in norm:
            return task["id"]
        if score >= 0.58:
            shortlist.append(task)

    if not shortlist:
        return None
    return ai_confirm_duplicate_task(candidate, shortlist, user_id=user_id)


def extract_tasks(user_id: int, entry_id: str, date_str: str, content: str) -> list[dict]:
    extraction_items: list[dict] = []
    try:
        chunks = utils.chunk_text(content, max_words=4000)
        entry_tags = topics.get_tags_for_entry(entry_id)
        tag_context = entry_tags or topics.list_all_tags(user_id)
        raw_candidates: list[dict] = []
        for chunk in chunks:
            raw_candidates.extend(extract_task_candidates(chunk, date_str, tag_context=tag_context, user_id=user_id))

        seen: set[str] = set()
        existing_tasks = tasks_db.list_active_for_dedupe(user_id)
        for candidate in raw_candidates:
            key = utils.normalize_tag_name(candidate["title"])
            if key in seen:
                continue
            seen.add(key)
            duplicate_id = find_duplicate_task(user_id, candidate, existing_tasks)
            if duplicate_id:
                extraction_items.append({
                    "item_type": "task_duplicate",
                    "item_id": duplicate_id,
                    "name": f"Skipped duplicate: {candidate['title']}",
                })
                continue
            task_id = tasks_db.create_task(
                user_id,
                title=candidate["title"],
                description=candidate.get("description") or None,
                emoji=candidate.get("emoji") or None,
                priority=candidate.get("priority") or "medium",
                due_at=candidate.get("due_at") or None,
                status="suggested",
                source="scan",
                source_session_id=entry_id,
            )
            tasks_db.link_input(user_id, task_id, entry_id)
            tag_ids = {int(t) for t in (candidate.get("tag_ids") or []) if str(t).isdigit()}
            if tag_ids:
                topics.tag_task(user_id, task_id, list(tag_ids), tag_source="intake_inferred")
            extraction_items.append({"item_type": "task", "item_id": task_id, "name": candidate["title"]})
            existing_tasks.insert(0, tasks_db.get_task(user_id, task_id))
    except Exception:
        logger.exception("Operation failed")
    return extraction_items


def ai_call(system: str, user: str, model: str | None = None,
             max_tokens: int = 1024, user_id=None, bucket: str | None = None,
             func_key: str | None = None) -> str:
    return _ai_text_call(
        system=system,
        user=user,
        bucket=bucket or _bucket_for_legacy_model(model),
        max_tokens=max_tokens,
        user_id=user_id,
        timeout=120,
        func_key=func_key,
    )


def ai_refresh_description(topic_name: str, all_content: str, user_id=None) -> str:
    return ai_call(
        "You write concise topic descriptions. Return only the description — 1-2 sentences, no preamble.",
        f"Topic: {topic_name}\n\nAll topic notes:\n\n{all_content}\n\n"
        "Write a concise 1-2 sentence description of this topic based on its notes.",
        user_id=user_id,
        func_key="topic_description",
    )


def ai_refresh_summary(topic_name: str, description: str, all_content: str, user_id=None) -> str:
    return ai_call(
        "You write concise topic summaries. Return only the summary markdown — no preamble.",
        f"Topic: {topic_name}\nDescription: {description}\n\nAll topic notes:\n\n{all_content}\n\n"
        "Write a concise summary of everything in these notes. "
        "Keep it to 2-3 short paragraphs maximum. Use markdown sparingly — prefer prose over bullets.",
        bucket="regular",
        max_tokens=512,
        user_id=user_id,
        func_key="topic_summary",
    )


def ai_compact(topic_name: str, all_content: str, user_id=None) -> str:
    return ai_call(
        "You consolidate topic notes into a single detailed summary. Return only the markdown — no preamble.",
        f"Topic: {topic_name}\n\nAll topic notes (newest first):\n\n{all_content}\n\n"
        "Consolidate all these notes into one comprehensive, well-organized entry. "
        "Preserve all important details, decisions, progress, and context. "
        "Use markdown with headers and bullets.",
        bucket="regular",
        max_tokens=4096,
        user_id=user_id,
        func_key="topic_summary",
    )


def ai_summarize_chat(transcript: str, user_id=None) -> str:
    return ai_call(
        "You summarize conversations into concise topic notes. Return only the summary — no preamble.",
        f"Chat transcript:\n\n{transcript}\n\nSummarize the key insights, decisions, and "
        "information from this conversation as topic notes.",
        user_id=user_id,
    )


def _chat_title_excerpt(messages: list, max_messages: int = 8, max_chars: int = 700) -> str:
    excerpts = []
    for m in messages:
        content_type = m.get("content_type", "text")
        if content_type == "tool_results":
            continue

        role = str(m.get("role") or "").title() or "Message"
        content = m.get("content") or ""
        if content_type == "tool_turn":
            try:
                blocks = json.loads(content) if isinstance(content, str) else content
            except Exception:
                blocks = []
            text_parts = [
                block.get("text", "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            content = " ".join(part.strip() for part in text_parts if part.strip())
        elif not isinstance(content, str):
            content = json.dumps(content)

        content = re.sub(r"\s+", " ", content).strip()
        if not content or content.startswith("[{\"type\": \"tool_"):
            continue
        excerpts.append(f"{role}: {content[:max_chars]}")
        if len(excerpts) >= max_messages:
            break
    return "\n".join(excerpts)


_TITLE_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "for",
    "from", "go", "how", "i", "is", "it", "making", "my", "of", "on", "or",
    "should", "that", "the", "this", "to", "we", "what", "with", "you",
}


def _clean_chat_title(title: str | None) -> str:
    clean = re.sub(r"\s+", " ", title or "").strip()
    clean = clean.strip(" \t\r\n\"'`*_~")
    clean = re.sub(r"[\s.?!,:;—-]+$", "", clean).strip()
    return clean[:120].strip()


def _valid_chat_title(title: str) -> bool:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'&/-]*", title)
    if len(title) < 10 or len(words) < 2:
        return False
    if title.lower() in {"new chat", "chat title", "conversation title"}:
        return False
    if len(words) > 12:
        return False
    return True


def _valid_entry_title(title: str) -> bool:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'&/-]*", title)
    if len(title) < 8 or len(words) < 2:
        return False
    if title.lower() in {"journal entry", "entry title", "title"}:
        return False
    if len(words) > 12:
        return False
    return True


def fallback_chat_title(messages: list) -> str:
    for m in messages:
        if m.get("role") != "user" or m.get("content_type", "text") != "text":
            continue
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'&/-]*", str(m.get("content") or ""))
        picked = [w for w in words if w.lower() not in _TITLE_STOPWORDS]
        picked = picked[:7] or words[:7]
        if picked:
            return " ".join(w.capitalize() if w.islower() else w for w in picked)[:120]
    return "New Chat"


def ai_name_chat(messages: list, user_id=None) -> str:
    excerpt = _chat_title_excerpt(messages)
    system = (
        "Generate a short chat title, 4-7 words, that captures the main topic. "
        "Return only the complete title. No quotes, no punctuation at the end."
    )
    user_prompt = (
        f"Conversation:\n{excerpt}\n\n"
        "Write one complete 4-7 word title. Keep it specific and natural."
    )

    title = _clean_chat_title(ai_call(
        system,
        user_prompt,
        bucket="lite",
        max_tokens=512,
        user_id=user_id,
    ))
    if _valid_chat_title(title):
        return title

    return fallback_chat_title(messages)


def ai_name_entry(content: str, user_id=None) -> str:
    system = (
        "Generate a concise journal entry title. "
        "Return only the complete title, no quotes, no punctuation at the end."
    )
    user_prompt = (
        f"Journal entry:\n\n{content[:3000]}\n\n"
        "Title requirements: 3-8 words, specific, natural, not a date."
    )

    title = _clean_chat_title(ai_call(
        system,
        user_prompt,
        bucket="lite",
        max_tokens=512,
        user_id=user_id,
    ))
    if _valid_entry_title(title):
        return title

    return journal.fallback_entry_title(content)


def guided_journal_question(
    *,
    recent_context: str,
    time_context: str,
    objective: str = "",
    answers: list[dict] | None = None,
    skipped_questions: list[str] | None = None,
    playbook: dict | None = None,
    user_id=None,
) -> dict:
    answers = answers or []
    skipped_questions = skipped_questions or []
    answered_questions = [str(a.get("question") or "").strip() for a in answers if a.get("question")]
    session_context = "\n".join(
        f"Q: {str(a.get('question') or '').strip()}\nA: {str(a.get('answer') or '').strip()}"
        for a in answers
        if str(a.get("question") or "").strip() and str(a.get("answer") or "").strip()
    )
    skipped_context = "\n".join(f"- {q}" for q in skipped_questions if str(q).strip()) or "(none)"
    already_asked = "\n".join(f"- {q}" for q in answered_questions if q) or "(none)"

    question_number = len(answers) + 1
    playbook_context = "(none selected)"
    if playbook:
        steps = playbook.get("steps") or []
        step_index = min(max(question_number - 1, 0), max(len(steps) - 1, 0)) if steps else 0
        current_step = steps[step_index] if steps else {}
        playbook_context = json.dumps({
            "id": playbook.get("id"),
            "title": playbook.get("title"),
            "description": playbook.get("description"),
            "target_question_count": playbook.get("target_question_count"),
            "current_step_number": step_index + 1 if steps else None,
            "current_step": current_step,
        }, ensure_ascii=False)

    system = (
        "You are a guided journaling facilitator. Return JSON only. "
        "Do not include markdown, commentary, or multiple questions."
    )
    user_prompt = (
        f"Current local time:\n{time_context}\n\n"
        f"User objective:\n{objective.strip() or '(assistant should choose a helpful direction)'}\n\n"
        f"Current question number:\n{question_number}\n\n"
        f"Selected Play Book:\n{playbook_context}\n\n"
        f"Previous answers in this guided session:\n{session_context or '(none yet)'}\n\n"
        f"Already asked questions:\n{already_asked}\n\n"
        f"Skipped questions:\n{skipped_context}\n\n"
        f"Recent journal context:\n{recent_context.strip()[:8000] or '(no recent journal context)'}\n\n"
        "Generate the next single journaling question.\n\n"
        "Rules:\n"
        "- Ask exactly one question.\n"
        "- Keep the question brief and easy to answer by voice or typing.\n"
        "- Use the user's objective and previous answers.\n"
        "- Use recent journal context only when it is clearly relevant.\n"
        "- If a Play Book is selected, use the current step as the primary guide and do not jump ahead unless clarification is needed.\n"
        "- Do not repeat or closely paraphrase already asked or skipped questions.\n"
        "- If the user is vague, ask a clarifying question.\n"
        "- If the user has already reflected deeply, move toward insight or next action.\n"
        "- Avoid therapy jargon and generic self-help language.\n\n"
        "Return JSON only in this shape:\n"
        "{"
        '"question": "one brief question", '
        '"question_type": "objective | clarification | reflection | emotional_check_in | perspective | action | closing", '
        '"playbook_step": null, '
        '"is_closing_question": false, '
        '"debug_reason": "one short sentence explaining why this question fits"'
        "}"
    )
    question = ai_call(
        system=system,
        user=user_prompt,
        bucket="regular",
        max_tokens=320,
        user_id=user_id,
    )
    if question.startswith("```"):
        question = question.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(question)
    except Exception:
        q = re.sub(r"\s+", " ", question).strip().strip("\"'")
        q = re.sub(r"^\d+[\).]\s*", "", q).strip()
        parsed = {"question": q, "question_type": "reflection", "debug_reason": "Fallback from non-JSON model response."}
    q = re.sub(r"\s+", " ", str(parsed.get("question") or "")).strip().strip("\"'")
    q = re.sub(r"^\d+[\).]\s*", "", q).strip()
    return {
        "question": q[:240] or "What feels most important to explore right now?",
        "question_type": str(parsed.get("question_type") or "reflection")[:64],
        "playbook_step": parsed.get("playbook_step"),
        "is_closing_question": bool(parsed.get("is_closing_question")),
        "debug_reason": str(parsed.get("debug_reason") or "")[:240],
    }


def auto_name_chat(chat_id: int, messages: list, user_id=None) -> None:
    try:
        title = ai_name_chat(messages, user_id=user_id)
        if title:
            topics.rename_chat(chat_id, title[:120])
    except Exception:
        logger.exception("Operation failed")


def ai_parse_task(text: str, now_local: datetime, tz_label: str = "local time", user_id=None) -> dict:
    from datetime import timedelta
    day_name = now_local.strftime("%A")
    date_str = now_local.strftime("%Y-%m-%d")
    time_str = now_local.strftime("%H:%M")
    hour = now_local.hour
    default_date = date_str if hour < 20 else (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
    default_label = "today" if hour < 20 else "tomorrow"

    upcoming = {}
    for i in range(1, 8):
        d = now_local + timedelta(days=i)
        upcoming[d.strftime("%A")] = d.strftime("%Y-%m-%d")

    prompt = (
        f'Parse this task input and return JSON only (no markdown):\n"{text}"\n\n'
        f"Context: Today is {day_name}, {date_str}, {time_str} {tz_label}.\n"
        f"Default due date if none specified: {default_date} ({default_label}).\n"
        f"Upcoming days: {json.dumps(upcoming)}\n\n"
        "Return ONLY this JSON:\n"
        '{"title": "clean action phrase (remove priority markers, phone numbers, URLs, dates)",'
        ' "emoji": "single most fitting emoji for this task",'
        ' "due_at": "YYYY-MM-DD",'
        ' "priority": "high or medium or low",'
        ' "description": "phone numbers, URLs, extra context not in title - empty string if none",'
        ' "recurrence_rule": "natural recurrence phrase like daily, every Monday, every 2 weeks, or null"}\n\n'
        "Rules:\n"
        "- title: clean verb phrase 2-10 words, strip meta info\n"
        "- emoji: best single emoji (📞 calls, 🚗 car, ⚾ sports, 🏥 medical, 🛒 shopping, etc.)\n"
        "- due_at: parse day names using upcoming map, 'today'/'tomorrow', explicit dates; "
        f"  use {default_date} as default\n"
        "- priority: p1/urgent/critical/asap/important=high; p3/low/whenever=low; else medium\n"
        "- recurrence_rule: fill only when the input says repeat, recurring, daily, weekly, monthly, yearly, every weekday, every weekend, every N days/weeks/months, or every named weekday.\n"
        "- description: remaining details, phone numbers, URLs; empty string if nothing"
    )

    result = ai_call(
        system="You are a task intake assistant. Parse natural language task descriptions into structured JSON.",
        user=prompt,
        bucket="lite",
        max_tokens=512,
        user_id=user_id,
    )
    if result.startswith("```"):
        result = result.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    parsed = json.loads(result)
    parsed["title"] = (parsed.get("title") or text[:100]).strip()
    parsed["emoji"] = (parsed.get("emoji") or "").strip() or None
    parsed["priority"] = parsed.get("priority") or "medium"
    parsed["due_at"] = parsed.get("due_at") or None
    parsed["description"] = (parsed.get("description") or "").strip() or None
    parsed["recurrence_rule"] = (parsed.get("recurrence_rule") or "").strip() or None
    return parsed


def parse_recurrence_locally(rule: str, current_dt: datetime,
                             prior_due_date: str | None = None) -> str | None:
    text = (rule or "").strip().lower()
    if not text:
        return None
    base = current_dt.date()
    if prior_due_date:
        try:
            base = datetime.strptime(prior_due_date[:10], "%Y-%m-%d").date()
        except ValueError:
            pass

    def after(days: int) -> str:
        return (base + timedelta(days=max(1, days))).isoformat()

    match = re.search(r"every\s+(\d+)\s+days?", text)
    if match:
        return after(int(match.group(1)))
    match = re.search(r"every\s+(\d+)\s+weeks?", text)
    if match:
        return after(int(match.group(1)) * 7)

    if "daily" in text or "every day" in text or "each day" in text:
        return after(1)
    if "weekly" in text or "every week" in text or "each week" in text:
        return after(7)
    if "biweekly" in text or "every other week" in text:
        return after(14)

    weekdays = {
        "monday": 0, "mon": 0,
        "tuesday": 1, "tue": 1, "tues": 1,
        "wednesday": 2, "wed": 2,
        "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
        "friday": 4, "fri": 4,
        "saturday": 5, "sat": 5,
        "sunday": 6, "sun": 6,
    }
    for name, target in weekdays.items():
        if re.search(rf"\b{name}\b", text):
            days = (target - base.weekday()) % 7
            return after(days or 7)

    if "monthly" in text or "every month" in text:
        import calendar
        year = base.year + (1 if base.month == 12 else 0)
        month = 1 if base.month == 12 else base.month + 1
        day = min(base.day, calendar.monthrange(year, month)[1])
        return f"{year:04d}-{month:02d}-{day:02d}"

    if "yearly" in text or "annually" in text or "every year" in text:
        year = base.year + 1
        day = 28 if base.month == 2 and base.day == 29 else base.day
        return f"{year:04d}-{base.month:02d}-{day:02d}"

    return None


def ai_parse_recurrence(rule: str, timezone_str: str, current_dt_str: str,
                        prior_due_date: str | None = None, user_id=None) -> str | None:
    try:
        current_dt = datetime.strptime(current_dt_str[:16], "%Y-%m-%d %H:%M")
        local = parse_recurrence_locally(rule, current_dt, prior_due_date)
        if local:
            return local
    except Exception:
        logger.exception("Local recurrence parse failed")
    try:
        result = ai_call(
            system="You compute recurring task dates. Return only one YYYY-MM-DD date.",
            user=(
                f"Recurrence rule: {rule}\n"
                f"Timezone: {timezone_str}\n"
                f"Current date/time: {current_dt_str}\n"
                f"Prior due date: {prior_due_date or '(none)'}\n\n"
                "Return the next due date strictly after the current date, in YYYY-MM-DD format."
            ),
            bucket="lite",
            max_tokens=32,
            user_id=user_id,
        ).strip()
        if len(result) == 10 and result[4] == "-" and result[7] == "-":
            return result
    except Exception:
        logger.exception("ai_parse_recurrence failed")
    return None


def fetch_user_ai_usage(user_id: int, start_date: str | None = None,
                        end_date: str | None = None, days: int = 30) -> dict:
    params: dict = {"app": "voice-journal", "user": str(user_id)}
    if start_date:
        params["start"] = start_date
    if end_date:
        params["end"] = end_date
    if not start_date and not end_date:
        params["days"] = str(days)
    try:
        r = req_lib.get(
            f"{GATEWAY_URL}/usage/summary",
            params=params,
            headers={"X-Internal-Token": INTERNAL_TOKEN},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        logger.exception("Failed to fetch AI usage for user %s", user_id)
        return {}


def stream_chat_model_round(
    *,
    user_id: int,
    system_prompt: str,
    messages: list[dict],
    tools_schema: list[dict],
    max_tokens: int = 8192,
):
    """Stream one provider call and normalize output to VoiceJournal's SSE/tool shape."""
    preset = ai_models.get_func_preset("chat", user_id)
    path, payload = ai_models.chat_payload(
        preset=preset,
        system=system_prompt,
        messages=messages,
        tools=tools_schema,
        max_tokens=max_tokens,
    )
    headers = _headers(user_id, anthropic_version=preset.provider == "anthropic")
    if preset.provider != "anthropic":
        headers = {k: v for k, v in headers.items() if not k.lower().startswith("anthropic-")}

    round_text = ""
    buf = ""
    state: dict = {}
    for attempt in range(2):
        try:
            with req_lib.post(
                f"{GATEWAY_URL}{path}",
                json=payload,
                headers=headers,
                stream=True,
                timeout=120,
            ) as r:
                r.raise_for_status()
                for chunk in r.iter_content(chunk_size=None):
                    if not chunk:
                        continue
                    buf += chunk.decode("utf-8", errors="replace")
                    lines = buf.split("\n")
                    buf = lines[-1]
                    for line in lines[:-1]:
                        out, _tool_uses, text_delta = ai_models.parse_stream_line(
                            preset=preset,
                            line=line,
                            state=state,
                        )
                        if text_delta:
                            round_text += text_delta
                        if out:
                            yield {"type": "sse", "data": out}
                break
        except req_lib.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None)
            if status_code == 429 and attempt == 0 and not round_text and not state.get("tool_uses"):
                time.sleep(3)
                continue
            raise

    tool_uses = ai_models.flush_stream_state(preset, state)
    yield {
        "type": "done",
        "round_text": round_text,
        "tool_uses": tool_uses,
        "stop_reason": state.get("stop_reason"),
        "provider": preset.provider,
        "model": preset.model,
    }


def default_chat_agent_instructions() -> str:
    return (
        "You are a thoughtful personal assistant with full access to the user's "
        "journal, tasks, topics, entities, and settings. "
        "You can read, search, create, update, and delete any of these objects using your tools.\n\n"
        "Guidelines:\n"
        "- Always search before modifying - don't guess IDs.\n"
        "- For bulk operations, search first, then use batch tools with the specific IDs you found.\n"
        "- When the user asks you to modify data, call the relevant mutation tool in the same turn after you have the needed IDs. Do not only announce that you will do it.\n"
        "- For applying tags to several tasks or entries, use batch_tag_objects instead of many separate tag_object calls.\n"
        "- For creating or reorganizing several topics/entities, use batch_create_tags, batch_update_tags, or batch_merge_tags instead of repeated single-tag calls.\n"
        "- Confirm with the user before deleting more than 3 items.\n"
        "- When creating tasks or tags, use descriptive titles."
    )


def build_chat_system_prompt(user_id: int, chat_id: int) -> str:
    parts = []
    scope_tags = topics.get_scope_tags(chat_id)
    loaded_tag_ids: set[int] = set()

    def _load_tag_notes(tag_id: int, label: str) -> None:
        if tag_id in loaded_tag_ids:
            return
        loaded_tag_ids.add(tag_id)
        content = topics.get_all_entry_content(tag_id)
        if content:
            parts.append(f"--- Notes: {label} ---\n\n{content}\n\n--- End of Notes ---")

    for tag in scope_tags:
        _load_tag_notes(tag["id"], tag["name"])
        for ancestor in topics.get_ancestors(user_id, tag["id"]):
            _load_tag_notes(ancestor["id"], ancestor["name"])
        queue = list(topics.get_children(tag["id"]))
        while queue:
            child = queue.pop(0)
            _load_tag_notes(child["id"], child["name"])
            queue.extend(topics.get_children(child["id"]))

    journal_ctx = journal.get_context(user_id, n_recent=7)
    if journal_ctx:
        parts.append(f"--- Recent Journal (last 7 days) ---\n\n{journal_ctx}\n\n--- End of Journal ---")
    context_block = "\n\n".join(parts) if parts else "(No context loaded.)"

    n_tasks = len(tasks_db.list_tasks(user_id))
    n_topics = len(topics.list_topics(user_id))
    n_entities = len(topics.list_entities(user_id))

    default_prompt = (
        "You are a thoughtful personal assistant with full access to the user's "
        "journal, tasks, topics, entities, and settings. "
        "You can read, search, create, update, and delete any of these objects using your tools.\n\n"
        "Guidelines:\n"
        "- Always search before modifying — don't guess IDs.\n"
        "- For bulk operations, search first, then use batch tools with the specific IDs you found.\n"
        "- When the user asks you to modify data, call the relevant mutation tool in the same turn after you have the needed IDs. Do not only announce that you will do it.\n"
        "- For applying tags to several tasks or entries, use batch_tag_objects instead of many separate tag_object calls.\n"
        "- For creating or reorganizing several topics/entities, use batch_create_tags, batch_update_tags, or batch_merge_tags instead of repeated single-tag calls.\n"
        "- Confirm with the user before deleting more than 3 items.\n"
        "- When creating tasks or tags, use descriptive titles.\n\n"
    )
    default_prompt = default_chat_agent_instructions()
    custom_prompt = topics.get_setting(user_id, "chat_agent_instructions", "").strip()
    instruction_block = custom_prompt or default_prompt

    return (
        instruction_block.rstrip()
        + "\n\n"
        f"System inventory: {n_tasks} tasks, {n_topics} topics, {n_entities} entities.\n\n"
        + context_block
    )


def _extract_decision_candidates(chunk: str, date_str: str, tag_context: list | None = None,
                                  user_id=None) -> list[dict]:
    """Call LLM to extract decision log candidates from a content chunk."""
    tags_json = json.dumps([
        {"id": t["id"], "name": t["name"], "kind": t["kind"]}
        for t in (tag_context or [])[:120]
    ])
    user_msg = (
        f"Journal entry for {date_str}:\n\n{chunk}\n\n"
        "Identify decisions made, open questions, and items to revisit.\n"
        "Return JSON only (no markdown wrapper):\n"
        '{"candidates": [{'
        '"type": "decision or open_question or revisit_later", '
        '"title": "concise summary (5-12 words)", '
        '"content": "full text or context from the entry", '
        '"rationale": "why decided this way, or what makes it open (if present)", '
        '"status": "decided or open or deferred", '
        '"confidence": "low or medium or high", '
        '"tag_ids": [numeric ids from tag catalog that apply], '
        '"source_quote": "short verbatim quote from entry that supports this item"'
        "}]}\n"
        "Rules:\n"
        "- Be conservative. Only include clear decisions, explicit open questions, "
        "or things the user says they should revisit.\n"
        "- Do not invent decisions. Do not rephrase general discussion as a decision.\n"
        "- A decision must be something that was actually settled (use type=decision, status=decided).\n"
        "- An open question is something explicitly unresolved or asked (type=open_question, status=open).\n"
        "- Revisit later is a deferred tradeoff or maybe-later idea (type=revisit_later, status=deferred).\n"
        "- If nothing qualifies, return {\"candidates\": []}.\n"
        f"- Tag catalog for tag_ids: {tags_json}"
    )
    result_text = _ai_text_call(
        system=(
            "You are a decision log extraction assistant. "
            "Identify settled decisions, open questions, and deferred items from journal entries."
        ),
        user=user_msg,
        bucket="lite",
        max_tokens=2048,
        user_id=user_id,
        timeout=60,
        func_key="decision_extraction",
    )
    if result_text.startswith("```"):
        result_text = result_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(result_text).get("candidates", [])


def extract_decision_log_items(user_id: int, entry_id: str, date_str: str,
                                content: str) -> list[dict]:
    """Extract decision log candidates from a journal entry and store them as suggestions."""
    extraction_items: list[dict] = []
    try:
        chunks = utils.chunk_text(content, max_words=4000)
        entry_tags = topics.get_tags_for_entry(entry_id)
        tag_context = entry_tags or topics.list_all_tags(user_id)
        raw_candidates: list[dict] = []
        for chunk in chunks:
            raw_candidates.extend(
                _extract_decision_candidates(chunk, date_str, tag_context=tag_context, user_id=user_id)
            )

        seen: set[str] = set()
        for candidate in raw_candidates:
            key = utils.normalize_tag_name(candidate.get("title", ""))
            if not key or key in seen:
                continue
            seen.add(key)
            item_type = candidate.get("type", "decision")
            title = candidate.get("title") or ""
            try:
                if decisions_db.find_duplicate_item(user_id, item_type, title):
                    continue
                item_id = decisions_db.create_item(
                    user_id,
                    item_type=item_type,
                    title=title,
                    content=candidate.get("content") or None,
                    rationale=candidate.get("rationale") or None,
                    status=candidate.get("status", "open"),
                    review_status="suggested",
                    source="extraction",
                    source_session_id=entry_id,
                    confidence=candidate.get("confidence") or None,
                    reject_duplicates=True,
                )
            except (decisions_db.DecisionValidationError, decisions_db.DuplicateDecisionError):
                continue
            tag_ids = {int(t) for t in (candidate.get("tag_ids") or []) if str(t).isdigit()}
            if tag_ids:
                decisions_db.tag_decision(user_id, item_id, list(tag_ids), tag_source="intake_inferred")
            extraction_items.append({
                "item_type": item_type,
                "item_id": item_id,
                "name": title,
            })
    except Exception:
        logger.exception("Decision log extraction failed for entry %s user_id=%s", entry_id, user_id)
    return extraction_items


def ai_translate_frequency(frequency_text: str, user_id: int) -> str:
    """Convert a natural language frequency description to a cron expression.
    Returns a 5-field cron string; defaults to '0 0 * * *' on failure."""
    try:
        result = _ai_text_call(
            system="You are a cron expression generator. Convert natural language scheduling descriptions to standard 5-field cron expressions.",
            user=(
                f"Convert this tracking frequency to a 5-field cron expression: {frequency_text!r}\n\n"
                "Return ONLY the cron expression, nothing else. Examples:\n"
                "Daily → 0 0 * * *\n"
                "Every workday → 0 0 * * 1-5\n"
                "Weekly on Monday → 0 0 * * 1\n"
                "Every other Tuesday → 0 0 * * 2/2\n"
                "Monthly on the 1st → 0 0 1 * *"
            ),
            bucket="lite",
            max_tokens=32,
            user_id=user_id,
            timeout=20,
            func_key="tracker_frequency",
        )
        result = result.strip().strip("`").strip()
        parts = result.split()
        if len(parts) == 5:
            return result
    except Exception:
        logger.exception("Failed to translate tracker frequency %r", frequency_text)
    return "0 0 * * *"


def _tracker_interval_context(user_id: int, interval_start: str, interval_end: str) -> str:
    """Fetch journal entries and completed tasks in the given interval."""
    entries = journal.search_entries(user_id, date_from=interval_start, date_to=interval_end, limit=60)
    parts = []
    for entry in reversed(entries):
        body = journal.get_entry(user_id, entry["date"], entry["time"])
        if body:
            parts.append(f"Journal entry {entry['date']} {entry['time']}:\n{body}")
    completed = tasks_db.list_completed_in_range(user_id, interval_start, interval_end)
    if completed:
        task_lines = "\n".join(f"- {t['title']}" for t in completed)
        parts.append(f"Tasks completed in this period:\n{task_lines}")
    return "\n\n---\n\n".join(parts)


def _cron_previous_fire(cron_expression: str, before_date: str) -> str:
    """Compute the most recent cron fire date before before_date.
    Falls back to one day before if croniter is unavailable."""
    from datetime import datetime, timedelta
    ref = datetime.strptime(before_date, "%Y-%m-%d")
    try:
        from croniter import croniter  # type: ignore
        it = croniter(cron_expression, ref)
        prev = it.get_prev(datetime)
        return prev.strftime("%Y-%m-%d")
    except Exception:
        return (ref - timedelta(days=1)).strftime("%Y-%m-%d")


def _dummy_field(user_id: int, fields: list[dict], row: dict,
                 period_start: str, period_end: str) -> list[str]:
    # Kept as a stub so import references don't break; not used in new flow.
    return []
def ai_capture_trackers(user_id: int, as_of_date: str) -> dict:
    """AI capture: for each active tracker due on as_of_date, fetch interval context
    and extract the tracker value in one call per tracker."""
    from datetime import datetime, timedelta
    trackers_due = trackers_db.list_trackers_due(user_id, as_of_date)
    results = []
    for tracker in trackers_due:
        try:
            cron = tracker.get("cron_expression") or "0 0 * * *"
            prev_date = _cron_previous_fire(cron, as_of_date)
            context = _tracker_interval_context(user_id, prev_date, as_of_date)
            tracker_type = tracker.get("type", "yes_no")
            instructions = tracker.get("capture_instructions") or ""
            if tracker_type == "yes_no":
                value_desc = 'true or false'
            elif tracker_type == "number":
                num_hint = ""
                if tracker.get("number_min") is not None and tracker.get("number_max") is not None:
                    num_hint = f" (expected range: {tracker['number_min']}–{tracker['number_max']})"
                elif tracker.get("number_min") is not None:
                    num_hint = f" (minimum: {tracker['number_min']})"
                elif tracker.get("number_max") is not None:
                    num_hint = f" (maximum: {tracker['number_max']})"
                value_desc = f'a numeric value (integer or float){num_hint}'
            else:
                value_desc = 'a short text string'
            user_msg = (
                f"Tracker: {tracker['name']}\n"
                f"Type: {tracker_type} — value must be {value_desc}\n"
                f"Period: {prev_date} through {as_of_date}\n"
                + (f"Capture instructions: {instructions}\n" if instructions else "")
                + f"\nContext from this period:\n{context[:7000]}\n\n"
                "Based on the above context, extract the tracker value for this period. "
                "It is perfectly fine to return null if the context does not contain enough information to justify a value — do not guess. "
                "When you do set a value, include 1–2 sentences explaining your reasoning, using direct quotes from the context to validate your choice. "
                "Return JSON only (no markdown):\n"
                '{"value": <extracted value or null if insufficient evidence>, '
                '"confidence": "low|medium|high", '
                '"explanation": "1-2 sentence explanation with direct quotes, or null if value is null"}'
            )
            result_text = _ai_text_call(
                system=(
                    "You are a personal tracker assistant. Extract structured values from "
                    "journal entries and task completion data. "
                    "You MUST return null for value if there is not enough evidence in the context — passing is always the right choice over guessing."
                ),
                user=user_msg,
                bucket="regular",
                max_tokens=300,
                user_id=user_id,
                timeout=60,
                func_key="tracker_capture",
            )
            if result_text.startswith("```"):
                result_text = result_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            parsed = json.loads(result_text)
            value = parsed.get("value")
            explanation = parsed.get("explanation") or None
            if value is None:
                # Create a pending entry (null value) for manual fill
                trackers_db.upsert_entry(user_id, tracker["id"], as_of_date, None,
                                         source="ai_captured")
                results.append({"tracker_id": tracker["id"], "captured": False})
            else:
                value_json = json.dumps(value)
                trackers_db.upsert_entry(user_id, tracker["id"], as_of_date, value_json,
                                         source="ai_captured", ai_explanation=explanation)
                results.append({"tracker_id": tracker["id"], "captured": True, "value": value})
        except Exception:
            logger.exception("Tracker capture failed for tracker %s", tracker["id"])
            try:
                trackers_db.upsert_entry(user_id, tracker["id"], as_of_date, None,
                                         source="ai_captured")
            except Exception:
                pass
            results.append({"tracker_id": tracker["id"], "captured": False, "error": True})
    return {"processed": len(results), "results": results}


def ai_tracker_commentary(user_id: int, tracker_id: str) -> str | None:
    """Generate AI commentary for a tracker. Returns None if no instructions configured."""
    tracker = trackers_db.get_tracker(user_id, tracker_id)
    if not tracker or not tracker.get("ai_commentary_instructions"):
        return None
    entries = trackers_db.list_entries(user_id, tracker_id, limit=30)
    if not entries:
        return None
    lines = []
    for e in reversed(entries):
        val = e.get("value_json")
        if val is not None:
            try:
                val = json.loads(val)
            except Exception:
                pass
        lines.append(f"{e['entry_date']}: {val if val is not None else '(missing)'}")
    data_text = "\n".join(lines)
    latest_date = entries[0]["entry_date"] if entries else ""
    instructions = tracker["ai_commentary_instructions"]
    result = _ai_text_call(
        system="You are a personal analytics assistant writing brief, insightful commentary on habit tracker data.",
        user=(
            f"Tracker: {tracker['name']} ({tracker['type']})\n"
            f"Instructions: {instructions}\n\n"
            f"Data (oldest first):\n{data_text}\n\n"
            "Write 1–2 sentences of commentary. Be specific, personal, and grounded in the data."
        ),
        bucket="regular",
        max_tokens=256,
        user_id=user_id,
        timeout=45,
        func_key="tracker_commentary",
    )
    trackers_db.upsert_commentary(user_id, tracker_id, result.strip(), latest_date)
    return result.strip()


def run_tracker_cron(user_id: int, as_of_date: str) -> dict:
    """Cron entry point: AI-capture all due trackers for a user."""
    return ai_capture_trackers(user_id, as_of_date)
