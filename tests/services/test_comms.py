import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    # Ensure module is imported so patch() can resolve it by dotted name
    import services.comms.main  # noqa: F401
    with patch("services.comms.main.AsyncWebClient") as mock_wc:
        mock_wc.return_value.chat_postMessage = AsyncMock(return_value={"ok": True})
        # Reset module-level _slack to the mock instance after patching
        import services.comms.main as comms_main
        comms_main._slack = mock_wc.return_value
        from services.comms.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def test_send_message(client):
    resp = await client.post("/send", json={"channel": "C123", "text": "hello"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_notify_approval(client):
    resp = await client.post(
        "/notify/approval",
        json={"token": "tok123", "hostname": "example.com", "url": "https://example.com/api"}
    )
    assert resp.status_code == 200


async def test_register_bot_webhook(client):
    resp = await client.post("/register", json={"webhook_url": "http://bot:9000/inbound"})
    assert resp.status_code == 200
