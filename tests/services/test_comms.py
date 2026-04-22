import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport


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
        from services.comms.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c, mock_wc.return_value


async def test_send_message(client):
    c, mock_slack = client
    resp = await c.post("/send", json={"channel": "C123", "text": "hello"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


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
