import httpx
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("COMMS_URL", "http://comms-mock:8001")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    from services.egressguard.main import app, _pending, _allowlist, _blocklist, _STATIC_ALLOW
    # Reset module-level state before each test
    _pending.clear()
    _allowlist.clear()
    _allowlist.update(_STATIC_ALLOW)
    _blocklist.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_allowed_domain_proxies(client, respx_mock):
    respx_mock.get("https://api.openai.com/v1/test").mock(return_value=httpx.Response(200, text="ok"))
    resp = await client.get("/proxy", params={"url": "https://api.openai.com/v1/test"})
    assert resp.status_code == 200


async def test_unknown_domain_returns_202(client):
    resp = await client.get("/proxy", params={"url": "https://unknown-domain-xyz.io/api"})
    assert resp.status_code == 202
    data = resp.json()
    assert "token" in data
    assert data["status"] == "pending"


async def test_poll_pending_token(client):
    resp = await client.get("/proxy", params={"url": "https://unknown-domain-xyz.io/api"})
    token = resp.json()["token"]
    poll = await client.get(f"/status/{token}")
    assert poll.json()["status"] == "pending"


async def test_callback_approves_token(client):
    resp = await client.get("/proxy", params={"url": "https://unknown-domain-xyz.io/api"})
    token = resp.json()["token"]
    callback = await client.post("/callback", json={"token": token, "decision": "approve_always", "hostname": "unknown-domain-xyz.io"})
    assert callback.status_code == 200
    poll = await client.get(f"/status/{token}")
    assert poll.json()["status"] == "approved"


async def test_callback_denies_token(client):
    resp = await client.get("/proxy", params={"url": "https://unknown-domain-xyz.io/api"})
    token = resp.json()["token"]
    await client.post("/callback", json={"token": token, "decision": "deny_always", "hostname": "unknown-domain-xyz.io"})
    poll = await client.get(f"/status/{token}")
    assert poll.json()["status"] == "denied"
