import asyncio
import inspect

from things3_mcp.fast_server import (
    CacheKeys,
    add_task,
    invalidate_all_caches,
    invalidate_tags_cache,
    invalidate_todos_cache,
)


class FakeContext:
    def __init__(self) -> None:
        self.state: dict[str, object] = {}

    async def get_state(self, key: str) -> object | None:
        return self.state.get(key)

    async def set_state(self, key: str, value: object | None) -> None:
        self.state[key] = value


def test_invalidate_todos_cache_clears_project_area_tag_caches() -> None:
    ctx = FakeContext()
    ctx.state[CacheKeys.INBOX_RESPONSE] = "inbox"
    ctx.state[CacheKeys.projects_raw(True)] = "projects-raw"
    ctx.state[CacheKeys.projects_response(True)] = "projects-response"
    ctx.state[CacheKeys.areas_raw(True)] = "areas-raw"
    ctx.state[CacheKeys.areas_response(True)] = "areas-response"
    ctx.state[CacheKeys.tags_response(True)] = "tags-response"
    ctx.state[CacheKeys.search_raw("tags_raw")] = ["tag-a"]

    asyncio.run(invalidate_todos_cache(ctx))

    assert ctx.state[CacheKeys.INBOX_RESPONSE] is None
    assert ctx.state[CacheKeys.projects_raw(True)] is None
    assert ctx.state[CacheKeys.projects_response(True)] is None
    assert ctx.state[CacheKeys.areas_raw(True)] is None
    assert ctx.state[CacheKeys.areas_response(True)] is None
    assert ctx.state[CacheKeys.tags_response(True)] is None
    assert ctx.state[CacheKeys.search_raw("tags_raw")] is None


def test_invalidate_tags_cache_clears_tag_responses_and_raw() -> None:
    ctx = FakeContext()
    ctx.state[CacheKeys.tags_response(False)] = "tags-response"
    ctx.state[CacheKeys.search_raw("tags_raw")] = ["tag-b"]

    asyncio.run(invalidate_tags_cache(ctx))

    assert ctx.state[CacheKeys.tags_response(False)] is None
    assert ctx.state[CacheKeys.search_raw("tags_raw")] is None


def test_invalidate_all_caches_clears_list_views() -> None:
    ctx = FakeContext()
    ctx.state[CacheKeys.INBOX_RESPONSE] = "inbox"
    ctx.state[CacheKeys.TODAY_RESPONSE] = "today"
    ctx.state[CacheKeys.UPCOMING_RESPONSE] = "upcoming"
    ctx.state[CacheKeys.ANYTIME_RESPONSE] = "anytime"
    ctx.state[CacheKeys.SOMEDAY_RESPONSE] = "someday"
    ctx.state[CacheKeys.TRASH_RESPONSE] = "trash"

    asyncio.run(invalidate_all_caches(ctx))

    assert ctx.state[CacheKeys.INBOX_RESPONSE] is None
    assert ctx.state[CacheKeys.TODAY_RESPONSE] is None
    assert ctx.state[CacheKeys.UPCOMING_RESPONSE] is None
    assert ctx.state[CacheKeys.ANYTIME_RESPONSE] is None
    assert ctx.state[CacheKeys.SOMEDAY_RESPONSE] is None
    assert ctx.state[CacheKeys.TRASH_RESPONSE] is None


def test_add_task_ctx_is_not_keyword_only() -> None:
    parameter = inspect.signature(add_task).parameters["ctx"]
    assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
