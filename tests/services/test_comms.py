import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport


class _FakeRow(dict):
    def __getitem__(self, key):
        return super().__getitem__(key)


class _FakeWebSocket:
    def __init__(self, inbound: list[dict]):
        self._inbound = inbound
        self.sent: list[dict] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def iter_json(self):
        for item in self._inbound:
            yield item

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("OWNER_CHANNEL", "C-owner")
    # Ensure module is imported so patch() can resolve it by dotted name
    import services.comms.main  # noqa: F401
    with patch("services.comms.main.AsyncWebClient") as mock_wc:
        mock_wc.return_value.chat_postMessage = AsyncMock(return_value={"ok": True})
        # Reset module-level _slack to the mock instance after patching
        import services.comms.main as comms_main
        comms_main._slack = mock_wc.return_value
        # OWNER_CHANNEL is read at module level, so patch it directly
        comms_main.OWNER_CHANNEL = "C-owner"
        comms_main._bot_webhook = None
        comms_main._ws_connection = None
        comms_main._specialists = []
        comms_main._message_buffer = {}
        comms_main._db_pool = None
        comms_main._pending_approvals = {}
        comms_main._pending_memory_approvals = {}
        comms_main._correction_window = {}
        from services.comms.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c, mock_wc.return_value


async def test_send_message(client):
    c, mock_slack = client
    resp = await c.post("/send", json={"channel": "C123", "text": "hello"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_slack.chat_postMessage.assert_called_once_with(channel="C123", text="hello")


async def test_notify_approval(client):
    c, mock_slack = client
    resp = await c.post(
        "/notify/approval",
        json={"token": "tok123", "hostname": "example.com", "url": "https://example.com/api"}
    )
    assert resp.status_code == 200
    mock_slack.chat_postMessage.assert_called_once()
    call_kwargs = mock_slack.chat_postMessage.call_args
    assert call_kwargs.kwargs["channel"] == "C-owner"
    assert "approve once tok123" in call_kwargs.kwargs["text"]
    assert "deny always tok123" in call_kwargs.kwargs["text"]


async def test_register_bot_webhook(client):
    c, _ = client
    resp = await c.post("/register", json={"webhook_url": "http://bot:9000/inbound"})
    assert resp.status_code == 200


async def test_ws_receive_message_dispatches_to_webhook():
    """Browser message over WS is forwarded to bot webhook."""
    import services.comms.main as m
    m._bot_webhook = "http://bot:9000/inbound"
    m._ws_connection = None

    with patch("services.comms.main._http_post", new_callable=AsyncMock) as mock_post:
        ws = _FakeWebSocket([{
            "type": "message",
            "group_id": "grp-1",
            "text": "Hello",
        }])
        await m.websocket_endpoint(ws)
        await asyncio.sleep(0)
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][1]["group_id"] == "grp-1"
        assert call_args[0][1]["text"] == "Hello"


async def test_send_pushes_to_ws_when_connected(client):
    """POST /send pushes over WebSocket if one is connected, not to Slack."""
    import services.comms.main as m

    pushed = []
    original = m._push_to_ws

    async def capture(payload):
        pushed.append(payload)

    # Simulate a connected WebSocket by using a sentinel truthy object
    m._ws_connection = object()
    m._push_to_ws = capture
    c, mock_slack = client

    resp = await c.post("/send", json={"channel": "grp-1", "text": "Hi"})
    assert resp.status_code == 200
    assert len(pushed) == 1
    assert pushed[0]["group_id"] == "grp-1"
    assert pushed[0]["text"] == "Hi"
    mock_slack.chat_postMessage.assert_not_called()

    m._push_to_ws = original
    m._ws_connection = None


async def test_specialists_sync_and_get(client):
    """POST /specialists-sync stores list; GET /specialists returns it."""
    c, _ = client
    payload = {"specialists": [
        {"group_id": "g1", "display_name": "Rex", "nicknames": ["Rex"], "job_title": "Stats"},
    ]}
    resp = await c.post("/specialists-sync", json=payload)
    assert resp.status_code == 200

    resp = await c.get("/specialists")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["group_id"] == "g1"


async def test_specialists_get_falls_back_to_db(client):
    import services.comms.main as m

    fake_pool = AsyncMock()
    fake_pool.fetch.return_value = [
        _FakeRow({
            "group_id": "g-db",
            "display_name": "Persisted",
            "nicknames": '["Persisted"]',
            "job_title": "Research",
        }),
    ]
    m._db_pool = fake_pool
    m._specialists = []

    c, _ = client
    resp = await c.get("/specialists")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["group_id"] == "g-db"
    assert data[0]["nicknames"] == ["Persisted"]


async def test_history_endpoint_uses_persisted_history_and_buffer(client):
    import services.comms.main as m

    fake_pool = AsyncMock()
    fake_pool.fetchrow.return_value = _FakeRow({
        "history": '[{"role":"user","content":"older"},{"role":"assistant","content":"reply"}]',
    })
    m._db_pool = fake_pool
    m._message_buffer = {
        "grp-1": [{"role": "user", "text": "newer"}],
    }

    c, _ = client
    resp = await c.get("/history/grp-1")
    assert resp.status_code == 200
    assert resp.json() == [
        {"role": "user", "text": "older"},
        {"role": "assistant", "text": "reply"},
        {"role": "user", "text": "newer"},
    ]


async def test_ws_hello_replays_persisted_history():
    import services.comms.main as m

    fake_pool = AsyncMock()
    fake_pool.fetchrow.return_value = _FakeRow({
        "history": '[{"role":"user","content":"older"},{"role":"assistant","content":"reply"}]',
    })
    m._db_pool = fake_pool
    m._message_buffer = {}
    m._ws_connection = None

    ws = _FakeWebSocket([{
        "type": "hello",
        "group_id": "grp-1",
        "owner_name": "Nathan",
    }])
    await m.websocket_endpoint(ws)
    first, second = ws.sent

    assert first["replayed"] is True
    assert first["text"] == "older"
    assert second["text"] == "reply"


async def test_approval_respond_posts_to_egressguard(client):
    """POST /approval/respond forwards decision to egressguard callback URL."""
    import services.comms.main as m
    m._pending_approvals["tok-abc"] = "http://egressguard:8000/callback/tok-abc"
    c, _ = client

    with patch("services.comms.main._http_post", new_callable=AsyncMock) as mock_post:
        resp = await c.post("/approval/respond", json={
            "token": "tok-abc",
            "decision": "approve_once",
        })
    assert resp.status_code == 200
    mock_post.assert_called_once()
    call_url = mock_post.call_args[0][0]
    assert "tok-abc" in call_url


async def test_ws_hello_sets_owner_name_forwarded_in_message():
    """WS hello with owner_name is forwarded as sender_name in subsequent message payload."""
    import services.comms.main as m
    m._bot_webhook = "http://bot:9000/inbound"
    m._ws_owner_name = ""
    m._ws_connection = None

    with patch("services.comms.main._http_post", new_callable=AsyncMock) as mock_post:
        ws = _FakeWebSocket([
            {
                "type": "hello",
                "owner_name": "Nathan",
            },
            {
                "type": "message",
                "group_id": "grp-1",
                "text": "Hello from Nathan",
            },
        ])
        await m.websocket_endpoint(ws)
        await asyncio.sleep(0)
        mock_post.assert_called_once()
        payload = mock_post.call_args[0][1]
        assert payload["sender_name"] == "Nathan"


async def test_backstage_event_stores_pending_memory_approval(client):
    """POST /backstage/event with memory_pending_approval populates _pending_memory_approvals."""
    import services.comms.main as m
    m._pending_memory_approvals = {}

    c, _ = client
    resp = await c.post("/backstage/event", json={
        "group_id": "grp-sales",
        "event": {
            "type": "memory_pending_approval",
            "token": "tok-mem-1",
            "content": "Always greet users by name.",
            "entry_type": "responsibility",
        },
    })
    assert resp.status_code == 200
    assert "tok-mem-1" in m._pending_memory_approvals
    stored = m._pending_memory_approvals["tok-mem-1"]
    assert stored["content"] == "Always greet users by name."
    assert stored["entry_type"] == "responsibility"
    assert stored["group_id"] == "grp-sales"


async def test_ws_memory_approved_forwards_with_content():
    """WS memory_approved message is enriched with stored content before forwarding."""
    import services.comms.main as m
    m._bot_webhook = "http://bot:9000/inbound"
    m._ws_connection = None
    m._pending_memory_approvals = {
        "tok-mem-2": {
            "content": "Be concise in responses.",
            "entry_type": "responsibility",
            "group_id": "grp-eng",
        }
    }

    with patch("services.comms.main._http_post", new_callable=AsyncMock) as mock_post:
        ws = _FakeWebSocket([{
            "type": "memory_approved",
            "token": "tok-mem-2",
        }])
        await m.websocket_endpoint(ws)
        await asyncio.sleep(0)
        mock_post.assert_called_once()
        payload = mock_post.call_args[0][1]
        assert payload["type"] == "memory_approved"
        assert payload["token"] == "tok-mem-2"
        assert payload["content"] == "Be concise in responses."
        assert payload["entry_type"] == "responsibility"
        assert payload["group_id"] == "grp-eng"

    # Token should have been consumed (popped)
    assert "tok-mem-2" not in m._pending_memory_approvals
