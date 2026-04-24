import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from starlette.testclient import TestClient as SyncClient


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
        comms_main._pending_approvals = {}
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


def test_ws_receive_message_dispatches_to_webhook():
    """Browser message over WS is forwarded to bot webhook."""
    import services.comms.main as m
    m._bot_webhook = "http://bot:9000/inbound"
    m._ws_connection = None

    with patch("services.comms.main._http_post", new_callable=AsyncMock) as mock_post:
        with SyncClient(m.app) as c:
            with c.websocket_connect("/ws") as ws:
                ws.send_json({
                    "type": "message",
                    "group_id": "grp-1",
                    "text": "Hello"
                })
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
