from __future__ import annotations

import json
import hashlib
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db
import agent_tools
import ai_models
import decisions as decisions_db
import journal
import guided_playbooks
import llm_service
import tasks as tasks_db
import topics
import trackers as trackers_db
import users


class TempVoiceJournalDB(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.temp_db = os.path.join(self.tmp, "app.db")
        self.journal_dir = os.path.join(self.tmp, "journal")
        self.original_get_db = db.get_db
        self.original_journal_dir = journal.JOURNAL_DIR

        def temp_get_db(path=None):
            return self.original_get_db(self.temp_db)

        db.get_db = temp_get_db
        journal.JOURNAL_DIR = self.journal_dir
        users.init_db()
        topics.init_db()
        tasks_db.init_db()
        journal.init_entries_db()
        guided_playbooks.init_db()
        decisions_db.init_db()
        trackers_db.init_db()

    def tearDown(self):
        db.get_db = self.original_get_db
        journal.JOURNAL_DIR = self.original_journal_dir
        self.stack.close()


class ChatGPTMCPConnectorTests(TempVoiceJournalDB):
    def _client(self):
        import app as voice_app

        voice_app.app.config["TESTING"] = True
        return voice_app.app.test_client()

    def _approved_user(self) -> int:
        user_id = users.create_user("mcp-user", "password")
        users.approve_user(user_id, user_id)
        return user_id

    def _oauth_token(self, user_id: int, scope: str) -> str:
        raw = f"token-{user_id}-{scope}"
        with db.get_db() as conn:
            conn.execute(
                """INSERT INTO oauth_tokens
                   (token, client_id, user_id, scope, resource, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    hashlib.sha256(raw.encode()).hexdigest(),
                    "test-client",
                    user_id,
                    scope,
                    "https://journal.wilbeevibes.com/mcp",
                    "2026-05-15T00:00:00+00:00",
                    "2999-01-01T00:00:00+00:00",
                ),
            )
        return raw

    def _rpc(self, client, token: str, method: str, params: dict | None = None, rpc_id: int = 1):
        return client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}},
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_oauth_metadata_advertises_chatgpt_connector_scopes(self):
        client = self._client()

        auth_meta = client.get("/.well-known/oauth-authorization-server").get_json()
        resource_meta = client.get("/.well-known/oauth-protected-resource").get_json()

        self.assertEqual(auth_meta["issuer"], "https://journal.wilbeevibes.com")
        self.assertEqual(auth_meta["registration_endpoint"], "https://journal.wilbeevibes.com/oauth/register")
        self.assertIn("S256", auth_meta["code_challenge_methods_supported"])
        self.assertIn("journal.read", auth_meta["scopes_supported"])
        self.assertIn("settings.write", auth_meta["scopes_supported"])
        self.assertEqual(resource_meta["resource"], "https://journal.wilbeevibes.com/mcp")
        self.assertEqual(resource_meta["authorization_servers"], ["https://journal.wilbeevibes.com"])
        self.assertIn("tasks.write", resource_meta["scopes_supported"])

    def test_dynamic_registration_allows_chatgpt_and_localhost_redirects(self):
        client = self._client()

        chatgpt = client.post("/oauth/register", json={"redirect_uris": ["https://chatgpt.com/aip/callback"]})
        local = client.post("/oauth/register", json={"redirect_uris": ["http://localhost:8765/callback"]})
        rejected = client.post("/oauth/register", json={"redirect_uris": ["https://example.com/callback"]})

        self.assertEqual(chatgpt.status_code, 201)
        self.assertEqual(local.status_code, 201)
        self.assertEqual(rejected.status_code, 400)

    def test_mcp_requires_auth_with_protected_resource_metadata(self):
        response = self._client().post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})

        self.assertEqual(response.status_code, 401)
        self.assertIn("resource_metadata=", response.headers["WWW-Authenticate"])
        self.assertIn("journal.read", response.headers["WWW-Authenticate"])

    def test_tools_list_includes_apps_sdk_security_and_annotations(self):
        user_id = self._approved_user()
        token = self._oauth_token(user_id, "journal.read tasks.read tasks.write tags.read tags.write settings.read settings.write")

        data = self._rpc(self._client(), token, "tools/list").get_json()
        tools = {t["name"]: t for t in data["result"]["tools"]}

        self.assertIn("search_entries", tools)
        self.assertEqual(tools["search_entries"]["securitySchemes"][0]["scopes"], ["journal.read"])
        self.assertEqual(tools["search_entries"]["_meta"]["securitySchemes"], tools["search_entries"]["securitySchemes"])
        self.assertTrue(tools["search_entries"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["search_entries"]["annotations"]["destructiveHint"])
        self.assertEqual(tools["create_task"]["securitySchemes"][0]["scopes"], ["tasks.write"])
        self.assertFalse(tools["create_task"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["delete_task"]["annotations"]["destructiveHint"])

    def test_read_tool_call_returns_content_and_structured_content(self):
        user_id = self._approved_user()
        journal.append_entry(user_id, "2026-05-15", "09:00:00", "ChatGPT connector planning notes.")
        token = self._oauth_token(user_id, "journal.read")

        data = self._rpc(
            self._client(),
            token,
            "tools/call",
            {"name": "search_entries", "arguments": {"query": "connector"}},
        ).get_json()

        result = data["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["count"], 1)
        self.assertIn("returned 1 entries", result["content"][0]["text"])

    def test_mutation_tool_call_requires_write_scope(self):
        user_id = self._approved_user()
        client = self._client()
        read_only_token = self._oauth_token(user_id, "tasks.read")

        denied = self._rpc(
            client,
            read_only_token,
            "tools/call",
            {"name": "create_task", "arguments": {"title": "Ship ChatGPT connector"}},
        ).get_json()

        self.assertEqual(denied["error"]["code"], -32001)
        self.assertIn("tasks.write", denied["error"]["message"])

        write_token = self._oauth_token(user_id, "tasks.write")
        allowed = self._rpc(
            client,
            write_token,
            "tools/call",
            {"name": "create_task", "arguments": {"title": "Ship ChatGPT connector"}},
        ).get_json()

        self.assertFalse(allowed["result"]["isError"])
        self.assertEqual(allowed["result"]["structuredContent"]["title"], "Ship ChatGPT connector")

    def test_unknown_tool_returns_json_rpc_invalid_params(self):
        user_id = self._approved_user()
        token = self._oauth_token(user_id, "journal.read")

        data = self._rpc(self._client(), token, "tools/call", {"name": "missing_tool", "arguments": {}}).get_json()

        self.assertEqual(data["error"]["code"], -32602)


class IntakeBehaviorTests(TempVoiceJournalDB):
    def test_extract_tasks_uses_only_task_specific_tag_ids(self):
        user_id = 1
        entry_id = journal.append_entry(user_id, "2026-05-01", "08:35:00", "Call Regina about the prototype.")
        work_id = topics.create_topic(user_id, "Work Life Balance")
        prototype_id = topics.create_topic(user_id, "Prototype App")
        topics.tag_entry(user_id, entry_id, [work_id, prototype_id])

        with patch.object(llm_service, "extract_task_candidates", return_value=[
            {
                "title": "Call Regina",
                "emoji": "☎",
                "priority": "medium",
                "due_at": None,
                "description": "Follow up from journal entry",
                "tag_ids": [prototype_id],
            }
        ]):
            items = llm_service.extract_tasks(user_id, entry_id, "2026-05-01", "Call Regina about the prototype.")

        self.assertEqual(len(items), 1)
        task_id = items[0]["item_id"]
        task_tags = topics.get_tags_for_task(task_id)
        self.assertEqual([t["id"] for t in task_tags], [prototype_id])

    def test_extract_topics_creates_only_explicit_strong_relationship_links(self):
        user_id = 1
        entry_id = journal.append_entry(user_id, "2026-05-01", "08:35:00", "Prototype work with AI workflows.")
        candidates = [
            {
                "name": "AI Development Workflows",
                "kind": "topic",
                "description": "Workflow notes",
                "content": "Workflow context",
                "key_sentences": [],
                "related_names": ["Prototype App"],
            },
            {
                "name": "Prototype App",
                "kind": "topic",
                "description": "Prototype notes",
                "content": "Prototype context",
                "key_sentences": [],
            },
            {
                "name": "Passing Mention",
                "kind": "topic",
                "description": "Mentioned but unrelated",
                "content": "Mention context",
                "key_sentences": [],
            },
        ]

        with patch.object(llm_service.utils, "chunk_text", return_value=["chunk"]), \
             patch.object(llm_service, "extract_candidates_resilient", return_value=candidates), \
             patch.object(llm_service, "get_embedding", return_value=None), \
             patch.object(llm_service, "update_tag_metadata"):
            items = llm_service.extract_topics(user_id, entry_id, "2026-05-01", "chunk")

        self.assertEqual(len(items), 3)
        tags = {t["name"]: t["id"] for t in topics.list_topics(user_id)}
        links = topics.get_tag_links(tags["AI Development Workflows"])
        self.assertEqual({l["linked_id"] for l in links}, {tags["Prototype App"]})
        self.assertEqual(topics.get_tag_links(tags["Passing Mention"]), [])

    def test_task_filter_expands_selected_topic_to_descendants(self):
        user_id = 1
        parent_id = topics.create_topic(user_id, "Parent")
        child_id = topics.create_topic(user_id, "Child", parent_tag_id=parent_id)
        task_id = tasks_db.create_task(user_id, "Review child task")
        topics.tag_task(user_id, task_id, [child_id])

        rows = tasks_db.list_tasks_sorted(user_id, show_done=True, show_cancelled=True, tag_ids=[parent_id])
        self.assertEqual([r["id"] for r in rows], [task_id])

    def test_extract_tasks_skips_existing_duplicate(self):
        user_id = 1
        entry_id = journal.append_entry(user_id, "2026-05-01", "08:35:00", "Call Regina about the prototype.")
        existing_id = tasks_db.create_task(user_id, "Call Regina")

        with patch.object(llm_service, "extract_task_candidates", return_value=[
            {
                "title": "Call Regina",
                "emoji": "☎",
                "priority": "medium",
                "due_at": None,
                "description": "Follow up from journal entry",
                "tag_ids": [],
            }
        ]):
            items = llm_service.extract_tasks(user_id, entry_id, "2026-05-01", "Call Regina about the prototype.")

        self.assertEqual(items, [{
            "item_type": "task_duplicate",
            "item_id": existing_id,
            "name": "Skipped duplicate: Call Regina",
        }])
        self.assertEqual(len(tasks_db.list_tasks(user_id)), 1)

    def test_local_recurrence_parser_handles_daily_without_ai(self):
        current = datetime(2026, 5, 4, 9, 0)
        self.assertEqual(
            llm_service.parse_recurrence_locally("daily", current, "2026-05-04"),
            "2026-05-05",
        )

    def test_quick_task_parser_preserves_recurrence_rule(self):
        with patch.object(llm_service, "ai_call", return_value=json.dumps({
            "title": "Pray the Rosary",
            "emoji": "🙏",
            "due_at": "2026-05-04",
            "priority": "medium",
            "description": "",
            "recurrence_rule": "daily",
        })):
            parsed = llm_service.ai_parse_task(
                "Pray the Rosary daily",
                datetime(2026, 5, 4, 9, 0),
                "America/Chicago",
                user_id=1,
            )

        self.assertEqual(parsed["recurrence_rule"], "daily")


class GuidedPlaybookTests(TempVoiceJournalDB):
    def test_custom_playbook_crud_and_builtin_protection(self):
        builtins = guided_playbooks.list_playbooks(1)
        self.assertTrue(any(pb["id"] == "builtin:clear_my_head" for pb in builtins))

        custom = guided_playbooks.save_playbook(1, {
            "title": "My Reset",
            "description": "Reset after work.",
            "target_question_count": 2,
            "steps": [
                {"name": "Name it", "purpose": "Find the issue", "examples": ["What is bothering you?"]},
                {"name": "Move", "purpose": "Find action", "examples": ["What would help?"]},
            ],
        })

        self.assertFalse(custom["is_builtin"])
        self.assertEqual(custom["steps"][0]["examples"], ["What is bothering you?"])

        updated = guided_playbooks.save_playbook(1, {
            "title": "My Better Reset",
            "target_question_count": 1,
            "steps": [{"name": "One", "purpose": "One purpose", "examples": ["One question?"]}],
        }, custom["id"])
        self.assertEqual(updated["title"], "My Better Reset")
        self.assertEqual(updated["target_question_count"], 1)

        with self.assertRaises(ValueError):
            guided_playbooks.save_playbook(1, {"title": "x", "steps": [{"name": "x"}]}, "builtin:clear_my_head")

        guided_playbooks.delete_playbook(1, custom["id"])
        self.assertIsNone(guided_playbooks.get_playbook(1, custom["id"]))

    def test_guided_question_returns_structured_json_and_playbook_step_context(self):
        captured = {}

        def fake_ai_call(system, user, bucket=None, max_tokens=None, user_id=None):
            captured["user"] = user
            return json.dumps({
                "question": "What matters most today?",
                "question_type": "action",
                "playbook_step": 1,
                "debug_reason": "The selected step is about priorities.",
            })

        playbook = guided_playbooks.get_playbook(1, "builtin:plan_my_day")
        with patch.object(llm_service, "ai_call", side_effect=fake_ai_call):
            result = llm_service.guided_journal_question(
                recent_context="",
                time_context="Friday morning",
                objective="Plan",
                answers=[],
                skipped_questions=[],
                playbook=playbook,
                user_id=1,
            )

        self.assertEqual(result["question"], "What matters most today?")
        self.assertEqual(result["question_type"], "action")
        self.assertIn('"current_step_number": 1', captured["user"])


class AgentToolTests(TempVoiceJournalDB):
    def test_tag_object_logs_identifiable_chat_action(self):
        user_id = 1
        chat_id = topics.create_chat_unscoped(user_id, "Tag test")
        tag_id = topics.create_topic(user_id, "Prototype App")
        task_id = tasks_db.create_task(user_id, "Review prototype")

        result = agent_tools.dispatch(
            "tag_object",
            {"object_kind": "task", "object_id": task_id, "tag_id": tag_id},
            chat_id,
            user_id,
        )

        self.assertEqual(result["id"], f"task:{task_id}:{tag_id}")
        self.assertEqual([t["id"] for t in topics.get_tags_for_task(task_id)], [tag_id])
        with db.get_db() as conn:
            action = conn.execute(
                "SELECT action, object_kind, object_id FROM chat_actions WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
        self.assertEqual(action["action"], "agent_tag_object")
        self.assertEqual(action["object_kind"], "object_tag")
        self.assertEqual(action["object_id"], f"task:{task_id}:{tag_id}")

    def test_batch_tag_objects_tags_multiple_tasks(self):
        user_id = 1
        chat_id = topics.create_chat_unscoped(user_id, "Batch tag test")
        tag_id = topics.create_topic(user_id, "Work Planning")
        first_task = tasks_db.create_task(user_id, "Plan report")
        second_task = tasks_db.create_task(user_id, "Send report")

        result = agent_tools.dispatch(
            "batch_tag_objects",
            {
                "items": [
                    {"object_kind": "task", "object_id": first_task, "tag_id": tag_id},
                    {"object_kind": "task", "object_id": second_task, "tag_id": tag_id},
                ]
            },
            chat_id,
            user_id,
        )

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["errors"], [])
        self.assertEqual([t["id"] for t in topics.get_tags_for_task(first_task)], [tag_id])
        self.assertEqual([t["id"] for t in topics.get_tags_for_task(second_task)], [tag_id])
        with db.get_db() as conn:
            actions = conn.execute(
                "SELECT object_id FROM chat_actions WHERE chat_id=? ORDER BY id",
                (chat_id,),
            ).fetchall()
        self.assertEqual(
            [row["object_id"] for row in actions],
            [f"task:{first_task}:{tag_id}", f"task:{second_task}:{tag_id}"],
        )


class ModelFlexTests(TempVoiceJournalDB):
    def test_model_settings_default_and_override(self):
        self.assertEqual(ai_models.get_user_model_settings(1), {
            "regular": "anthropic_sonnet_46",
            "lite": "anthropic_haiku_45",
        })

        ai_models.set_user_model_settings(
            1,
            regular="openai_gpt_54_mini",
            lite="gemini_3_flash",
        )

        self.assertEqual(ai_models.get_preset("regular", 1).provider, "openai")
        self.assertEqual(ai_models.get_preset("lite", 1).provider, "gemini")

        ai_models.set_user_model_settings(1, lite="openai_gpt_54_nano")
        self.assertEqual(ai_models.get_preset("lite", 1).model, "gpt-5.4-nano")

    def test_openai_chat_payload_converts_claude_tool_turn_history(self):
        preset = ai_models.get_preset("regular", preset_key="openai_gpt_54_mini")
        path, payload = ai_models.chat_payload(
            preset=preset,
            system="system",
            messages=[
                {"role": "user", "content": "find task"},
                {"role": "assistant", "content": [
                    {"type": "text", "text": "Looking."},
                    {"type": "tool_use", "id": "tool-1", "name": "search_tasks", "input": {"query": "x"}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "tool-1", "content": '{"tasks":[]}'},
                ]},
            ],
            tools=[{"name": "search_tasks", "description": "Search", "input_schema": {"type": "object", "properties": {}}}],
            max_tokens=128,
        )

        self.assertEqual(path, "/proxy/openai/v1/chat/completions")
        self.assertEqual(payload["tools"][0]["type"], "function")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][2]["tool_calls"][0]["function"]["name"], "search_tasks")
        self.assertEqual(payload["messages"][3]["role"], "tool")

    def test_openai_stream_parser_normalizes_text_and_tool_calls(self):
        preset = ai_models.get_preset("regular", preset_key="openai_gpt_54_mini")
        state = {}
        out, _, text = ai_models.parse_stream_line(
            preset=preset,
            line='data: {"choices":[{"delta":{"content":"Hi"}}]}',
            state=state,
        )
        self.assertIn(b"content_block_delta", out)
        self.assertEqual(text, "Hi")

        ai_models.parse_stream_line(
            preset=preset,
            line='data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"search_tasks","arguments":"{\\"query\\":"}}]}}]}',
            state=state,
        )
        ai_models.parse_stream_line(
            preset=preset,
            line='data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"x\\"}"}}]},"finish_reason":"tool_calls"}]}',
            state=state,
        )
        calls = ai_models.flush_stream_state(preset, state)
        self.assertEqual(calls[0]["name"], "search_tasks")
        self.assertEqual(json.loads(calls[0]["input_json_str"]), {"query": "x"})

    def test_gemini_chat_payload_sets_medium_thinking(self):
        preset = ai_models.get_preset("regular", preset_key="gemini_3_flash")
        path, payload = ai_models.chat_payload(
            preset=preset,
            system="system",
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            max_tokens=8192,
        )

        self.assertIn("streamGenerateContent", path)
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 8192)
        self.assertEqual(payload["generationConfig"]["thinkingConfig"]["thinkingLevel"], "medium")

    def test_gemini_function_call_stop_does_not_cancel_tool_use(self):
        preset = ai_models.get_preset("regular", preset_key="gemini_3_flash")
        state = {}

        ai_models.parse_stream_line(
            preset=preset,
            line='data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"list_tags","args":{"kind":"topic"},"id":"gem-call-1"},"thoughtSignature":"sig-1"}]}}]}',
            state=state,
        )
        ai_models.parse_stream_line(
            preset=preset,
            line='data: {"candidates":[{"content":{"parts":[{"text":""}]},"finishReason":"STOP"}]}',
            state=state,
        )

        self.assertEqual(state["stop_reason"], "tool_use")
        calls = ai_models.flush_stream_state(preset, state)
        self.assertEqual(calls[0]["id"], "gem-call-1")
        self.assertEqual(calls[0]["name"], "list_tags")
        self.assertEqual(json.loads(calls[0]["input_json_str"]), {"kind": "topic"})
        self.assertEqual(calls[0]["provider_metadata"]["thoughtSignature"], "sig-1")

    def test_gemini_history_preserves_function_call_metadata(self):
        contents = ai_models._gemini_contents([
            {"role": "assistant", "content": [
                {
                    "type": "tool_use",
                    "id": "gem-call-1",
                    "name": "list_tags",
                    "input": {"kind": "topic"},
                    "provider_metadata": {"thoughtSignature": "sig-1", "id": "gem-call-1"},
                }
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "gem-call-1", "content": '{"tags":[]}'}
            ]},
        ])

        part = contents[0]["parts"][0]
        fn = part["functionCall"]
        self.assertEqual(part["thoughtSignature"], "sig-1")
        self.assertNotIn("thoughtSignature", fn)
        self.assertEqual(fn["id"], "gem-call-1")
        self.assertEqual(contents[1]["parts"][0]["functionResponse"]["name"], "list_tags")

    def test_gemini_tool_schema_converts_nullable_types(self):
        schema = ai_models.gemini_tool_schema({
            "name": "update_tag",
            "description": "Update tag",
            "input_schema": {
                "type": "object",
                "properties": {
                    "parent_tag_id": {"type": ["integer", "null"]},
                },
            },
        })

        parent_schema = schema["parameters"]["properties"]["parent_tag_id"]
        self.assertEqual(parent_schema["type"], "INTEGER")
        self.assertTrue(parent_schema["nullable"])

    def test_gemini_text_payload_sets_thinking_by_work_size(self):
        captured = []

        def fake_post(*args, **kwargs):
            captured.append(kwargs["json"])

            class FakeResponse:
                pass

            return FakeResponse()

        with patch.object(ai_models.req_lib, "post", side_effect=fake_post):
            ai_models.text_request(
                gateway_url="http://gateway",
                headers={},
                preset=ai_models.get_preset("regular", preset_key="gemini_3_flash"),
                system="system",
                user="user",
                max_tokens=2048,
            )
            ai_models.text_request(
                gateway_url="http://gateway",
                headers={},
                preset=ai_models.get_preset("lite", preset_key="gemini_31_flash_lite"),
                system="system",
                user="user",
                max_tokens=512,
            )

        self.assertEqual(captured[0]["generationConfig"]["thinkingConfig"]["thinkingLevel"], "medium")
        self.assertEqual(captured[1]["generationConfig"]["thinkingConfig"]["thinkingLevel"], "low")

    def test_stream_parsers_normalize_token_limit_stop_reasons(self):
        openai_preset = ai_models.get_preset("regular", preset_key="openai_gpt_54_mini")
        openai_state = {}
        ai_models.parse_stream_line(
            preset=openai_preset,
            line='data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
            state=openai_state,
        )
        self.assertEqual(openai_state["stop_reason"], "max_tokens")

        gemini_preset = ai_models.get_preset("regular", preset_key="gemini_3_flash")
        gemini_state = {}
        ai_models.parse_stream_line(
            preset=gemini_preset,
            line='data: {"candidates":[{"content":{"parts":[]},"finishReason":"MAX_TOKENS"}]}',
            state=gemini_state,
        )
        self.assertEqual(gemini_state["stop_reason"], "max_tokens")

    def test_chat_title_generation_uses_single_generous_budget(self):
        calls = []

        def fake_ai_call(system, user, bucket=None, max_tokens=None, user_id=None):
            calls.append(max_tokens)
            self.assertIn("4-7 word title", user)
            return "Daily Priority Planning"

        with patch.object(llm_service, "ai_call", side_effect=fake_ai_call):
            title = llm_service.ai_name_chat([
                {"role": "user", "content": "What should be my main core focuses today for a good day?"},
                {"role": "assistant", "content": "Focus on work, family rhythm, recovery basics, and prayer."},
            ], user_id=1)

        self.assertEqual(title, "Daily Priority Planning")
        self.assertEqual(calls, [512])

    def test_entry_title_generation_uses_single_generous_budget(self):
        calls = []

        def fake_ai_call(system, user, bucket=None, max_tokens=None, user_id=None):
            calls.append(max_tokens)
            self.assertIn("3-8 words", user)
            return "Morning Priority Planning"

        with patch.object(llm_service, "ai_call", side_effect=fake_ai_call):
            title = llm_service.ai_name_entry(
                "I talked through my main priorities for the morning and how to plan the day.",
                user_id=1,
            )

        self.assertEqual(title, "Morning Priority Planning")
        self.assertEqual(calls, [512])

    def test_chat_title_fallback_skips_tool_json_noise(self):
        messages = [
            {"role": "user", "content": "What organizational changes should we consider making on the topic screen?"},
            {"role": "assistant", "content_type": "tool_turn", "content": json.dumps([
                {"type": "tool_use", "id": "1", "name": "list_tags", "input": {"kind": "topic"}},
            ])},
            {"role": "user", "content_type": "tool_results", "content": "[{\"huge\":\"json\"}]"},
        ]

        with patch.object(llm_service, "ai_call", return_value="Optimizing"):
            title = llm_service.ai_name_chat(messages, user_id=1)

        excerpt = llm_service._chat_title_excerpt(messages)
        self.assertNotIn("tool_result", excerpt)
        self.assertEqual(title, "Organizational Changes Consider Topic Screen")


class JournalHelperTests(TempVoiceJournalDB):
    def test_entry_title_column_and_fallback_title(self):
        entry_id = journal.append_entry(1, "2026-05-01", "20:35:00", "# Custom Heading\n\nBody text")
        journal.update_entry_title(1, entry_id, "AI Named Entry")
        entry = journal.get_entry_by_id(1, entry_id)
        self.assertEqual(entry["title"], "AI Named Entry")
        self.assertEqual(journal.fallback_entry_title("# Custom Heading\n\nBody"), "Custom Heading")

    def test_journal_stream_includes_saved_chat_inputs(self):
        entry_id = journal.append_entry(1, "2026-05-01", "20:35:00", "Journal body")
        chat_id = journal.create_input(1, "chat_summary", "Saved chat body", occurred_at="2026-05-02T09:15:00-05:00")
        journal.update_entry_title(1, chat_id, "Chat Summary")

        stream = journal.list_journal_stream(1)

        self.assertEqual([item["id"] for item in stream], [chat_id, entry_id])
        self.assertEqual(stream[0]["source"], "chat_summary")
        self.assertEqual(stream[0]["content"], "Saved chat body")

    def test_journal_stream_orders_mixed_timestamp_formats_by_time(self):
        chat_id = journal.create_input(1, "chat_summary", "Saved chat body", occurred_at="2026-05-04T09:15:00-05:00")
        entry_id = journal.append_entry(1, "2026-05-04", "10:30:00", "Later journal body")

        stream = journal.list_journal_stream(1)

        self.assertEqual([item["id"] for item in stream], [entry_id, chat_id])

    def test_local_occurred_at_formats_timezone_datetime_like_journal_entries(self):
        dt = datetime.fromisoformat("2026-05-04T09:15:00-05:00")

        self.assertEqual(journal.local_occurred_at(dt, "America/Chicago"), "2026-05-04 09:15:00")


class AgentTagToolTests(TempVoiceJournalDB):
    def test_get_tag_tool_omits_embedding_payload(self):
        tag_id = topics.create_topic(1, "Architecture")
        topics.store_tag_embedding(tag_id, "[0.1, 0.2, 0.3]")

        result = agent_tools.dispatch("get_tag", {"tag_id": tag_id}, chat_id=1, user_id=1)

        self.assertEqual(result["id"], tag_id)
        self.assertNotIn("embedding", result)

    def test_batch_update_tags_updates_multiple_tags(self):
        parent_id = topics.create_topic(1, "Parent")
        child_a = topics.create_topic(1, "Child A")
        child_b = topics.create_topic(1, "Child B")
        chat_id = topics.create_chat_unscoped(1)

        result = agent_tools.dispatch(
            "batch_update_tags",
            {
                "updates": [
                    {"tag_id": child_a, "parent_tag_id": parent_id},
                    {"tag_id": child_b, "parent_tag_id": parent_id},
                ]
            },
            chat_id=chat_id,
            user_id=1,
        )

        self.assertEqual(result["errors"], [])
        self.assertEqual(set(result["affected_ids"]), {child_a, child_b})
        self.assertEqual(topics.get_topic(1, child_a)["parent_tag_id"], parent_id)
        self.assertEqual(topics.get_topic(1, child_b)["parent_tag_id"], parent_id)


class DecisionAndTrackerHardeningTests(TempVoiceJournalDB):
    def test_tracker_rejects_cross_tracker_field_value(self):
        t1 = trackers_db.create_tracker(1, "Habits")
        t2 = trackers_db.create_tracker(1, "Health")
        field_id = trackers_db.add_field(1, t2, "Mood", "mood", "text")
        row = trackers_db.get_or_create_row(1, t1, "2026-05-18", "2026-05-18")

        with self.assertRaises(trackers_db.TrackerValidationError):
            trackers_db.set_value(1, row["id"], field_id, json.dumps("good"))

    def test_tracker_question_answer_writes_value_and_closes_question(self):
        tracker_id = trackers_db.create_tracker(1, "Daily Habits")
        field_id = trackers_db.add_field(1, tracker_id, "Prayed", "prayed", "boolean", required=True)
        row = trackers_db.get_or_create_row(1, tracker_id, "2026-05-18", "2026-05-18")
        question_id = trackers_db.create_question(
            1, tracker_id, "Did you pray?", row_id=row["id"], field_id=field_id
        )

        import app as voice_app
        voice_app.app.config["TESTING"] = True
        client = voice_app.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["csrf_token"] = "test-csrf"

        response = client.post(
            f"/api/tracker-questions/{question_id}/answer",
            json={"value": True},
            headers={"X-CSRF-Token": "test-csrf"},
        )

        self.assertEqual(response.status_code, 200)
        question = trackers_db.get_question(1, question_id)
        values = trackers_db.get_values_for_row(row["id"])
        self.assertEqual(question["status"], "answered")
        self.assertEqual(json.loads(values[field_id]["value_json"]), True)

    def test_period_bounds_cover_weekly_monthly_and_ad_hoc(self):
        self.assertEqual(
            trackers_db.derive_period_bounds("2026-05-18", "weekly"),
            ("2026-05-18", "2026-05-24"),
        )
        self.assertEqual(
            trackers_db.derive_period_bounds("2026-05-18", "monthly"),
            ("2026-05-01", "2026-05-31"),
        )
        self.assertEqual(
            trackers_db.derive_period_bounds("2026-05-18", "ad_hoc"),
            ("2026-05-18", "2026-05-18"),
        )

    def test_tracker_create_defaults_to_user_timezone_and_rejects_invalid_timezone(self):
        topics.set_setting(1, "timezone", "America/Los_Angeles")

        import app as voice_app
        voice_app.app.config["TESTING"] = True
        client = voice_app.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["csrf_token"] = "test-csrf"

        response = client.post(
            "/api/trackers",
            json={"name": "West Coast Daily", "cadence": "daily"},
            headers={"X-CSRF-Token": "test-csrf"},
        )

        self.assertEqual(response.status_code, 201)
        tracker = trackers_db.get_tracker(1, response.get_json()["id"])
        self.assertEqual(tracker["timezone"], "America/Los_Angeles")

        bad_response = client.post(
            "/api/trackers",
            json={"name": "Bad TZ", "cadence": "daily", "timezone": "Not/AZone"},
            headers={"X-CSRF-Token": "test-csrf"},
        )
        self.assertEqual(bad_response.status_code, 400)

    def test_tracker_row_api_derives_period_from_date_and_cadence(self):
        tracker_id = trackers_db.create_tracker(1, "Monthly Health", cadence="monthly", timezone_str="America/Chicago")

        import app as voice_app
        voice_app.app.config["TESTING"] = True
        client = voice_app.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["csrf_token"] = "test-csrf"

        response = client.post(
            f"/api/trackers/{tracker_id}/rows",
            json={"date": "2026-05-18"},
            headers={"X-CSRF-Token": "test-csrf"},
        )

        self.assertEqual(response.status_code, 201)
        row = response.get_json()
        self.assertEqual(row["period_start"], "2026-05-01")
        self.assertEqual(row["period_end"], "2026-05-31")

    def test_tracker_inference_uses_monthly_period_and_skips_ad_hoc_questions(self):
        monthly = trackers_db.create_tracker(1, "Monthly Health", cadence="monthly")
        mood = trackers_db.add_field(1, monthly, "Mood", "mood", "number", required=True)
        ad_hoc = trackers_db.create_tracker(1, "One Off", cadence="ad_hoc")
        ad_hoc_field = trackers_db.add_field(1, ad_hoc, "Note", "note", "text", required=True)

        payload = {
            "inferred": [{
                "tracker_id": monthly,
                "field_id": mood,
                "value": 7,
                "confidence": "high",
                "reason": "explicit",
            }],
            "questions": [{
                "tracker_id": ad_hoc,
                "field_id": ad_hoc_field,
                "question": "What was the note?",
                "reason": "missing",
            }],
        }
        with patch("llm_service._ai_text_call", return_value=json.dumps(payload)):
            llm_service.infer_tracker_values(1, "entry-1", "2026-05-18", "Mood was 7.")

        rows = trackers_db.list_rows(1, monthly)
        self.assertEqual(rows[0]["period_start"], "2026-05-01")
        self.assertEqual(rows[0]["period_end"], "2026-05-31")
        self.assertEqual(trackers_db.list_open_questions(1, tracker_id=ad_hoc), [])

    def test_tracker_cron_populates_linked_recurring_task_count(self):
        root_id = tasks_db.create_task(
            1,
            "Pray Rosary",
            due_at="2026-05-04",
            recurrence_rule="daily",
            source="user",
        )
        second_id = tasks_db.create_next_occurrence(
            1,
            tasks_db.get_task(1, root_id),
            "2026-05-05",
        )
        tasks_db.update_task(1, root_id, status="done")
        tasks_db.update_task(1, second_id, status="done")
        with db.get_db() as conn:
            conn.execute("UPDATE tasks SET completed_at=? WHERE id=?", ("2026-05-04T12:00:00+00:00", root_id))
            conn.execute("UPDATE tasks SET completed_at=? WHERE id=?", ("2026-05-05T12:00:00+00:00", second_id))

        tracker_id = trackers_db.create_tracker(1, "Weekly Prayer", cadence="weekly")
        field_id = trackers_db.add_field(
            1,
            tracker_id,
            "Rosary completions",
            "rosary_completions",
            "number",
            inference_policy="system_computed",
            linked_task_id=root_id,
            ai_explanation="Use the linked recurring task completion count for the period.",
        )

        result = llm_service.run_tracker_cron(1, "2026-05-11")

        self.assertEqual(result["processed"], 1)
        row = trackers_db.list_rows(1, tracker_id)[0]
        values = trackers_db.get_values_for_row(row["id"])
        self.assertEqual(row["period_start"], "2026-05-04")
        self.assertEqual(row["period_end"], "2026-05-10")
        self.assertEqual(json.loads(values[field_id]["value_json"]), 2)

    def test_tracker_period_population_uses_ai_explanation_and_creates_question(self):
        journal.append_entry(1, "2026-05-18", "08:00:00", "I had a stressful morning.")
        tracker_id = trackers_db.create_tracker(1, "Daily Health", cadence="daily")
        field_id = trackers_db.add_field(
            1,
            tracker_id,
            "Mood",
            "mood",
            "number",
            required=True,
            inference_policy="ask_if_missing",
            ai_explanation="Ask for explicit confirmation unless the user gives a numeric mood.",
        )

        captured = {}

        def fake_ai(system, user, **kwargs):
            captured["user"] = user
            return json.dumps({
                "inferred": [],
                "questions": [{
                    "field_id": field_id,
                    "question": "What mood number should I log?",
                    "reason": "No numeric mood was stated.",
                }],
            })

        with patch("llm_service._ai_text_call", side_effect=fake_ai):
            result = llm_service.run_tracker_cron(1, "2026-05-19")

        self.assertTrue(result["results"][0]["used_ai"])
        self.assertIn("Ask for explicit confirmation", captured["user"])
        questions = trackers_db.list_open_questions(1, tracker_id)
        self.assertEqual(questions[0]["field_id"], field_id)

    def test_decision_extraction_skips_fuzzy_duplicate(self):
        decisions_db.create_item(
            1,
            item_type="decision",
            title="Use SQLite for the tracker store",
            status="decided",
        )
        with patch("llm_service._extract_decision_candidates") as extract:
            extract.return_value = [{
                "type": "decision",
                "title": "Use sqlite tracker store",
                "content": "We settled on SQLite.",
                "status": "decided",
                "confidence": "high",
                "tag_ids": [],
            }]

            items = llm_service.extract_decision_log_items(
                1, "entry-1", "2026-05-18", "We settled on SQLite."
            )

        self.assertEqual(items, [])
        self.assertEqual(len(decisions_db.list_items(1)), 1)

    def test_manual_duplicate_decision_api_returns_conflict(self):
        decisions_db.create_item(1, "decision", "Use SQLite for trackers", status="decided")
        import app as voice_app
        voice_app.app.config["TESTING"] = True
        client = voice_app.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["csrf_token"] = "test-csrf"

        response = client.post(
            "/api/decisions",
            json={"item_type": "decision", "title": "Use sqlite trackers", "status": "decided"},
            headers={"X-CSRF-Token": "test-csrf"},
        )

        self.assertEqual(response.status_code, 409)

    def test_invalid_tracker_and_decision_domain_values_are_rejected(self):
        with self.assertRaises(trackers_db.TrackerValidationError):
            trackers_db.create_tracker(1, "Bad", cadence="yearly")
        with self.assertRaises(decisions_db.DecisionValidationError):
            decisions_db.create_item(1, "maybe", "Bad item")

    def test_new_decision_and_tracker_ai_functions_are_registered(self):
        for func_key in ("decision_extraction", "tracker_schema_proposal", "tracker_inference"):
            preset = ai_models.get_func_preset(func_key, 1)
            self.assertIn(preset.key, {p.key for p in ai_models.LITE_PRESETS})


class DisplayHelperTests(unittest.TestCase):
    def test_chat_tool_turns_are_interspersed_with_assistant_text(self):
        from app import _display_chat_messages

        messages = [
            {"role": "user", "content": "Question", "content_type": "text"},
            {
                "role": "assistant",
                "content_type": "tool_turn",
                "content": json.dumps([
                    {"type": "text", "text": "Let me look."},
                    {"type": "tool_use", "id": "a", "name": "search_entries", "input": {"query": "x"}},
                    {"type": "tool_use", "id": "b", "name": "list_entries", "input": {}},
                ]),
            },
            {"role": "user", "content_type": "tool_results", "content": "[]"},
            {
                "role": "assistant",
                "content_type": "tool_turn",
                "content": json.dumps([
                    {"type": "text", "text": "Good context."},
                    {"type": "tool_use", "id": "c", "name": "search_tags", "input": {}},
                ]),
            },
            {"role": "user", "content_type": "tool_results", "content": "[]"},
            {"role": "assistant", "content": "Final answer.", "content_type": "text"},
        ]

        display = _display_chat_messages(messages)
        self.assertEqual([m["role"] for m in display], ["user", "assistant", "assistant", "assistant"])
        self.assertEqual(display[1]["content"], "Let me look.")
        self.assertEqual(len(display[1]["tools"]), 2)
        self.assertEqual(display[2]["content"], "Good context.")
        self.assertEqual(len(display[2]["tools"]), 1)
        self.assertEqual(display[3]["content"], "Final answer.")
        self.assertNotIn("tools", display[3])

    def test_entry_datetime_format_is_12_hour(self):
        from app import format_entry_datetime

        self.assertEqual(
            format_entry_datetime("2026-05-05", "20:35:00"),
            "Tuesday, May 5, 8:35 PM",
        )


if __name__ == "__main__":
    unittest.main()
