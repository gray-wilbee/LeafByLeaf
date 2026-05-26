from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

import requests as req_lib

import topics


REGULAR_SETTING = "ai_model_regular"
LITE_SETTING = "ai_model_lite"


@dataclass(frozen=True)
class ModelPreset:
    key: str
    label: str
    provider: str
    model: str
    endpoint: str


REGULAR_PRESETS = [
    ModelPreset("anthropic_sonnet_46", "Anthropic Claude Sonnet 4.6", "anthropic", "claude-sonnet-4-6", "messages"),
    ModelPreset("openai_gpt_54_mini", "OpenAI GPT-5.4 mini", "openai", "gpt-5.4-mini", "chat_completions"),
    ModelPreset("gemini_3_flash", "Gemini 3 Flash", "gemini", "gemini-3-flash-preview", "generate_content"),
    ModelPreset("gemini_3_pro", "Gemini 3 Pro", "gemini", "gemini-3-pro-preview", "generate_content"),
]

LITE_PRESETS = [
    ModelPreset("anthropic_haiku_45", "Anthropic Claude Haiku 4.5", "anthropic", "claude-haiku-4-5-20251001", "messages"),
    ModelPreset("openai_gpt_54_mini", "OpenAI GPT-5.4 mini", "openai", "gpt-5.4-mini", "chat_completions"),
    ModelPreset("openai_gpt_54_nano", "OpenAI GPT-5.4 nano", "openai", "gpt-5.4-nano", "chat_completions"),
    ModelPreset("gemini_31_flash_lite", "Gemini 3.1 Flash-Lite", "gemini", "gemini-3.1-flash-lite-preview", "generate_content"),
    ModelPreset("gemini_3_flash", "Gemini 3 Flash", "gemini", "gemini-3-flash-preview", "generate_content"),
]

PRESETS_BY_BUCKET = {
    "regular": REGULAR_PRESETS,
    "lite": LITE_PRESETS,
}

DEFAULT_PRESET_KEY = {
    "regular": REGULAR_PRESETS[0].key,
    "lite": LITE_PRESETS[0].key,
}

SETTING_BY_BUCKET = {
    "regular": REGULAR_SETTING,
    "lite": LITE_SETTING,
}


def preset_options() -> dict[str, list[dict[str, str]]]:
    return {
        bucket: [
            {"key": p.key, "label": p.label, "provider": p.provider, "model": p.model}
            for p in presets
        ]
        for bucket, presets in PRESETS_BY_BUCKET.items()
    }


def get_preset(bucket: str, user_id: int | None = None, preset_key: str | None = None) -> ModelPreset:
    if bucket not in PRESETS_BY_BUCKET:
        raise ValueError(f"Unknown AI model bucket: {bucket}")
    key = preset_key
    if key is None and user_id is not None:
        key = topics.get_setting(user_id, SETTING_BY_BUCKET[bucket], DEFAULT_PRESET_KEY[bucket])
    key = key or DEFAULT_PRESET_KEY[bucket]
    for preset in PRESETS_BY_BUCKET[bucket]:
        if preset.key == key:
            return preset
    if preset_key is not None:
        raise ValueError(f"Unknown {bucket} AI model preset: {preset_key}")
    return PRESETS_BY_BUCKET[bucket][0]


def get_user_model_settings(user_id: int) -> dict[str, str]:
    return {
        "regular": get_preset("regular", user_id).key,
        "lite": get_preset("lite", user_id).key,
    }


def set_user_model_settings(user_id: int, regular: str | None = None, lite: str | None = None) -> None:
    if regular is not None:
        get_preset("regular", preset_key=regular)
        topics.set_setting(user_id, REGULAR_SETTING, regular)
    if lite is not None:
        get_preset("lite", preset_key=lite)
        topics.set_setting(user_id, LITE_SETTING, lite)


REASONING_LEVELS = [
    ("", "— No override —"),
    ("provider_default", "Provider default"),
    ("off", "Off"),
    ("minimal", "Minimal"),
    ("light", "Light"),
    ("balanced", "Balanced"),
    ("deep", "Deep"),
    ("auto", "Auto"),
]

AI_FUNCTIONS: dict[str, dict[str, str]] = {
    "transcript_format":  {"label": "Transcript formatting",         "bucket": "lite"},
    "topic_extraction":   {"label": "Topic & entity extraction",      "bucket": "regular"},
    "task_extraction":    {"label": "Task extraction",                "bucket": "lite"},
    "task_dedup":         {"label": "Task duplicate detection",       "bucket": "lite"},
    "chat":               {"label": "Chat",                           "bucket": "regular"},
    "entry_title":        {"label": "Entry title generation",         "bucket": "lite"},
    "topic_summary":      {"label": "Topic summary & compact",        "bucket": "regular"},
    "topic_description":  {"label": "Topic description",              "bucket": "lite"},
    "intake_summary":     {"label": "Intake summary",                 "bucket": "lite"},
    "decision_extraction": {"label": "Decision Log extraction",        "bucket": "lite"},
    "tracker_capture":    {"label": "Tracker capture",                 "bucket": "regular"},
    "tracker_commentary": {"label": "Tracker AI commentary",           "bucket": "regular"},
    "tracker_frequency":  {"label": "Tracker frequency → cron",       "bucket": "lite"},
}


def get_func_preset(func_key: str, user_id: int) -> ModelPreset:
    """Return model preset for a specific AI function.
    Checks function-level override first, then falls back to bucket default."""
    if func_key not in AI_FUNCTIONS:
        raise ValueError(f"Unknown AI function: {func_key}")
    bucket = AI_FUNCTIONS[func_key]["bucket"]
    saved = topics.get_setting(user_id, f"ai_func_{func_key}_model", "")
    if saved:
        try:
            return get_preset(bucket, preset_key=saved)
        except ValueError:
            pass  # Unknown preset key — fall back to bucket default
    return get_preset(bucket, user_id=user_id)


def get_func_reasoning(func_key: str, user_id: int) -> str | None:
    """Return the reasoning level override for a function, or None if not set."""
    val = topics.get_setting(user_id, f"ai_func_{func_key}_reasoning", "")
    return val or None


def get_user_func_settings(user_id: int) -> dict[str, dict[str, str]]:
    """Return all per-function model/reasoning settings for the admin UI."""
    result = {}
    for key, meta in AI_FUNCTIONS.items():
        model_val = topics.get_setting(user_id, f"ai_func_{key}_model", "")
        reasoning_val = topics.get_setting(user_id, f"ai_func_{key}_reasoning", "")
        result[key] = {
            "label": meta["label"],
            "bucket": meta["bucket"],
            "model": model_val,
            "reasoning": reasoning_val,
        }
    return result


def set_func_settings(user_id: int, functions: dict[str, dict[str, str]]) -> None:
    """Save per-function model and reasoning overrides.
    Pass empty string values to clear overrides (reverts to bucket default)."""
    for func_key, settings in functions.items():
        if func_key not in AI_FUNCTIONS:
            continue
        bucket = AI_FUNCTIONS[func_key]["bucket"]
        model_val = (settings.get("model") or "").strip()
        reasoning_val = (settings.get("reasoning") or "").strip()
        if model_val:
            get_preset(bucket, preset_key=model_val)  # validate
        topics.set_setting(user_id, f"ai_func_{func_key}_model", model_val)
        topics.set_setting(user_id, f"ai_func_{func_key}_reasoning", reasoning_val)


def response_text(provider: str, data: dict[str, Any]) -> str:
    if provider == "anthropic":
        return "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        ).strip()
    if provider == "openai":
        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        content = msg.get("content") or ""
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
        return str(content).strip()
    if provider == "gemini":
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        return "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
    return ""


def text_request(
    *,
    gateway_url: str,
    headers: dict[str, str],
    preset: ModelPreset,
    system: str,
    user: str,
    max_tokens: int,
    stream: bool = False,
    thinking: bool = False,
    timeout: int = 120,
) -> req_lib.Response:
    if preset.provider == "anthropic":
        body: dict[str, Any] = {
            "model": preset.model,
            "max_tokens": max_tokens,
            "stream": stream,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if thinking:
            body["thinking"] = {"type": "enabled", "budget_tokens": min(5000, max(1024, max_tokens // 2))}
        return req_lib.post(
            f"{gateway_url}/proxy/anthropic/v1/messages",
            json=body,
            headers=headers,
            timeout=timeout,
        )

    if preset.provider == "openai":
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        return req_lib.post(
            f"{gateway_url}/proxy/openai/v1/chat/completions",
            json={
                "model": preset.model,
                "stream": stream,
                "messages": messages,
                "max_completion_tokens": max_tokens,
                "prompt_cache_key": _cache_key(preset, system),
            },
            headers={k: v for k, v in headers.items() if not k.lower().startswith("anthropic-")},
            timeout=timeout,
        )

    if preset.provider == "gemini":
        body = {
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "thinkingConfig": {"thinkingLevel": _gemini_text_thinking_level(preset, max_tokens)},
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return req_lib.post(
            f"{gateway_url}/proxy/gemini/v1beta/models/{preset.model}:generateContent",
            json=body,
            headers={k: v for k, v in headers.items() if not k.lower().startswith("anthropic-")},
            timeout=timeout,
        )

    raise ValueError(f"Unsupported provider: {preset.provider}")


def claude_tool_schema(tool_def: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": tool_def["name"],
        "description": tool_def.get("description", ""),
        "input_schema": tool_def.get("input_schema") or tool_def.get("parameters") or {"type": "object"},
    }


def openai_tool_schema(tool_def: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool_def["name"],
            "description": tool_def.get("description", ""),
            "parameters": tool_def.get("input_schema") or tool_def.get("parameters") or {"type": "object"},
        },
    }


def gemini_tool_schema(tool_def: dict[str, Any]) -> dict[str, Any]:
    schema = copy.deepcopy(tool_def.get("input_schema") or tool_def.get("parameters") or {"type": "object"})
    _strip_gemini_unsupported_schema_keys(schema)
    return {
        "name": tool_def["name"],
        "description": tool_def.get("description", ""),
        "parameters": schema,
    }


def chat_payload(
    *,
    preset: ModelPreset,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    if preset.provider == "anthropic":
        return (
            "/proxy/anthropic/v1/messages",
            {
                "model": preset.model,
                "max_tokens": max_tokens,
                "stream": True,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": messages,
                "tools": [claude_tool_schema(t) for t in tools],
            },
        )
    if preset.provider == "openai":
        body = {
            "model": preset.model,
            "stream": True,
            "messages": _openai_messages(system, messages),
            "tools": [openai_tool_schema(t) for t in tools],
            "tool_choice": "auto",
            "max_completion_tokens": max_tokens,
            "prompt_cache_key": _cache_key(preset, system),
        }
        return "/proxy/openai/v1/chat/completions", body
    if preset.provider == "gemini":
        body = {
            "contents": _gemini_contents(messages),
            "systemInstruction": {"parts": [{"text": system}]},
            "tools": [{"functionDeclarations": [gemini_tool_schema(t) for t in tools]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "thinkingConfig": {"thinkingLevel": _gemini_chat_thinking_level(preset)},
            },
        }
        return f"/proxy/gemini/v1beta/models/{preset.model}:streamGenerateContent?alt=sse", body
    raise ValueError(f"Unsupported provider: {preset.provider}")


def parse_stream_line(
    *,
    preset: ModelPreset,
    line: str,
    state: dict[str, Any],
) -> tuple[bytes | None, list[dict[str, Any]], str | None]:
    if preset.provider == "anthropic":
        return _parse_anthropic_stream_line(line, state)
    if preset.provider == "openai":
        return _parse_openai_stream_line(line, state)
    if preset.provider == "gemini":
        return _parse_gemini_stream_line(line, state)
    return None, [], None


def flush_stream_state(preset: ModelPreset, state: dict[str, Any]) -> list[dict[str, Any]]:
    if preset.provider == "openai":
        calls = []
        for item in sorted((state.get("tool_calls") or {}).values(), key=lambda v: v["index"]):
            calls.append({
                "id": item.get("id") or f"toolu_openai_{item['index']}",
                "name": item.get("name") or "",
                "input_json_str": item.get("arguments") or "{}",
            })
        return [c for c in calls if c["name"]]
    if preset.provider == "gemini":
        return state.get("tool_calls") or []
    return state.get("tool_uses") or []


def _cache_key(preset: ModelPreset, system: str) -> str:
    digest = hashlib.sha256(system[:4000].encode("utf-8")).hexdigest()[:16]
    return f"voicejournal:{preset.provider}:{preset.model}:{digest}"


def _gemini_chat_thinking_level(preset: ModelPreset) -> str:
    if "pro" in preset.model:
        return "low"
    return "medium"


def _gemini_text_thinking_level(preset: ModelPreset, max_tokens: int) -> str:
    if "pro" in preset.model:
        return "low"
    if "lite" in preset.model or max_tokens <= 1024:
        return "low"
    return "medium"


def _openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for msg in messages:
        role = msg["role"]
        content = msg.get("content", "")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            tool_calls = []
            text_parts = []
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    })
            msg_out = {"role": "assistant", "content": "".join(text_parts) or None}
            if tool_calls:
                msg_out["tool_calls"] = tool_calls
            out.append(msg_out)
        elif role == "user":
            for block in content:
                if block.get("type") == "tool_result":
                    out.append({
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": block.get("content", ""),
                    })
                elif block.get("type") == "text":
                    out.append({"role": "user", "content": block.get("text", "")})
    return out


def _gemini_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    last_tool_names: dict[str, str] = {}
    for msg in messages:
        role = "model" if msg["role"] == "assistant" else "user"
        content = msg.get("content", "")
        if isinstance(content, str):
            out.append({"role": role, "parts": [{"text": content}]})
            continue
        parts = []
        for block in content:
            if block.get("type") == "text":
                parts.append({"text": block.get("text", "")})
            elif block.get("type") == "tool_use":
                last_tool_names[block["id"]] = block["name"]
                fn = {"name": block["name"], "args": block.get("input") or {}}
                provider_meta = block.get("provider_metadata") or {}
                if provider_meta.get("id"):
                    fn["id"] = provider_meta["id"]
                part = {"functionCall": fn}
                if provider_meta.get("thoughtSignature"):
                    part["thoughtSignature"] = provider_meta["thoughtSignature"]
                parts.append(part)
            elif block.get("type") == "tool_result":
                name = last_tool_names.get(block.get("tool_use_id"), "tool_result")
                try:
                    response = json.loads(block.get("content") or "{}")
                except Exception:
                    response = {"result": block.get("content", "")}
                parts.append({"functionResponse": {"name": name, "response": response}})
        out.append({"role": role, "parts": parts or [{"text": ""}]})
    return out


def _parse_anthropic_stream_line(line: str, state: dict[str, Any]) -> tuple[bytes | None, list[dict[str, Any]], str | None]:
    if not line.startswith("data: "):
        return None, [], None
    raw = line[6:].strip()
    if not raw or raw == "[DONE]":
        return None, [], None
    try:
        ev = json.loads(raw)
    except Exception:
        return None, [], None
    ev_type = ev.get("type", "")
    if ev_type == "content_block_start":
        cb = ev.get("content_block", {})
        if cb.get("type") == "tool_use":
            state["current_tool"] = {"id": cb["id"], "name": cb["name"], "input_json_str": ""}
        return None, [], None
    if ev_type == "content_block_delta":
        delta = ev.get("delta", {})
        if delta.get("type") == "input_json_delta" and state.get("current_tool"):
            state["current_tool"]["input_json_str"] += delta.get("partial_json", "")
            return None, [], None
        if delta.get("type") == "text_delta":
            return f"data: {raw}\n\n".encode("utf-8"), [], delta.get("text", "")
    if ev_type == "content_block_stop" and state.get("current_tool"):
        state.setdefault("tool_uses", []).append(state["current_tool"])
        state["current_tool"] = None
    if ev_type == "message_delta":
        sr = ev.get("delta", {}).get("stop_reason")
        if sr:
            state["stop_reason"] = sr
    return None, [], None


def _parse_openai_stream_line(line: str, state: dict[str, Any]) -> tuple[bytes | None, list[dict[str, Any]], str | None]:
    if not line.startswith("data: "):
        return None, [], None
    raw = line[6:].strip()
    if not raw or raw == "[DONE]":
        return None, [], None
    try:
        ev = json.loads(raw)
    except Exception:
        return None, [], None
    choice = (ev.get("choices") or [{}])[0]
    finish_reason = choice.get("finish_reason")
    if finish_reason == "tool_calls":
        state["stop_reason"] = "tool_use"
    elif finish_reason == "length":
        state["stop_reason"] = "max_tokens"
    elif finish_reason:
        state["stop_reason"] = finish_reason
    delta = choice.get("delta") or {}
    text = delta.get("content")
    if text:
        claude_event = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
        return f"data: {json.dumps(claude_event)}\n\n".encode("utf-8"), [], text
    for tc in delta.get("tool_calls") or []:
        idx = tc.get("index", 0)
        item = state.setdefault("tool_calls", {}).setdefault(idx, {"index": idx, "arguments": ""})
        if tc.get("id"):
            item["id"] = tc["id"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            item["name"] = fn["name"]
        if fn.get("arguments"):
            item["arguments"] += fn["arguments"]
    return None, [], None


def _parse_gemini_stream_line(line: str, state: dict[str, Any]) -> tuple[bytes | None, list[dict[str, Any]], str | None]:
    if not line.startswith("data: "):
        return None, [], None
    raw = line[6:].strip()
    if not raw:
        return None, [], None
    try:
        ev = json.loads(raw)
    except Exception:
        return None, [], None
    choice = (ev.get("candidates") or [{}])[0]
    finish_reason = choice.get("finishReason")
    if finish_reason == "MAX_TOKENS" and not state.get("tool_calls"):
        state["stop_reason"] = "max_tokens"
    elif finish_reason == "STOP" and not state.get("tool_calls"):
        state["stop_reason"] = "stop"
    elif finish_reason and not state.get("tool_calls"):
        state["stop_reason"] = finish_reason.lower()
    parts = ((choice.get("content") or {}).get("parts") or [])
    for part in parts:
        text = part.get("text")
        if text:
            claude_event = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
            return f"data: {json.dumps(claude_event)}\n\n".encode("utf-8"), [], text
        fn = part.get("functionCall")
        if fn:
            state["stop_reason"] = "tool_use"
            idx = len(state.setdefault("tool_calls", []))
            state["tool_calls"].append({
                "id": fn.get("id") or f"toolu_gemini_{idx}",
                "name": fn.get("name", ""),
                "input_json_str": json.dumps(fn.get("args") or {}),
                "provider_metadata": {
                    k: v for k, v in {
                        "thoughtSignature": part.get("thoughtSignature") or fn.get("thoughtSignature"),
                        "id": fn.get("id"),
                    }.items() if v
                },
            })
    return None, [], None


def _strip_gemini_unsupported_schema_keys(schema: Any) -> None:
    if isinstance(schema, dict):
        for key in ("default", "$schema", "additionalProperties"):
            schema.pop(key, None)
        if "type" in schema and isinstance(schema["type"], list):
            raw_types = [str(t).lower() for t in schema["type"]]
            type_values = [t for t in raw_types if t != "null"]
            schema["type"] = (type_values[0] if type_values else "string").upper()
            if "null" in raw_types:
                schema["nullable"] = True
        elif "type" in schema and isinstance(schema["type"], str):
            schema["type"] = schema["type"].upper()
        for value in schema.values():
            _strip_gemini_unsupported_schema_keys(value)
    elif isinstance(schema, list):
        for item in schema:
            _strip_gemini_unsupported_schema_keys(item)
