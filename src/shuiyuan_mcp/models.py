"""Validate API fields that we use; discard all other fields."""

from html.parser import HTMLParser
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositiveId = Annotated[int, Field(gt=0)]


class APIModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class Retort(APIModel):
    emoji: str
    usernames: list[str] = Field(default_factory=list)


class PollOption(APIModel):
    html: str
    # Missing means results are hidden, not zero votes.
    votes: Annotated[int, Field(ge=0)] | None = None


class Poll(APIModel):
    title: str | None = None
    name: str = "poll"
    options: list[PollOption]


class Post(APIModel):
    id: PositiveId
    topic_id: PositiveId
    post_number: PositiveId
    username: str
    created_at: str
    raw: str | None = None
    cooked: str | None = None
    reply_to_post_number: PositiveId | None = None
    retorts: list[Retort] | None = None
    polls: list[Poll] | None = None

    @model_validator(mode="after")
    def require_content(self) -> "Post":
        if self.raw is None and self.cooked is None:
            raise ValueError("post has no content")
        return self


class PostStream(APIModel):
    posts: list[Post]


class TopicStream(PostStream):
    stream: list[PositiveId]


class TopicTag(APIModel):
    name: str


class Topic(APIModel):
    id: PositiveId
    title: str
    category_id: PositiveId | None = None
    tags: list[str | TopicTag] = Field(default_factory=list)
    post_stream: TopicStream


class TopicPosts(APIModel):
    post_stream: PostStream


class SearchPost(APIModel):
    id: PositiveId
    topic_id: PositiveId
    username: str
    created_at: str
    blurb: str


class SearchTopic(APIModel):
    id: PositiveId
    title: str


class SearchMeta(APIModel):
    more_full_page_results: bool | None = None
    error: str | None = None


class SearchResponse(APIModel):
    posts: list[SearchPost]
    topics: list[SearchTopic] = Field(default_factory=list)
    grouped_search_result: SearchMeta


class User(APIModel):
    id: int  # Discourse system users may have negative IDs.
    username: str
    name: str | None = None
    title: str | None = None
    avatar_template: str | None = None
    created_at: str | None = None
    trust_level: int | None = None
    bio_raw: str | None = None
    bio_cooked: str | None = None
    location: str | None = None
    website: str | None = None


class UserResponse(APIModel):
    user: User


class UserAction(APIModel):
    post_id: PositiveId
    topic_id: PositiveId
    post_number: PositiveId
    username: str
    created_at: str
    title: str
    excerpt: str
    reply_to_post_number: PositiveId | None = None


class UserActions(APIModel):
    user_actions: list[UserAction]


class Category(APIModel):
    id: PositiveId
    name: str
    slug: str
    parent_category_id: PositiveId | None = None
    description_text: str | None = None


class Site(APIModel):
    categories: list[Category]


class Tag(APIModel):
    id: int | str
    name: str
    count: Annotated[int, Field(ge=0)] | None = None


class TagGroup(APIModel):
    tags: list[Tag]


class TagExtras(APIModel):
    tag_groups: list[TagGroup] = Field(default_factory=list)


class Tags(APIModel):
    tags: list[Tag]
    extras: TagExtras = Field(default_factory=TagExtras)


class _TextParser(HTMLParser):
    """Extract readable fallback text from API-supplied HTML (never scrape pages)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden:
            if tag in {"p", "div", "br", "li", "blockquote", "pre"}:
                self.parts.append("\n")
            elif tag == "img":
                self.parts.append(dict(attrs).get("alt") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in {"p", "div", "li", "blockquote", "pre"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: str) -> str:
    parser = _TextParser()
    parser.feed(value)
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
