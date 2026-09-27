import httpx
import pytest

from shuiyuan_mcp.client import ShuiyuanClient
from shuiyuan_mcp.config import Config


@pytest.fixture
def post():
    return {
        "id": 101,
        "topic_id": 10,
        "post_number": 1,
        "username": "alice",
        "created_at": "2026-01-01T00:00:00Z",
        "raw": "hello **world**",
        "cooked": "<p>hello <strong>world</strong></p>",
        "reply_to_post_number": None,
        "unwanted": "private-data",
    }


@pytest.fixture
async def client_factory():
    clients = []

    def create(handler, config=None):
        client = ShuiyuanClient(
            config or Config(base_url="https://shuiyuan.invalid", user_api_key="test-key"),
            transport=httpx.MockTransport(handler),
        )
        clients.append(client)
        return client

    yield create
    for client in clients:
        await client.aclose()


@pytest.fixture(autouse=True)
def forbid_real_http(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Tests must not use real HTTP; inject httpx.MockTransport")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
