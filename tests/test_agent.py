import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from napyclaw.agent import Agent, AgentLoopError
from napyclaw.models.base import ChatResponse, ToolCall
from napyclaw.tools.base import Tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_client(responses: list[ChatResponse]) -> MagicMock:
    """Create a mock LLMClient that returns responses in order."""
    client = MagicMock()
    client.provider = "test"
    client.model = "test-model"
    client.context_window = 8192
    client.chat = AsyncMock(side_effect=responses)
    return client


class EchoTool(Tool):
    name = "echo"
    description = "Echoes the input"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def execute(self, **kwargs) -> str:
        return f"Echo: {kwargs.get('text', '')}"


# ---------------------------------------------------------------------------
# Agent tests
# ---------------------------------------------------------------------------


class TestAgent:
    async def test_simple_text_response(self):
        client = _mock_client([
            ChatResponse(text="Hello!", tool_calls=None, finish_reason="stop"),
        ])
        agent = Agent(client=client, tools=[], system_prompt="You are helpful.")
        result = await agent.run("hi")
        assert result == "Hello!"

    async def test_tool_call_then_response(self):
        client = _mock_client([
            ChatResponse(
                text=None,
                tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "world"})],
                finish_reason="tool_calls",
            ),
            ChatResponse(text="Echo said: world", tool_calls=None, finish_reason="stop"),
        ])
        agent = Agent(
            client=client,
            tools=[EchoTool()],
            system_prompt="You are helpful.",
        )
        result = await agent.run("echo world")
        assert result == "Echo said: world"
        assert client.chat.call_count == 2

    async def test_multiple_tool_calls_in_one_turn(self):
        client = _mock_client([
            ChatResponse(
                text=None,
                tool_calls=[
                    ToolCall(id="call_1", name="echo", arguments={"text": "a"}),
                    ToolCall(id="call_2", name="echo", arguments={"text": "b"}),
                ],
                finish_reason="tool_calls",
            ),
            ChatResponse(text="Done", tool_calls=None, finish_reason="stop"),
        ])
        agent = Agent(
            client=client,
            tools=[EchoTool()],
            system_prompt="You are helpful.",
        )
        result = await agent.run("echo both")
        assert result == "Done"

        # Check tool results were appended
        tool_msgs = [m for m in agent.history if m.get("role") == "tool"]
        assert len(tool_msgs) == 2
        assert tool_msgs[0]["content"] == "Echo: a"
        assert tool_msgs[1]["content"] == "Echo: b"

    async def test_unknown_tool_returns_error(self):
        client = _mock_client([
            ChatResponse(
                text=None,
                tool_calls=[ToolCall(id="call_1", name="nonexistent", arguments={})],
                finish_reason="tool_calls",
            ),
            ChatResponse(text="Sorry", tool_calls=None, finish_reason="stop"),
        ])
        agent = Agent(
            client=client,
            tools=[EchoTool()],
            system_prompt="You are helpful.",
        )
        result = await agent.run("do something")
        assert result == "Sorry"

        tool_msgs = [m for m in agent.history if m.get("role") == "tool"]
        assert "unknown tool" in tool_msgs[0]["content"]

    async def test_loop_limit_raises(self):
        # Always return tool calls — should hit the limit
        tool_response = ChatResponse(
            text=None,
            tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "loop"})],
            finish_reason="tool_calls",
        )
        client = MagicMock()
        client.provider = "test"
        client.model = "test-model"
        client.context_window = 8192
        client.chat = AsyncMock(return_value=tool_response)

        agent = Agent(
            client=client,
            tools=[EchoTool()],
            system_prompt="You are helpful.",
            max_tool_iterations=3,
        )

        with pytest.raises(AgentLoopError):
            await agent.run("loop forever")

    async def test_history_persisted(self):
        client = _mock_client([
            ChatResponse(text="First", tool_calls=None, finish_reason="stop"),
        ])
        agent = Agent(client=client, tools=[], system_prompt="Test.")
        await agent.run("hello")

        assert len(agent.history) == 2  # user + assistant
        assert agent.history[0]["role"] == "user"
        assert agent.history[1]["role"] == "assistant"

    async def test_history_restored(self):
        """Agent can be initialized with prior history."""
        prior = [
            {"role": "user", "content": "old message"},
            {"role": "assistant", "content": "old response"},
        ]
        client = _mock_client([
            ChatResponse(text="New response", tool_calls=None, finish_reason="stop"),
        ])
        agent = Agent(
            client=client, tools=[], system_prompt="Test.", history=prior
        )
        result = await agent.run("new message")
        assert result == "New response"
        assert len(agent.history) == 4  # 2 old + 1 new user + 1 new assistant

    async def test_system_prompt_always_first(self):
        client = _mock_client([
            ChatResponse(text="Ok", tool_calls=None, finish_reason="stop"),
        ])
        agent = Agent(client=client, tools=[], system_prompt="Be concise.")
        await agent.run("hi")

        # Verify system prompt is first message passed to client
        call_args = client.chat.call_args
        messages = call_args[0][0]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "Be concise."

    async def test_sender_id_passed_to_tools(self):
        class SpyTool(Tool):
            name = "spy"
            description = "Captures sender_id"
            parameters = {"type": "object", "properties": {}}
            captured_sender_id = None

            async def execute(self, **kwargs) -> str:
                self.captured_sender_id = kwargs.get("sender_id")
                return "ok"

        spy = SpyTool()
        client = _mock_client([
            ChatResponse(
                text=None,
                tool_calls=[ToolCall(id="call_1", name="spy", arguments={})],
                finish_reason="tool_calls",
            ),
            ChatResponse(text="Done", tool_calls=None, finish_reason="stop"),
        ])
        agent = Agent(client=client, tools=[spy], system_prompt="Test.")
        await agent.run("do it", sender_id="U001")
        assert spy.captured_sender_id == "U001"

    async def test_tool_schema_property(self):
        agent = Agent(
            client=MagicMock(), tools=[EchoTool()], system_prompt="Test."
        )
        schemas = agent.tool_schemas
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "echo"


class TestHistoryPruning:
    async def test_prune_removes_oldest_exchange(self):
        # Build agent with small context window to force pruning
        client = MagicMock()
        client.provider = "test"
        client.model = "test-model"
        client.context_window = 200  # Very small — will force pruning
        client.chat = AsyncMock(
            return_value=ChatResponse(text="Ok", tool_calls=None, finish_reason="stop")
        )

        agent = Agent(
            client=client,
            tools=[],
            system_prompt="Short.",
            history=[
                {"role": "user", "content": "message 1"},
                {"role": "assistant", "content": "response 1"},
                {"role": "user", "content": "message 2"},
                {"role": "assistant", "content": "response 2"},
                {"role": "user", "content": "message 3"},
                {"role": "assistant", "content": "response 3"},
            ],
        )

        await agent.run("message 4")

        # History should have been pruned — oldest exchanges removed
        user_msgs = [m["content"] for m in agent.history if m["role"] == "user"]
        # At minimum, the latest message should be there
        assert "message 4" in user_msgs
        # And the oldest should be gone
        assert "message 1" not in user_msgs
