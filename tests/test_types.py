from napyclaw.channels.base import Message
from napyclaw.models.base import ChatResponse, ToolCall


def test_message_fields():
    msg = Message(
        group_id="C0123ABC",
        channel_name="general",
        sender_id="U0123ABC",
        sender_name="Alice",
        text="hello",
        timestamp="2026-03-24T12:00:00Z",
        channel_type="slack",
    )
    assert msg.group_id == "C0123ABC"
    assert msg.channel_type == "slack"


def test_tool_call_fields():
    tc = ToolCall(id="call_1", name="web_search", arguments={"query": "test"})
    assert tc.name == "web_search"
    assert tc.arguments == {"query": "test"}


def test_chat_response_text_only():
    resp = ChatResponse(text="Hello!", tool_calls=None, finish_reason="stop")
    assert resp.text == "Hello!"
    assert resp.tool_calls is None


def test_chat_response_tool_calls():
    tc = ToolCall(id="call_1", name="web_search", arguments={"query": "test"})
    resp = ChatResponse(text=None, tool_calls=[tc], finish_reason="tool_calls")
    assert resp.text is None
    assert len(resp.tool_calls) == 1
