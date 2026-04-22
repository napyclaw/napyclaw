import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from napyclaw.db import Database
from napyclaw.tools.base import Tool
from napyclaw.tools.web_search import SearXNGBackend, WebSearchTool
from napyclaw.tools.file_ops import FileReadTool, FileWriteTool
from napyclaw.tools.messaging import SendMessageTool
from napyclaw.tools.scheduling import ScheduleTaskTool
from napyclaw.tools.identity import RenameBot, AddNickname, ClearNicknames, SwitchModel


# ---------------------------------------------------------------------------
# Tool ABC
# ---------------------------------------------------------------------------


class TestToolSchema:
    def test_schema_property(self):
        class DummyTool(Tool):
            name = "dummy"
            description = "A dummy tool"
            parameters = {"type": "object", "properties": {}}

            async def execute(self, **kwargs) -> str:
                return "ok"

        tool = DummyTool()
        schema = tool.schema
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "dummy"
        assert schema["function"]["description"] == "A dummy tool"


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------


class TestWebSearchTool:
    def _make_searxng_backend(self, mock_http):
        return SearXNGBackend(base_url="http://searxng:8080", http_client=mock_http)

    async def test_returns_results(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"title": "Result 1", "url": "https://example.com", "content": "A result"},
            ]
        }
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)

        tool = WebSearchTool(backends=[self._make_searxng_backend(mock_http)])
        result = await tool.execute(query="test query")
        assert "Result 1" in result
        assert "https://example.com" in result

    async def test_empty_query_returns_error(self):
        mock_http = AsyncMock()
        tool = WebSearchTool(backends=[self._make_searxng_backend(mock_http)])
        result = await tool.execute(query="")
        assert "Error" in result

    async def test_api_error_returns_error_string(self):
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("timeout"))

        tool = WebSearchTool(backends=[self._make_searxng_backend(mock_http)])
        result = await tool.execute(query="test")
        assert "Error" in result
        assert "timeout" in result

    async def test_multi_provider_deduplicates(self):
        def make_resp(results):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"results": results}
            http = AsyncMock()
            http.get = AsyncMock(return_value=mock_resp)
            return http

        shared_url = "https://shared.com"
        http1 = make_resp([{"title": "A", "url": shared_url, "content": "first"}])
        http2 = make_resp([
            {"title": "A", "url": shared_url, "content": "duplicate"},
            {"title": "B", "url": "https://unique.com", "content": "unique"},
        ])
        b1 = SearXNGBackend(base_url="http://s1:8080", http_client=http1)
        b2 = SearXNGBackend(base_url="http://s2:8080", http_client=http2)

        tool = WebSearchTool(backends=[b1, b2])
        result = await tool.execute(query="test")
        assert result.count(shared_url) == 1
        assert "unique.com" in result

    async def test_provider_param_targets_specific_backend(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [{"title": "Targeted", "url": "https://t.com", "content": "yes"}]
        }
        http = AsyncMock()
        http.get = AsyncMock(return_value=mock_resp)
        backend = SearXNGBackend(base_url="http://searxng:8080", http_client=http)

        tool = WebSearchTool(backends=[backend])
        result = await tool.execute(query="test", providers=["searxng"])
        assert "Targeted" in result

    async def test_web_search_returns_pending_on_202(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.status_code = 202
        mock_resp.json.return_value = {"status": "pending", "token": "tok1", "retry_after": 30}
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)

        backend = SearXNGBackend(base_url="http://searxng:8080", http_client=mock_http)
        tool = WebSearchTool(backends=[backend])
        result = await tool.execute(query="test query")
        assert "pending approval" in result.lower()
        assert "tok1" in result

    async def test_web_search_multi_backend_pending_on_202(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.status_code = 202
        mock_resp.json.return_value = {"status": "pending", "token": "tok2", "retry_after": 60}
        mock_http1 = AsyncMock()
        mock_http1.get = AsyncMock(return_value=mock_resp)
        mock_http2 = AsyncMock()
        mock_http2.get = AsyncMock(return_value=mock_resp)

        b1 = SearXNGBackend(base_url="http://s1:8080", http_client=mock_http1)
        b2 = SearXNGBackend(base_url="http://s2:8080", http_client=mock_http2)
        tool = WebSearchTool(backends=[b1, b2])
        result = await tool.execute(query="test multi")
        assert "pending" in result.lower()
        assert "tok2" in result


# ---------------------------------------------------------------------------
# FileReadTool / FileWriteTool
# ---------------------------------------------------------------------------


class TestFileOps:
    def _make_config(self, workspace: Path) -> MagicMock:
        config = MagicMock()
        config.workspace_dir = workspace
        return config

    async def test_file_read(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "hello.txt").write_text("hello world", encoding="utf-8")

        tool = FileReadTool(config=self._make_config(workspace))
        result = await tool.execute(path="hello.txt")
        assert result == "hello world"

    async def test_file_read_not_found(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        tool = FileReadTool(config=self._make_config(workspace))
        result = await tool.execute(path="nope.txt")
        assert "Error" in result
        assert "not found" in result

    async def test_file_read_path_traversal_blocked(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        tool = FileReadTool(config=self._make_config(workspace))
        result = await tool.execute(path="../secret.txt")
        assert "Error" in result
        assert "traversal" in result

    async def test_file_write(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        tool = FileWriteTool(config=self._make_config(workspace))
        result = await tool.execute(path="out.txt", content="written!")
        assert result == "Written: out.txt"
        assert (workspace / "out.txt").read_text(encoding="utf-8") == "written!"

    async def test_file_write_creates_subdirectories(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        tool = FileWriteTool(config=self._make_config(workspace))
        result = await tool.execute(path="sub/dir/file.txt", content="deep")
        assert result == "Written: sub/dir/file.txt"
        assert (workspace / "sub" / "dir" / "file.txt").read_text(encoding="utf-8") == "deep"

    async def test_file_write_path_traversal_blocked(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        tool = FileWriteTool(config=self._make_config(workspace))
        result = await tool.execute(path="../escape.txt", content="bad")
        assert "Error" in result
        assert "traversal" in result

    async def test_memory_md_routing_read(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        memory_path = tmp_path / "groups" / "C001" / "MEMORY.md"
        memory_path.parent.mkdir(parents=True)
        memory_path.write_text("memory content", encoding="utf-8")

        tool = FileReadTool(config=self._make_config(workspace), memory_path=memory_path)
        result = await tool.execute(path="MEMORY.md")
        assert result == "memory content"

    async def test_memory_md_routing_write(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        memory_path = tmp_path / "groups" / "C001" / "MEMORY.md"
        memory_path.parent.mkdir(parents=True)

        tool = FileWriteTool(config=self._make_config(workspace), memory_path=memory_path)
        result = await tool.execute(path="memory.md", content="new memory")
        assert result == "Written: memory.md"
        assert memory_path.read_text(encoding="utf-8") == "new memory"


# ---------------------------------------------------------------------------
# SendMessageTool
# ---------------------------------------------------------------------------


class TestSendMessageTool:
    async def test_send_to_current_channel(self):
        channel = AsyncMock()
        tool = SendMessageTool(channel=channel, current_group_id="C001")
        result = await tool.execute(text="hello")
        assert result == "Sent"
        channel.send.assert_called_once_with("C001", "hello")

    async def test_send_to_specific_channel(self):
        channel = AsyncMock()
        tool = SendMessageTool(channel=channel, current_group_id="C001")
        result = await tool.execute(text="hello", group_id="C999")
        assert result == "Sent"
        channel.send.assert_called_once_with("C999", "hello")

    async def test_empty_text_returns_error(self):
        channel = AsyncMock()
        tool = SendMessageTool(channel=channel, current_group_id="C001")
        result = await tool.execute(text="")
        assert "Error" in result

    async def test_send_failure_returns_error(self):
        channel = AsyncMock()
        channel.send = AsyncMock(side_effect=Exception("network error"))
        tool = SendMessageTool(channel=channel, current_group_id="C001")
        result = await tool.execute(text="hello")
        assert "Error" in result


# ---------------------------------------------------------------------------
# ScheduleTaskTool
# ---------------------------------------------------------------------------


@pytest.fixture
async def sched_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    await db.init()
    return db


class TestScheduleTaskTool:
    async def test_create_task(self, sched_db: Database):
        tool = ScheduleTaskTool(db=sched_db, group_id="C001", owner_id="U001")
        result = await tool.execute(
            action="create",
            prompt="Say hello",
            schedule_type="interval",
            schedule_value="3600",
        )
        data = json.loads(result)
        assert data["status"] == "active"
        assert data["prompt"] == "Say hello"

    async def test_list_tasks(self, sched_db: Database):
        tool = ScheduleTaskTool(db=sched_db, group_id="C001", owner_id="U001")
        await tool.execute(
            action="create",
            prompt="Task 1",
            schedule_type="once",
            schedule_value="2026-04-01T00:00:00Z",
        )
        result = await tool.execute(action="list")
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["prompt"] == "Task 1"

    async def test_list_empty(self, sched_db: Database):
        tool = ScheduleTaskTool(db=sched_db, group_id="C001", owner_id="U001")
        result = await tool.execute(action="list")
        assert result == "No scheduled tasks."

    async def test_cancel_task(self, sched_db: Database):
        tool = ScheduleTaskTool(db=sched_db, group_id="C001", owner_id="U001")
        create_result = await tool.execute(
            action="create",
            prompt="Cancel me",
            schedule_type="interval",
            schedule_value="60",
        )
        task_id = json.loads(create_result)["id"]

        cancel_result = await tool.execute(action="cancel", task_id=task_id)
        data = json.loads(cancel_result)
        assert data["status"] == "paused"

    async def test_cancel_wrong_group(self, sched_db: Database):
        tool_a = ScheduleTaskTool(db=sched_db, group_id="C001", owner_id="U001")
        create_result = await tool_a.execute(
            action="create",
            prompt="Other group",
            schedule_type="interval",
            schedule_value="60",
        )
        task_id = json.loads(create_result)["id"]

        tool_b = ScheduleTaskTool(db=sched_db, group_id="C999", owner_id="U001")
        result = await tool_b.execute(action="cancel", task_id=task_id)
        assert "not found" in result

    async def test_create_missing_fields(self, sched_db: Database):
        tool = ScheduleTaskTool(db=sched_db, group_id="C001", owner_id="U001")
        result = await tool.execute(action="create")
        assert "Error" in result


# ---------------------------------------------------------------------------
# Identity tools
# ---------------------------------------------------------------------------


@pytest.fixture
async def identity_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    await db.init()
    await db.save_group_context(
        group_id="C001",
        default_name="General_napy",
        display_name="General_napy",
        nicknames=[],
        owner_id="U001",
        provider="ollama",
        model="llama3.3:latest",
        is_first_interaction=False,
        history=[],
    )
    return db


class TestRenameBot:
    async def test_owner_can_rename(self, identity_db: Database):
        tool = RenameBot(db=identity_db, group_id="C001", owner_id="U001")
        result = await tool.execute(sender_id="U001", new_name="kevin")
        assert result == "Renamed to Kevin"

        ctx = await identity_db.load_group_context("C001")
        assert ctx["display_name"] == "Kevin"

    async def test_non_owner_blocked(self, identity_db: Database):
        tool = RenameBot(db=identity_db, group_id="C001", owner_id="U001")
        result = await tool.execute(sender_id="U999", new_name="hacker")
        assert "Only the channel owner" in result


class TestAddNickname:
    async def test_anyone_can_add(self, identity_db: Database):
        tool = AddNickname(db=identity_db, group_id="C001")
        result = await tool.execute(nickname="Kev")
        assert result == "Nickname 'Kev' added"

        ctx = await identity_db.load_group_context("C001")
        assert "Kev" in ctx["nicknames"]

    async def test_duplicate_ignored(self, identity_db: Database):
        tool = AddNickname(db=identity_db, group_id="C001")
        await tool.execute(nickname="Kev")
        await tool.execute(nickname="Kev")

        ctx = await identity_db.load_group_context("C001")
        assert ctx["nicknames"].count("Kev") == 1


class TestClearNicknames:
    async def test_owner_can_clear(self, identity_db: Database):
        tool_add = AddNickname(db=identity_db, group_id="C001")
        await tool_add.execute(nickname="Kev")
        await tool_add.execute(nickname="K-bot")

        tool = ClearNicknames(db=identity_db, group_id="C001", owner_id="U001")
        result = await tool.execute(sender_id="U001")
        assert result == "All nicknames cleared"

        ctx = await identity_db.load_group_context("C001")
        assert ctx["nicknames"] == []

    async def test_non_owner_blocked(self, identity_db: Database):
        tool = ClearNicknames(db=identity_db, group_id="C001", owner_id="U001")
        result = await tool.execute(sender_id="U999")
        assert "Only the channel owner" in result


class TestSwitchModel:
    async def test_owner_can_switch(self, identity_db: Database):
        tool = SwitchModel(db=identity_db, group_id="C001", owner_id="U001")
        result = await tool.execute(sender_id="U001", provider="openai", model="gpt-4o")
        assert result == "Switched to openai/gpt-4o"

        ctx = await identity_db.load_group_context("C001")
        assert ctx["provider"] == "openai"
        assert ctx["model"] == "gpt-4o"

    async def test_non_owner_blocked(self, identity_db: Database):
        tool = SwitchModel(db=identity_db, group_id="C001", owner_id="U001")
        result = await tool.execute(sender_id="U999", provider="openai", model="gpt-4o")
        assert "Only the channel owner" in result
