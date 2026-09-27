import asyncio
from contextlib import aclosing

import httpx
import pytest

from shuiyuan_mcp.client import ShuiyuanError, build_search_query
from shuiyuan_mcp.config import Config


def test_search_query():
    assert build_search_query(
        "选课", "alice", "campus", "2026-02-01", "2026-01-01", "latest", "open"
    ) == (
        "选课 user:alice category:campus before:2026-02-01 "
        "after:2026-01-01 order:latest status:open"
    )
    assert build_search_query(username="alice") == "user:alice"
    assert build_search_query("hello", order="relevance") == "hello"
    assert build_search_query("hello", category="parent/child") == "hello category:parent/child"


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"keyword": "\x00"},
        {"keyword": "x" * 1001},
        {"before": "2026-02-30"},
        {"before": "20260101"},
        {"username": "x status:open"},
        {"category": "x order:likes"},
        {"order": "bad"},
        {"status": "bad"},
        {"before": "2026-01-01", "after": "2026-02-01"},
    ],
)
def test_bad_query(kwargs):
    with pytest.raises(ShuiyuanError, match="Invalid Argument"):
        build_search_query(**kwargs)


async def test_search_normalization_and_pagination(client_factory):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/search.json"
        assert request.url.params["q"] == "hello user:alice"
        assert request.url.params["page"] == "2"
        return httpx.Response(
            200,
            json={
                "posts": [
                    {
                        "id": i,
                        "topic_id": 10,
                        "username": "alice",
                        "created_at": "now",
                        "blurb": "<b>Hello</b> &amp; goodbye",
                        "secret": "hidden",
                    }
                    for i in range(1, 4)
                ],
                "topics": [{"id": 10, "title": "Test"}],
                "grouped_search_result": {"more_full_page_results": True},
            },
        )

    async with aclosing(client_factory(handler)) as client:
        data = await client.search(
            "hello", username="alice", page=2, offset=1, limit=1, max_chars=5
        )
    assert data["next_offset"] == 2 and data["next_page"] == 3
    assert data["results"][0] == {
        "post_id": 2,
        "topic_id": 10,
        "title": "Test",
        "username": "alice",
        "created_at": "now",
        "excerpt": "Hello",
        "truncated": True,
        "url": "https://shuiyuan.invalid/p/2",
    }


async def test_empty_search(client_factory):
    async with aclosing(
        client_factory(
            lambda r: httpx.Response(
                200,
                json={
                    "posts": [],
                    "topics": [],
                    "grouped_search_result": {"more_full_page_results": False},
                },
            )
        )
    ) as client:
        result = await client.search("nothing")
    assert result["results"] == []
    assert result["next_page"] is None and result["next_offset"] is None


async def test_read_post_normalization(client_factory, post):
    post.update(
        retorts=[{"emoji": "heart", "usernames": ["bob"], "post_id": 101}],
        polls=[
            {
                "title": "<b>Lunch?</b>",
                "options": [{"html": "<p>Rice &amp; soup</p>", "votes": 10}, {"html": "Hidden"}],
                "voters": 50,
            }
        ],
        polls_votes={"poll": ["private-vote"]},
    )

    def handler(request):
        assert request.url.path == "/posts/101.json"
        assert request.url.params["include_raw"] == "true"
        return httpx.Response(200, json=post)

    async with aclosing(client_factory(handler)) as client:
        result = await client.read_post(101, max_chars=5)
        rest = await client.read_post(101, offset=result["next_offset"])
    assert result["raw"] + rest["raw"] == post["raw"]
    assert result["truncated"] and rest["next_offset"] is None
    assert result["retorts"] == [{"emoji": "heart", "usernames": ["bob"]}]
    assert result["polls"] == [
        {
            "title": "Lunch?",
            "options": [{"text": "Rice & soup", "votes": 10}, {"text": "Hidden", "votes": None}],
        }
    ]
    assert "polls_votes" not in result and "unwanted" not in result


@pytest.mark.parametrize(
    "extras", [{}, {"retorts": None, "polls": None}, {"retorts": [], "polls": []}]
)
async def test_missing_plugins(client_factory, post, extras):
    post.update(extras)
    async with aclosing(client_factory(lambda r: httpx.Response(200, json=post))) as client:
        result = await client.read_post(101)
    assert result["retorts"] == result["polls"] == []
    assert not result["metadata_truncated"]


async def test_topic_crosses_initial_window(client_factory, post):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/t/10.json":
            return httpx.Response(
                200,
                json={
                    "id": 10,
                    "title": "Topic",
                    "category_id": 2,
                    "tags": ["campus"],
                    "post_stream": {"stream": [101, 102, 103, 104], "posts": [post]},
                },
            )
        assert request.url.path == "/t/10/posts.json"
        assert request.url.params.get_list("post_ids[]") == ["102", "103"]
        assert request.url.params["include_raw"] == "true"
        return httpx.Response(
            200,
            json={
                "post_stream": {
                    "posts": [
                        {**post, "id": 103, "post_number": 5, "raw": "third"},
                        {
                            **post,
                            "id": 102,
                            "post_number": 3,
                            "raw": None,
                            "cooked": "<p>second &amp; reply</p>",
                            "reply_to_post_number": 1,
                        },
                    ]
                }
            },
        )

    async with aclosing(client_factory(handler)) as client:
        result = await client.read_topic(10, offset=1, limit=2, max_chars=6)
    assert len(requests) == 2
    assert result["tags"] == ["campus"] and result["category_id"] == 2
    assert result["next_offset"] == 3 and result["total_posts"] == 4
    assert [p["id"] for p in result["posts"]] == [102, 103]
    assert result["posts"][0]["content"] == "second"
    assert result["posts"][0]["reply_to_post_number"] == 1
    assert result["posts"][0]["content_format"] == "plain_text"
    assert result["posts"][0]["truncated"]
    assert result["posts"][1]["content_format"] == "markdown"
    assert all("unwanted" not in p for p in result["posts"])


async def test_topic_batches_and_out_of_range(client_factory, post):
    batches = []

    def handler(request):
        if request.url.path == "/t/10.json":
            return httpx.Response(
                200,
                json={
                    "id": 10,
                    "title": "Topic",
                    "post_stream": {"stream": list(range(101, 161)), "posts": [post]},
                },
            )
        ids = [int(i) for i in request.url.params.get_list("post_ids[]")]
        batches.append(ids)
        return httpx.Response(
            200,
            json={
                "post_stream": {"posts": [{**post, "id": i, "post_number": i - 100} for i in ids]}
            },
        )

    async with aclosing(client_factory(handler)) as client:
        result = await client.read_topic(10, limit=50)
        empty = await client.read_topic(10, offset=100)
    assert [len(b) for b in batches] == [20, 20, 9]
    assert len(result["posts"]) == 50 and result["next_offset"] == 50
    assert empty["posts"] == [] and empty["next_offset"] is None


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "Authentication Required"),
        (403, "Permission Denied"),
        (404, "Not Found"),
        (429, "Rate Limited"),
        (500, "Shuiyuan Unreachable"),
        (503, "Shuiyuan Unreachable"),
        (418, "Unexpected API Response"),
        (302, "Unexpected API Response"),
    ],
)
async def test_http_errors_no_retry_or_leak(client_factory, status, code):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="TOKEN-SECRET traceback", headers={"Retry-After": "10"})

    async with aclosing(client_factory(handler)) as client:
        with pytest.raises(ShuiyuanError, match=code) as exc:
            await client.read_post(101)
    assert len(requests) == 1
    assert "TOKEN-SECRET" not in str(exc.value)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.ConnectError,
        httpx.RemoteProtocolError,
        httpx.PoolTimeout,
    ],
)
async def test_transport_errors(client_factory, error):
    def handler(request):
        raise error("credential-secret", request=request)

    async with aclosing(client_factory(handler)) as client:
        with pytest.raises(ShuiyuanError) as exc:
            await client.read_post(101)
    assert exc.value.code == (
        "Timeout" if issubclass(error, httpx.TimeoutException) else "Shuiyuan Unreachable"
    )
    assert "credential-secret" not in str(exc.value)


@pytest.mark.parametrize(
    "payload", [[], {}, {"id": "bad"}, {"id": 101, "raw": 42}, {"errors": ["secret"]}]
)
async def test_invalid_schema(client_factory, payload):
    async with aclosing(client_factory(lambda r: httpx.Response(200, json=payload))) as client:
        with pytest.raises(ShuiyuanError, match="Unexpected API Response"):
            await client.read_post(101)


async def test_html_response_and_login_redirect(client_factory):
    for response, code in [
        (httpx.Response(200, text="<html>login</html>"), "Unexpected API Response"),
        (
            httpx.Response(302, headers={"Location": "https://jaccount.sjtu.edu.cn/login"}),
            "Authentication Required",
        ),
    ]:
        async with aclosing(client_factory(lambda r, response=response: response)) as client:
            with pytest.raises(ShuiyuanError, match=code):
                await client.read_post(101)


@pytest.mark.parametrize(
    "config",
    [
        Config(base_url="https://shuiyuan.invalid"),
        Config(
            base_url="https://shuiyuan.invalid/forum/",
            user_api_key="secret",
            user_api_client_id="client",
        ),
    ],
)
async def test_headers(client_factory, config, post):
    def handler(request):
        assert request.method == "GET"
        assert request.headers["Accept"] == "application/json"
        assert request.headers["User-Agent"].startswith("shuiyuan-mcp-lite/")
        assert "Api-Key" not in request.headers and "Cookie" not in request.headers
        if config.user_api_key:
            assert request.headers["User-Api-Key"] == "secret"
            assert request.headers["User-Api-Client-Id"] == "client"
            assert request.url.path == "/forum/posts/101.json"
        else:
            assert "User-Api-Key" not in request.headers
        return httpx.Response(200, json=post)

    async with aclosing(client_factory(handler, config)) as client:
        await client.read_post(101)


async def test_user_allowlist(client_factory):
    def handler(request):
        assert request.url.path == "/u/alice.json"
        return httpx.Response(
            200,
            json={
                "user": {
                    "id": 1,
                    "username": "alice",
                    "name": "Alice",
                    "bio_raw": "Biography",
                    "email": "private@example.com",
                    "ip_address": "1.2.3.4",
                    "custom_fields": {"secret": "secret"},
                }
            },
        )

    async with aclosing(client_factory(handler)) as client:
        result = await client.get_user("alice", max_chars=3)
    assert result["bio"] == "Bio" and result["truncated"]
    assert "private" not in str(result) and "secret" not in str(result) and "email" not in result


async def test_user_posts(client_factory):
    def handler(request):
        assert request.url.path == "/user_actions.json"
        assert dict(request.url.params) == {
            "username": "alice",
            "filter": "4,5",
            "offset": "20",
            "limit": "1",
        }
        return httpx.Response(
            200,
            json={
                "user_actions": [
                    {
                        "post_id": 101,
                        "topic_id": 10,
                        "post_number": 3,
                        "username": "alice",
                        "created_at": "now",
                        "title": "Title",
                        "excerpt": "<p>Hello</p>",
                        "extra": "secret",
                    }
                ]
            },
        )

    async with aclosing(client_factory(handler)) as client:
        result = await client.list_user_posts("alice", offset=20, limit=1, max_chars=2)
    assert result["next_offset"] == 21
    assert result["posts"][0]["excerpt"] == "He"
    assert "secret" not in str(result)


async def test_resources(client_factory):
    def handler(request):
        if request.url.path == "/site.json":
            return httpx.Response(
                200,
                json={
                    "categories": [
                        {
                            "id": 1,
                            "name": "校园",
                            "slug": "campus",
                            "description_text": "Hi",
                            "secret": "secret",
                        }
                    ],
                    "users": [{"email": "secret"}],
                },
            )
        assert request.url.path == "/tags.json"
        return httpx.Response(
            200, json={"tags": [{"id": "test", "name": "test", "count": 2, "extra": "secret"}]}
        )

    async with aclosing(client_factory(handler)) as client:
        categories = await client.categories()
        tags = await client.tags()
    assert categories["categories"][0]["name"] == "校园"
    assert tags["tags"] == [{"id": "test", "name": "test", "count": 2}]
    assert "secret" not in str(categories) + str(tags)


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("read_post", {"post_id": 0}),
        ("read_post", {"post_id": 1, "max_chars": 0}),
        ("read_topic", {"topic_id": 1, "limit": 51}),
        ("read_topic", {"topic_id": 1, "offset": -1}),
        ("search", {"keyword": "x", "page": 11}),
        ("get_user", {"username": "../admin"}),
        ("list_user_posts", {"username": "alice", "limit": 0}),
    ],
)
async def test_invalid_args_do_not_request(client_factory, method, kwargs):
    def handler(request):
        pytest.fail("invalid arguments must not make requests")

    async with aclosing(client_factory(handler)) as client:
        with pytest.raises(ShuiyuanError, match="Invalid Argument"):
            await getattr(client, method)(**kwargs)


async def test_concurrency_is_bounded(client_factory, post):
    active = peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json=post)

    async with aclosing(client_factory(handler)) as client:
        await asyncio.gather(*(client.read_post(101) for _ in range(12)))
    assert peak == 4


async def test_nullable_search_pagination_and_absent_empty_topics(client_factory):
    async with aclosing(
        client_factory(
            lambda r: httpx.Response(
                200,
                json={
                    "posts": [],
                    "grouped_search_result": {"more_full_page_results": None},
                },
            )
        )
    ) as client:
        result = await client.search("none")
    assert result["results"] == [] and result["next_page"] is None


async def test_topic_object_tags(client_factory, post):
    async with aclosing(
        client_factory(
            lambda r: httpx.Response(
                200,
                json={
                    "id": 10,
                    "title": "Topic",
                    "tags": [{"id": 1, "name": "campus", "slug": "campus"}],
                    "post_stream": {"posts": [post], "stream": [101]},
                },
            )
        )
    ) as client:
        result = await client.read_topic(10)
    assert result["tags"] == ["campus"]
    assert result["posts"][0]["content"] == post["raw"]
    assert result["next_offset"] is None


async def test_grouped_tags_and_resource_limits(client_factory):
    def handler(request):
        if request.url.path == "/site.json":
            return httpx.Response(
                200,
                json={
                    "categories": [
                        {"id": i, "name": "campus", "slug": "campus", "description_text": "x" * 600}
                        for i in range(1, 502)
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "tags": [{"id": 1, "name": "one", "count": 2}],
                "extras": {
                    "tag_groups": [
                        {"tags": [{"id": i, "name": str(i), "count": 2} for i in range(1, 1002)]}
                    ]
                },
            },
        )

    async with aclosing(client_factory(handler)) as client:
        tags = await client.tags()
        categories = await client.categories()
    assert tags["total"] == 1001 and tags["truncated"] and len(tags["tags"]) == 1000
    assert categories["total"] == 501 and categories["truncated"]
    assert len(categories["categories"]) == 500
    assert len(categories["categories"][0]["description"]) == 500


async def test_post_metadata_limits(client_factory, post):
    post.update(
        retorts=[{"emoji": "heart", "usernames": ["alice"] * 101}] * 51,
        polls=[{"title": "x" * 301, "options": [{"html": "x" * 501, "votes": 1}] * 101}] * 11,
    )
    async with aclosing(client_factory(lambda r: httpx.Response(200, json=post))) as client:
        result = await client.read_post(101)
    assert result["metadata_truncated"]
    assert len(result["retorts"]) == 50 and len(result["retorts"][0]["usernames"]) == 100
    assert len(result["polls"]) == 10 and len(result["polls"][0]["options"]) == 100
    assert len(result["polls"][0]["title"]) == 300
    assert len(result["polls"][0]["options"][0]["text"]) == 500


@pytest.mark.parametrize(
    ("method", "args", "payload"),
    [
        ("search", {"keyword": "x"}, {"posts": "wrong", "topics": [], "grouped_search_result": {}}),
        (
            "read_topic",
            {"topic_id": 10},
            {"id": 10, "title": "Bad", "post_stream": {"posts": [], "stream": "wrong"}},
        ),
        ("get_user", {"username": "alice"}, {"user": {"id": 1}}),
        ("list_user_posts", {"username": "alice"}, {"user_actions": [{}]}),
        ("categories", {}, {"categories": [42]}),
        ("tags", {}, {"tags": [{"name": "no ID"}]}),
    ],
)
async def test_endpoint_schema_failures(client_factory, method, args, payload):
    async with aclosing(client_factory(lambda r: httpx.Response(200, json=payload))) as client:
        with pytest.raises(ShuiyuanError, match="Unexpected API Response"):
            await getattr(client, method)(**args)


async def test_topic_missing_requested_posts(client_factory):
    def handler(request):
        if request.url.path == "/t/10.json":
            return httpx.Response(
                200, json={"id": 10, "title": "Topic", "post_stream": {"posts": [], "stream": [1]}}
            )
        return httpx.Response(200, json={"post_stream": {"posts": []}})

    async with aclosing(client_factory(handler)) as client:
        with pytest.raises(ShuiyuanError, match="missing or inconsistent"):
            await client.read_topic(10)


async def test_redirect_does_not_forward_credentials(client_factory):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.invalid/posts/101.json"})

    config = Config(base_url="https://shuiyuan.invalid", user_api_key="top-secret")
    async with aclosing(client_factory(handler, config)) as client:
        with pytest.raises(ShuiyuanError, match="Unexpected API Response"):
            await client.read_post(101)
    assert len(requests) == 1 and requests[0].url.host == "shuiyuan.invalid"
