"""Read-only Discourse client with bounded output and sanitized errors."""

import asyncio
import re
from datetime import date
from typing import Literal
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from .config import Config
from .models import (
    APIModel,
    Post,
    SearchResponse,
    Site,
    Tags,
    Topic,
    TopicPosts,
    UserActions,
    UserResponse,
    plain_text,
)

Order = Literal["relevance", "latest", "oldest", "latest_topic", "oldest_topic", "views", "likes"]
Status = Literal["open", "closed", "archived", "noreplies", "single_user"]


class ShuiyuanError(Exception):
    """Safe to return to the MCP host. Never contains response bodies or credentials."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def bounds(name: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ShuiyuanError("Invalid Argument", f"{name} must be {minimum}..{maximum}")


def username_value(value: str) -> str:
    if not re.fullmatch(r"[\w.-]{1,60}", value) or value in {".", ".."}:
        raise ShuiyuanError("Invalid Argument", "username must be a valid Discourse username")
    return value


def build_search_query(
    keyword: str = "",
    username: str | None = None,
    category: str | None = None,
    before: str | None = None,
    after: str | None = None,
    order: Order | None = None,
    status: Status | None = None,
) -> str:
    if len(keyword) > 1000 or any(ord(c) < 32 for c in keyword):
        raise ShuiyuanError(
            "Invalid Argument", "keyword must be at most 1000 characters without controls"
        )
    parts = [keyword.strip()] if keyword.strip() else []
    if username is not None:
        parts.append(f"user:{username_value(username)}")
    if category is not None:
        if not re.fullmatch(r"[\w/.-]{1,100}", category):
            raise ShuiyuanError("Invalid Argument", "category must be an ID or category slug")
        parts.append(f"category:{category}")
    for name, value in (("before", before), ("after", after)):
        if value is not None:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError
                date.fromisoformat(value)
            except ValueError:
                raise ShuiyuanError(
                    "Invalid Argument", f"{name} must be a valid YYYY-MM-DD date"
                ) from None
            parts.append(f"{name}:{value}")
    if before and after and after >= before:
        raise ShuiyuanError("Invalid Argument", "after must precede before")
    if order is not None:
        if order not in {
            "relevance",
            "latest",
            "oldest",
            "latest_topic",
            "oldest_topic",
            "views",
            "likes",
        }:
            raise ShuiyuanError("Invalid Argument", "unsupported order")
        if order != "relevance":
            parts.append(f"order:{order}")
    if status is not None:
        if status not in {"open", "closed", "archived", "noreplies", "single_user"}:
            raise ShuiyuanError("Invalid Argument", "unsupported status")
        parts.append(f"status:{status}")
    if not parts:
        raise ShuiyuanError("Invalid Argument", "provide a keyword or search filter")
    return " ".join(parts)


class ShuiyuanClient:
    def __init__(self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = config.base_url
        headers = {"Accept": "application/json", "User-Agent": "shuiyuan-mcp-lite/0.1.0"}
        if config.user_api_key:
            headers["User-Api-Key"] = config.user_api_key
            if config.user_api_client_id:
                headers["User-Api-Client-Id"] = config.user_api_client_id
        self._http = httpx.AsyncClient(
            base_url=f"{self.base_url}/",
            headers=headers,
            timeout=httpx.Timeout(20, connect=10),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
            follow_redirects=False,
            transport=transport,
        )
        self._semaphore = asyncio.Semaphore(4)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get[T: APIModel](self, path: str, model: type[T], params=None) -> T:
        async with self._semaphore:
            try:
                response = await self._http.get(path.lstrip("/"), params=params)
            except httpx.TimeoutException:
                raise ShuiyuanError(
                    "Timeout", "Shuiyuan request timed out; try again later"
                ) from None
            except httpx.RequestError:
                raise ShuiyuanError("Shuiyuan Unreachable", "could not reach Shuiyuan") from None
        codes = {
            401: ("Authentication Required", "provide a valid SHUIYUAN_USER_API_KEY"),
            403: ("Permission Denied", "the current user cannot access this content"),
            404: ("Not Found", "requested content was not found"),
            429: ("Rate Limited", "Shuiyuan rate limit reached; wait before retrying"),
        }
        if response.status_code in codes:
            raise ShuiyuanError(*codes[response.status_code])
        if response.status_code >= 500:
            raise ShuiyuanError("Shuiyuan Unreachable", "Shuiyuan returned a server error")
        if 300 <= response.status_code < 400:
            location = response.headers.get("location", "")
            if "/login" in location or "jaccount" in location.lower():
                raise ShuiyuanError("Authentication Required", "Shuiyuan redirected to login")
        if response.status_code != 200:
            raise ShuiyuanError("Unexpected API Response", f"HTTP {response.status_code}")
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError):
            raise ShuiyuanError(
                "Unexpected API Response", "invalid JSON or unexpected response schema"
            ) from None

    def _post_url(self, topic_id: int, post_number: int) -> str:
        return f"{self.base_url}/t/{topic_id}/{post_number}"

    async def search(
        self,
        keyword: str = "",
        username: str | None = None,
        category: str | None = None,
        before: str | None = None,
        after: str | None = None,
        order: Order | None = None,
        status: Status | None = None,
        page: int = 1,
        offset: int = 0,
        limit: int = 20,
        max_chars: int = 500,
    ) -> dict:
        bounds("page", page, 1, 10)
        bounds("offset", offset, 0, 1000)
        bounds("limit", limit, 1, 50)
        bounds("max_chars", max_chars, 1, 2000)
        query = build_search_query(keyword, username, category, before, after, order, status)
        data = await self._get("/search.json", SearchResponse, {"q": query, "page": page})
        if data.grouped_search_result.error:
            raise ShuiyuanError("Unexpected API Response", "Shuiyuan could not complete the search")
        titles = {topic.id: topic.title for topic in data.topics}
        results = []
        for post in data.posts[offset : offset + limit]:
            if post.topic_id not in titles:
                raise ShuiyuanError("Unexpected API Response", "search result is missing its topic")
            excerpt = plain_text(post.blurb)
            results.append(
                {
                    "topic_id": post.topic_id,
                    "post_id": post.id,
                    "title": titles[post.topic_id][:300],
                    "username": post.username,
                    "created_at": post.created_at,
                    "excerpt": excerpt[:max_chars],
                    "truncated": len(excerpt) > max_chars,
                    "url": f"{self.base_url}/p/{post.id}",
                }
            )
        end = offset + len(results)
        return {
            "results": results,
            "page": page,
            "offset": offset,
            "next_offset": end if end < len(data.posts) else None,
            "next_page": page + 1
            if data.grouped_search_result.more_full_page_results and page < 10
            else None,
        }

    async def read_topic(
        self, topic_id: int, offset: int = 0, limit: int = 10, max_chars: int = 2000
    ) -> dict:
        bounds("topic_id", topic_id, 1, 2**63 - 1)
        bounds("offset", offset, 0, 2**31 - 1)
        bounds("limit", limit, 1, 50)
        bounds("max_chars", max_chars, 1, 10000)
        topic = await self._get(f"/t/{topic_id}.json", Topic, {"include_raw": "true"})
        if topic.id != topic_id:
            raise ShuiyuanError("Unexpected API Response", "topic ID mismatch")
        selected = topic.post_stream.stream[offset : offset + limit]
        posts = {p.id: p for p in topic.post_stream.posts}
        missing = [post_id for post_id in selected if post_id not in posts]
        # Discourse commonly limits batches to 20 posts. Fetch only the requested window.
        for start in range(0, len(missing), 20):
            params = [("post_ids[]", str(i)) for i in missing[start : start + 20]]
            params.append(("include_raw", "true"))
            extra = await self._get(f"/t/{topic_id}/posts.json", TopicPosts, params)
            posts.update({p.id: p for p in extra.post_stream.posts})
        result = []
        for post_id in selected:
            if post_id not in posts or posts[post_id].topic_id != topic_id:
                raise ShuiyuanError(
                    "Unexpected API Response", "requested topic posts are missing or inconsistent"
                )
            post = posts[post_id]
            content = post.raw if post.raw is not None else plain_text(post.cooked or "")
            result.append(
                {
                    "id": post.id,
                    "post_number": post.post_number,
                    "username": post.username,
                    "created_at": post.created_at,
                    "content": content[:max_chars],
                    "content_format": "markdown" if post.raw is not None else "plain_text",
                    "reply_to_post_number": post.reply_to_post_number,
                    "truncated": len(content) > max_chars,
                    "url": self._post_url(topic_id, post.post_number),
                }
            )
        end = offset + len(selected)
        return {
            "id": topic.id,
            "title": topic.title[:300],
            "category_id": topic.category_id,
            "tags": [t if isinstance(t, str) else t.name for t in topic.tags],
            "posts": result,
            "offset": offset,
            "total_posts": len(topic.post_stream.stream),
            "next_offset": end if end < len(topic.post_stream.stream) else None,
            "url": f"{self.base_url}/t/{topic_id}",
        }

    async def read_post(self, post_id: int, offset: int = 0, max_chars: int = 8000) -> dict:
        bounds("post_id", post_id, 1, 2**63 - 1)
        bounds("offset", offset, 0, 2**31 - 1)
        bounds("max_chars", max_chars, 1, 20000)
        post = await self._get(f"/posts/{post_id}.json", Post, {"include_raw": "true"})
        if post.id != post_id or post.raw is None:
            raise ShuiyuanError(
                "Unexpected API Response", "raw post is missing or ID does not match"
            )
        retorts, polls = post.retorts or [], post.polls or []
        end = min(offset + max_chars, len(post.raw))
        return {
            "id": post.id,
            "topic_id": post.topic_id,
            "post_number": post.post_number,
            "username": post.username,
            "created_at": post.created_at,
            "raw": post.raw[offset:end],
            "reply_to_post_number": post.reply_to_post_number,
            "retorts": [
                {"emoji": r.emoji[:100], "usernames": r.usernames[:100]} for r in retorts[:50]
            ],
            "polls": [
                {
                    "title": plain_text(p.title or p.name)[:300],
                    "options": [
                        {"text": plain_text(o.html)[:500], "votes": o.votes}
                        for o in p.options[:100]
                    ],
                }
                for p in polls[:10]
            ],
            "metadata_truncated": len(retorts) > 50
            or any(len(r.usernames) > 100 for r in retorts)
            or any(len(r.emoji) > 100 for r in retorts)
            or len(polls) > 10
            or any(
                len(p.options) > 100
                or len(plain_text(p.title or p.name)) > 300
                or any(len(plain_text(o.html)) > 500 for o in p.options)
                for p in polls
            ),
            "offset": offset,
            "total_chars": len(post.raw),
            "truncated": offset > 0 or end < len(post.raw),
            "next_offset": end if end < len(post.raw) else None,
            "url": self._post_url(post.topic_id, post.post_number),
        }

    async def get_user(self, username: str, max_chars: int = 2000) -> dict:
        username_value(username)
        bounds("max_chars", max_chars, 1, 10000)
        data = await self._get(f"/u/{quote(username, safe='')}.json", UserResponse)
        user = data.user
        bio = user.bio_raw if user.bio_raw is not None else plain_text(user.bio_cooked or "")
        # Explicit allowlist: never expose email, IP, session or custom user fields.
        return {
            "id": user.id,
            "username": user.username,
            "name": (user.name or "")[:300],
            "title": (user.title or "")[:300],
            "created_at": user.created_at,
            "trust_level": user.trust_level,
            "bio": bio[:max_chars],
            "bio_format": "markdown" if user.bio_raw is not None else "plain_text",
            "truncated": len(bio) > max_chars,
            "location": (user.location or "")[:300],
            "website": (user.website or "")[:1000],
            "url": f"{self.base_url}/u/{quote(user.username, safe='')}",
        }

    async def list_user_posts(
        self, username: str, offset: int = 0, limit: int = 20, max_chars: int = 500
    ) -> dict:
        username_value(username)
        bounds("offset", offset, 0, 2**31 - 1)
        bounds("limit", limit, 1, 50)
        bounds("max_chars", max_chars, 1, 2000)
        data = await self._get(
            "/user_actions.json",
            UserActions,
            {"username": username, "filter": "4,5", "offset": offset, "limit": limit},
        )
        posts = []
        for p in data.user_actions[:limit]:
            excerpt = plain_text(p.excerpt)
            posts.append(
                {
                    "post_id": p.post_id,
                    "topic_id": p.topic_id,
                    "post_number": p.post_number,
                    "username": p.username,
                    "title": p.title[:300],
                    "created_at": p.created_at,
                    "excerpt": excerpt[:max_chars],
                    "truncated": len(excerpt) > max_chars,
                    "reply_to_post_number": p.reply_to_post_number,
                    "url": self._post_url(p.topic_id, p.post_number),
                }
            )
        return {
            "posts": posts,
            "offset": offset,
            "next_offset": offset + len(posts) if len(posts) == limit else None,
        }

    async def categories(self) -> dict:
        data = await self._get("/site.json", Site)
        return {
            "categories": [
                {
                    "id": c.id,
                    "name": c.name[:300],
                    "slug": c.slug[:300],
                    "parent_category_id": c.parent_category_id,
                    "description": (c.description_text or "")[:500],
                }
                for c in data.categories[:500]
            ],
            "truncated": len(data.categories) > 500,
            "total": len(data.categories),
        }

    async def tags(self) -> dict:
        data = await self._get("/tags.json", Tags)
        tags = {t.id: t for t in data.tags}
        for group in data.extras.tag_groups:
            tags.update({t.id: t for t in group.tags})
        visible = list(tags.values())
        return {
            "tags": [{"id": t.id, "name": t.name[:100], "count": t.count} for t in visible[:1000]],
            "truncated": len(visible) > 1000,
            "total": len(visible),
        }
