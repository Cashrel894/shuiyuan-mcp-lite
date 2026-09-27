import asyncio
import json
import os
import shutil
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl

from shuiyuan_mcp.server import create_server

TOOL_NAMES = {"search", "read_topic", "read_post", "get_user", "list_user_posts"}
RESOURCE_URIS = {"shuiyuan://categories", "shuiyuan://tags"}
ROOT = Path(__file__).resolve().parents[1]


async def test_mcp_all_tools_and_resources(client_factory, post):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        payloads = {
            "/posts/101.json": post,
            "/t/10.json": {
                "id": 10,
                "title": "Topic",
                "post_stream": {"stream": [101], "posts": [post]},
            },
            "/search.json": {
                "posts": [
                    {
                        "id": 101,
                        "topic_id": 10,
                        "username": "alice",
                        "created_at": "now",
                        "blurb": "Hello",
                    }
                ],
                "topics": [{"id": 10, "title": "Topic"}],
                "grouped_search_result": {"more_full_page_results": False},
            },
            "/u/alice.json": {"user": {"id": 1, "username": "alice", "email": "PRIVATE"}},
            "/user_actions.json": {"user_actions": []},
            "/site.json": {"categories": [{"id": 1, "name": "校园", "slug": "campus"}]},
            "/tags.json": {"tags": []},
        }
        return httpx.Response(200, json=payloads[request.url.path])

    client = client_factory(handler)
    async with create_connected_server_and_client_session(create_server(client=client)) as session:
        tools = (await session.list_tools()).tools
        assert {t.name for t in tools} == TOOL_NAMES
        for tool in tools:
            assert tool.annotations.readOnlyHint
            assert not tool.annotations.destructiveHint
            assert "ctx" not in tool.inputSchema["properties"]
        schemas = {t.name: t.inputSchema for t in tools}
        assert schemas["read_topic"]["properties"]["limit"]["maximum"] == 50
        assert {str(r.uri) for r in (await session.list_resources()).resources} == RESOURCE_URIS
        for name, args, expected in [
            ("search", {"keyword": "Hello"}, "results"),
            ("read_topic", {"topic_id": 10}, "posts"),
            ("read_post", {"post_id": 101}, "raw"),
            ("get_user", {"username": "alice"}, "username"),
            ("list_user_posts", {"username": "alice"}, "posts"),
        ]:
            result = await session.call_tool(name, args)
            assert not result.isError, result
            assert expected in result.structuredContent
            assert "PRIVATE" not in str(result)
            assert json.loads(result.content[0].text) == result.structuredContent
        for uri in RESOURCE_URIS:
            result = await session.read_resource(AnyUrl(uri))
            assert result.contents[0].mimeType == "application/json"
            assert uri.split("//")[1] in json.loads(result.contents[0].text)
    assert len(requests) == 7
    assert client._http.is_closed


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
async def test_mcp_errors_are_safe(client_factory, status):
    client = client_factory(lambda r: httpx.Response(status, text="TOKEN-SECRET"))
    async with create_connected_server_and_client_session(create_server(client=client)) as session:
        result = await session.call_tool("read_post", {"post_id": 101})
        assert result.isError
        assert "Traceback" not in str(result) and "TOKEN-SECRET" not in str(result)
        assert "Error" in result.content[0].text or "error" in result.content[0].text
        with pytest.raises(McpError) as exc:
            await session.read_resource(AnyUrl("shuiyuan://tags"))
        assert "TOKEN-SECRET" not in str(exc.value) and "Traceback" not in str(exc.value)


async def test_mcp_invalid_args(client_factory):
    def handler(request):
        pytest.fail("invalid MCP inputs must not cause HTTP requests")

    async with create_connected_server_and_client_session(
        create_server(client=client_factory(handler))
    ) as session:
        for name, args in [
            ("read_post", {"post_id": -1}),
            ("read_topic", {"topic_id": 1, "limit": 1000}),
            ("search", {"keyword": "x", "page": 0}),
            ("get_user", {"username": "../admin"}),
        ]:
            result = await session.call_tool(name, args)
            assert result.isError
            assert "Traceback" not in str(result)


async def test_uv_stdio_startup(tmp_path):
    """Launch the exact documented CLI; no valid tool call or network request."""
    uv = shutil.which("uv")
    assert uv, "uv is required for the documented CLI acceptance test"
    params = StdioServerParameters(
        command=uv,
        args=["run", "--offline", "shuiyuan-mcp"],
        cwd=str(ROOT),
        env={
            "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(tmp_path / "uv-cache")),
            "SHUIYUAN_BASE_URL": "https://shuiyuan.invalid",
            "SHUIYUAN_USER_API_KEY": "",
            "SHUIYUAN_USER_API_CLIENT_ID": "",
            "SHUIYUAN_COOKIE_FILE": str(tmp_path / "missing-cookies.json"),
        },
    )
    with (tmp_path / "stderr.log").open("w+") as stderr:
        async with asyncio.timeout(20):
            async with stdio_client(params, errlog=stderr) as (read, write):
                async with ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=10)
                ) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "shuiyuan-mcp"
                    assert {t.name for t in (await session.list_tools()).tools} == TOOL_NAMES
                    assert {
                        str(r.uri) for r in (await session.list_resources()).resources
                    } == RESOURCE_URIS
                    result = await session.call_tool("read_post", {"post_id": -1})
                    assert result.isError
                    missing_auth = await session.call_tool("read_post", {"post_id": 101})
                    assert missing_auth.isError
                    assert "auth login" in missing_auth.content[0].text
        stderr.seek(0)
        assert "Failed to parse JSONRPC" not in stderr.read()
