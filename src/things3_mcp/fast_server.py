"""Things MCP Server implementation using the FastMCP pattern."""

import asyncio
import hashlib
import json
import random
import traceback
from collections.abc import Callable
from typing import Any

import things
from fastmcp import Context, FastMCP

from .applescript_bridge import (
    add_project,
    add_todo,
    ensure_things_ready,
    update_project,
    update_todo,
)
from .formatters import format_area, format_project, format_tag, format_todo
from .logging_config import (
    get_logger,
    log_operation_end,
    log_operation_start,
    setup_logging,
)

# Configure enhanced logging
setup_logging(console_level="INFO", file_level="DEBUG", structured_logs=True)
logger = get_logger(__name__)


def preprocess_array_params(**kwargs):
    """Preprocess parameters to handle MCP framework array serialization issues.

    The MCP framework sometimes passes arrays as strings (e.g., '["tag1", "tag2"]')
    instead of actual arrays. This function detects and parses such cases.
    """
    result = {}
    for key, value in kwargs.items():
        if value is None:
            result[key] = None
        elif isinstance(value, str) and value.startswith("[") and value.endswith("]"):
            # Looks like a stringified array, try to parse it
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    result[key] = parsed
                    logger.debug(f"Parsed stringified array for {key}: {value} -> {parsed}")
                else:
                    result[key] = value
            except (json.JSONDecodeError, ValueError):
                # If parsing fails, keep as string
                result[key] = value
                logger.warning(f"Failed to parse potential array parameter {key}: {value}")
        else:
            result[key] = value
    return result


# =============================================================================
# CACHE KEY MANAGEMENT
# =============================================================================
# Centralized cache key definitions to prevent key mismatches in invalidation.
# All cache keys should be defined here and used via these constants/functions.

# Maximum number of cached responses to keep (FIFO eviction).
# Sizing rationale: 500 entries covers typical session usage patterns:
# - ~10-20 unique search queries
# - ~50-100 tagged item views
# - ~30 recent/period views
# Each entry is mostly formatted text (a few KB), so total memory is modest (~1-5 MB).
MAX_RESPONSE_CACHE_SIZE = 500

# Maximum length for cache keys before hashing (to avoid excessively long keys)
MAX_CACHE_KEY_LENGTH = 100


class CacheKeys:
    """Centralized cache key definitions to prevent key mismatches."""

    # Raw data caches (for internal lookups)
    AREAS_LOOKUP = "areas_cache"  # Simple list for area_title resolution
    PROJECTS_LOOKUP = "projects_cache"  # Simple list for list_title resolution

    # Cache tracking key (for size limits)
    CACHE_KEYS_LIST = "_cache_keys_list"

    # Raw data caches with include_items variants
    @staticmethod
    def projects_raw(include_items: bool) -> str:
        """Cache key for raw projects data."""
        return f"projects_raw_{include_items}"

    @staticmethod
    def areas_raw(include_items: bool) -> str:
        """Cache key for raw areas data."""
        return f"areas_raw_{include_items}"

    # Formatted response caches
    @staticmethod
    def projects_response(include_items: bool) -> str:
        """Cache key for formatted projects response."""
        return f"projects_response_{include_items}"

    @staticmethod
    def areas_response(include_items: bool) -> str:
        """Cache key for formatted areas response."""
        return f"areas_response_{include_items}"

    @staticmethod
    def tags_response(include_items: bool) -> str:
        """Cache key for formatted tags response."""
        return f"tags_response_{include_items}"

    @staticmethod
    def tagged_items_response(tag: str) -> str:
        """Cache key for formatted tagged items response."""
        return f"tagged_items_response_{tag}"

    @staticmethod
    def recent_response(period: str) -> str:
        """Cache key for formatted recent items response."""
        return f"recent_response_{period}"

    @staticmethod
    def search_todos_response(query: str) -> str:
        """Cache key for formatted search todos response."""
        base = f"search_todos_response_{query}"
        if len(base) > MAX_CACHE_KEY_LENGTH:
            hash_suffix = hashlib.sha256(query.encode()).hexdigest()[:16]
            return f"search_todos_response_hash_{hash_suffix}"
        return base

    @staticmethod
    def search_advanced_response(cache_key: str) -> str:
        """Cache key for formatted advanced search response."""
        base = f"search_advanced_response_{cache_key}"
        if len(base) > MAX_CACHE_KEY_LENGTH:
            hash_suffix = hashlib.sha256(cache_key.encode()).hexdigest()[:16]
            return f"search_advanced_response_hash_{hash_suffix}"
        return base

    # Individual search cache keys (avoid shared dict race condition)
    @staticmethod
    def search_raw(key: str) -> str:
        """Cache key for raw search data (individual keys to avoid race conditions)."""
        base = f"search_raw_{key}"
        if len(base) > MAX_CACHE_KEY_LENGTH:
            hash_suffix = hashlib.sha256(key.encode()).hexdigest()[:16]
            return f"search_raw_hash_{hash_suffix}"
        return base


async def _track_cache_key(ctx: Context, key: str) -> None:
    """Track cache keys for FIFO eviction when limit is exceeded.

    Note: This function assumes single-threaded execution within an MCP session.
    FastMCP processes requests sequentially per session, so there's no concurrent
    access to the keys list within a session. If concurrent access becomes possible
    in future FastMCP versions, this would need locking or atomic operations.
    """
    keys_list = await ctx.get_state(CacheKeys.CACHE_KEYS_LIST)
    if keys_list is None:
        keys_list = []

    # Add key if not already tracked
    if key not in keys_list:
        keys_list.append(key)

    # Evict oldest keys if over limit (FIFO - first added, first removed)
    while len(keys_list) > MAX_RESPONSE_CACHE_SIZE:
        old_key = keys_list.pop(0)
        await ctx.set_state(old_key, None)
        logger.debug("[CACHE] Evicted old cache key: %s", old_key)

    await ctx.set_state(CacheKeys.CACHE_KEYS_LIST, keys_list)


# =============================================================================
# CACHE INVALIDATION
# =============================================================================


async def invalidate_all_caches(ctx: Context) -> None:
    """Invalidate all caches. Use sparingly - prefer targeted invalidation."""
    keys_list = await ctx.get_state(CacheKeys.CACHE_KEYS_LIST)
    if keys_list:
        for key in keys_list:
            await ctx.set_state(key, None)
        await ctx.set_state(CacheKeys.CACHE_KEYS_LIST, [])

    # Also clear the lookup caches
    await ctx.set_state(CacheKeys.AREAS_LOOKUP, None)
    await ctx.set_state(CacheKeys.PROJECTS_LOOKUP, None)
    logger.debug("[CACHE] Invalidated all caches")


async def invalidate_projects_cache(ctx: Context) -> None:
    """Invalidate all project-related caches."""
    # Clear lookup cache
    await ctx.set_state(CacheKeys.PROJECTS_LOOKUP, None)
    # Clear raw data caches (both include_items variants)
    await ctx.set_state(CacheKeys.projects_raw(True), None)
    await ctx.set_state(CacheKeys.projects_raw(False), None)
    # Clear response caches
    await ctx.set_state(CacheKeys.projects_response(True), None)
    await ctx.set_state(CacheKeys.projects_response(False), None)
    logger.debug("[CACHE] Invalidated projects caches")


async def invalidate_areas_cache(ctx: Context) -> None:
    """Invalidate all area-related caches."""
    await ctx.set_state(CacheKeys.AREAS_LOOKUP, None)
    await ctx.set_state(CacheKeys.areas_raw(True), None)
    await ctx.set_state(CacheKeys.areas_raw(False), None)
    await ctx.set_state(CacheKeys.areas_response(True), None)
    await ctx.set_state(CacheKeys.areas_response(False), None)
    logger.debug("[CACHE] Invalidated areas caches")


async def invalidate_todos_cache(ctx: Context) -> None:
    """Invalidate caches that may contain todo data.

    This is broader because todos appear in search, recent, tagged items, etc.
    We invalidate the search_cache and response caches that might be stale.
    """
    keys_list = await ctx.get_state(CacheKeys.CACHE_KEYS_LIST)
    if not keys_list:
        return

    # Identify keys to invalidate (O(n) scan)
    prefixes_to_clear = ("search_", "recent_", "tagged_items_")
    keys_to_clear = [key for key in keys_list if any(key.startswith(p) for p in prefixes_to_clear)]

    # Clear the cache entries
    for key in keys_to_clear:
        await ctx.set_state(key, None)
        logger.debug("[CACHE] Invalidated todo-related cache: %s", key)

    # Update the tracking list (O(n) list comprehension, not O(n²) remove calls)
    if keys_to_clear:
        keys_to_clear_set = set(keys_to_clear)  # O(1) lookups
        updated_keys = [k for k in keys_list if k not in keys_to_clear_set]
        await ctx.set_state(CacheKeys.CACHE_KEYS_LIST, updated_keys)


async def invalidate_tags_cache(ctx: Context) -> None:
    """Invalidate tag-related caches."""
    await ctx.set_state(CacheKeys.tags_response(True), None)
    await ctx.set_state(CacheKeys.tags_response(False), None)
    logger.debug("[CACHE] Invalidated tags caches")


# =============================================================================
# SESSION STATE CACHE HELPERS
# =============================================================================


async def get_cached_areas(ctx: Context) -> list:
    """Get areas list, cached for the session.

    Avoids redundant Things database lookups when resolving area_title parameters.
    Cache is automatically scoped to the MCP session.
    """
    areas = await ctx.get_state(CacheKeys.AREAS_LOOKUP)
    if areas is None:
        areas = things.areas()
        await ctx.set_state(CacheKeys.AREAS_LOOKUP, areas)
        logger.debug(f"Cached {len(areas)} areas for session")
    return areas


async def get_cached_projects(ctx: Context) -> list:
    """Get projects list, cached for the session.

    Avoids redundant Things database lookups when resolving list_title parameters.
    Cache is automatically scoped to the MCP session.
    """
    projects = await ctx.get_state(CacheKeys.PROJECTS_LOOKUP)
    if projects is None:
        projects = things.projects()
        await ctx.set_state(CacheKeys.PROJECTS_LOOKUP, projects)
        logger.debug(f"Cached {len(projects)} projects for session")
    return projects


async def _fetch_projects_with_cache(ctx: Context | None, include_items: bool) -> list:
    cache_key = CacheKeys.projects_raw(include_items)
    logger.debug("[CACHE] _fetch_projects_with_cache called: ctx=%s, key=%s", ctx is not None, cache_key)

    if ctx is not None:
        cached = await ctx.get_state(cache_key)
        if cached is not None:
            logger.debug("[CACHE] HIT for %s (%d items)", cache_key, len(cached))
            return cached
        logger.debug("[CACHE] MISS for %s", cache_key)

    projects = things.projects(include_items=include_items)
    if ctx is not None:
        await ctx.set_state(cache_key, projects)
        await _track_cache_key(ctx, cache_key)
        logger.debug("[CACHE] STORED %s (%d items)", cache_key, len(projects))
    return projects


async def _fetch_areas_with_cache(ctx: Context | None, include_items: bool) -> list:
    cache_key = CacheKeys.areas_raw(include_items)
    logger.debug("[CACHE] _fetch_areas_with_cache called: ctx=%s, key=%s", ctx is not None, cache_key)

    if ctx is not None:
        cached = await ctx.get_state(cache_key)
        if cached is not None:
            logger.debug("[CACHE] HIT for %s (%d items)", cache_key, len(cached))
            return cached
        logger.debug("[CACHE] MISS for %s", cache_key)

    areas = things.areas(include_items=include_items)
    if ctx is not None:
        await ctx.set_state(cache_key, areas)
        await _track_cache_key(ctx, cache_key)
        logger.debug("[CACHE] STORED %s (%d items)", cache_key, len(areas))
    return areas


def _build_cache_key(prefix: str, params: dict[str, Any]) -> str:
    """Build a cache key from prefix and params, hashing if too long.

    Long cache keys (e.g., from complex search queries) are hashed to avoid
    memory/key-length issues while maintaining uniqueness.
    """
    if not params:
        return f"{prefix}:empty"
    serialised = json.dumps(params, sort_keys=True, default=str)
    key = f"{prefix}:{serialised}"

    # Hash long keys to avoid excessive memory usage and key-length issues
    if len(key) > MAX_CACHE_KEY_LENGTH:
        hash_suffix = hashlib.sha256(serialised.encode()).hexdigest()[:16]
        return f"{prefix}:hash_{hash_suffix}"

    return key


async def _fetch_search_cache(
    ctx: Context | None,
    cache_key: str,
    loader: Callable[[], Any],
    label: str | None = None,
) -> Any:
    """Fetch data with caching, using individual keys to avoid race conditions.

    Previously used a shared dict which had read-modify-write race conditions.
    Now uses individual state keys for each cache entry.
    """
    display_label = label or cache_key

    if ctx is None:
        logger.debug("[CACHE] Context is None - bypassing cache for %s", display_label)
        return loader()

    # Use individual key instead of shared dict (fixes race condition)
    state_key = CacheKeys.search_raw(cache_key)
    cached = await ctx.get_state(state_key)

    if cached is not None:
        logger.debug("[CACHE] HIT for %s", display_label)
        return cached

    logger.debug("[CACHE] MISS for %s", display_label)
    result = loader()
    await ctx.set_state(state_key, result)
    await _track_cache_key(ctx, state_key)
    logger.debug("[CACHE] STORED %s", display_label)
    return result


async def _build_tags_response(include_items: bool, ctx: Context | None) -> str:
    """Build tags response with response-level caching.

    Caches the formatted response to avoid O(n) formatter DB calls when
    include_items=True (format_tag calls things.todos() per tag).
    """
    # Response cache key
    response_key = CacheKeys.tags_response(include_items)

    if ctx is not None:
        cached_response = await ctx.get_state(response_key)
        if cached_response is not None:
            logger.debug("[CACHE] RESPONSE HIT for %s", response_key)
            return cached_response
        logger.debug("[CACHE] RESPONSE MISS for %s", response_key)

    # Note: things.tags() ignores include_items, so we use a single raw cache key
    tags = await _fetch_search_cache(ctx, "tags_raw", lambda: things.tags(), label="get_tags")

    if not tags:
        return "No tags found"

    formatted_tags = [format_tag(tag, include_items) for tag in tags]
    response = "\n\n---\n\n".join(formatted_tags)

    if ctx is not None:
        await ctx.set_state(response_key, response)
        await _track_cache_key(ctx, response_key)
        logger.debug("[CACHE] RESPONSE STORED for %s", response_key)

    return response


async def _build_tagged_items_response(tag: str, ctx: Context | None) -> str:
    """Build tagged items response with response-level caching."""
    response_key = CacheKeys.tagged_items_response(tag)

    if ctx is not None:
        cached_response = await ctx.get_state(response_key)
        if cached_response is not None:
            logger.debug("[CACHE] RESPONSE HIT for %s", response_key)
            return cached_response
        logger.debug("[CACHE] RESPONSE MISS for %s", response_key)

    cache_key = _build_cache_key("tagged_items", {"tag": tag})
    todos = await _fetch_search_cache(
        ctx,
        cache_key,
        lambda: things.todos(tag=tag, include_items=True),
        label=f"tagged_items:{tag}",
    )

    if not todos:
        return f"No items found with tag '{tag}'"

    formatted_todos = [format_todo(todo) for todo in todos]
    response = "\n\n---\n\n".join(formatted_todos)

    if ctx is not None:
        await ctx.set_state(response_key, response)
        await _track_cache_key(ctx, response_key)
        logger.debug("[CACHE] RESPONSE STORED for %s", response_key)

    return response


def _validate_period(period: str) -> bool:
    return bool(period and any(period.endswith(unit) for unit in ["d", "w", "m", "y"]))


async def _build_recent_response(period: str, ctx: Context | None) -> str:
    """Build recent items response with response-level caching."""
    if not _validate_period(period):
        raise ValueError("Period must be in format '3d', '1w', '2m', '1y'")

    response_key = CacheKeys.recent_response(period)

    if ctx is not None:
        cached_response = await ctx.get_state(response_key)
        if cached_response is not None:
            logger.debug("[CACHE] RESPONSE HIT for %s", response_key)
            return cached_response
        logger.debug("[CACHE] RESPONSE MISS for %s", response_key)

    cache_key = _build_cache_key("get_recent", {"period": period})
    items = await _fetch_search_cache(
        ctx,
        cache_key,
        lambda: things.last(period, include_items=True),
        label=f"get_recent:{period}",
    )

    if not items:
        return f"No items found in the last {period}"

    formatted_items: list[str] = []
    for item in items:
        if item.get("type") == "to-do":
            formatted_items.append(format_todo(item))
        elif item.get("type") == "project":
            formatted_items.append(format_project(item, include_items=False))

    response = "\n\n---\n\n".join(formatted_items)

    if ctx is not None:
        await ctx.set_state(response_key, response)
        await _track_cache_key(ctx, response_key)
        logger.debug("[CACHE] RESPONSE STORED for %s", response_key)

    return response


# Create the FastMCP server
mcp = FastMCP("Things", instructions="Interact with the Things 3 task management app")


def register_tool(name: str):
    """Register a tool while preserving the original callable for internal reuse."""

    def decorator(func):
        mcp.tool(name=name)(func)
        return func

    return decorator


# LIST VIEWS


@register_tool(name="get_inbox")
def get_inbox() -> str:
    """Get todos from Inbox."""
    import time

    start_time = time.time()
    log_operation_start("get-inbox")

    try:
        todos = things.inbox(include_items=True)

        if not todos:
            log_operation_end("get-inbox", True, time.time() - start_time, count=0)
            return "No items found in Inbox"

        formatted_todos = [format_todo(todo) for todo in todos]
        log_operation_end("get-inbox", True, time.time() - start_time, count=len(todos))
        return "\n\n---\n\n".join(formatted_todos)
    except Exception as e:
        log_operation_end("get-inbox", False, time.time() - start_time, error=str(e))
        raise


@register_tool(name="get_today")
def get_today() -> str:
    """Get todos due today."""
    import time

    start_time = time.time()
    log_operation_start("get-today")

    try:
        todos = things.today(include_items=True)

        if not todos:
            log_operation_end("get-today", True, time.time() - start_time, count=0)
            return "No items due today"

        formatted_todos = [format_todo(todo) for todo in todos]
        log_operation_end("get-today", True, time.time() - start_time, count=len(todos))
        return "\n\n---\n\n".join(formatted_todos)
    except TypeError as e:
        if "'<' not supported between instances of 'NoneType' and 'str'" in str(e):
            # Handle the known sorting bug in things.today() by using a workaround
            try:
                # Replicate the exact logic from things.today() but with safe sorting
                import datetime

                datetime.date.today().strftime("%Y-%m-%d")

                # Replicate the three categories from things.today():
                # 1. regular_today_tasks: start_date=True (today), start="Anytime", index="todayIndex"
                regular_today_tasks = things.tasks(
                    start_date=True,  # today
                    start="Anytime",
                    index="todayIndex",
                    status="incomplete",
                    include_items=True,
                )

                # 2. unconfirmed_scheduled_tasks: start_date="past", start="Someday", index="todayIndex"
                unconfirmed_scheduled_tasks = things.tasks(start_date="past", start="Someday", index="todayIndex", status="incomplete", include_items=True)

                # 3. unconfirmed_overdue_tasks: start_date=False, deadline="past", deadline_suppressed=False
                unconfirmed_overdue_tasks = things.tasks(start_date=False, deadline="past", deadline_suppressed=False, status="incomplete", include_items=True)

                # Combine all three categories like the original
                result = [
                    *regular_today_tasks,
                    *unconfirmed_scheduled_tasks,
                    *unconfirmed_overdue_tasks,
                ]

                if not result:
                    return "No items due today"

                # Sort manually with None-safe comparison
                def safe_sort_key(task):
                    today_index = task.get("today_index")
                    if today_index is None:
                        today_index = 999999  # Put items without today_index at the end
                    start_date = task.get("start_date")
                    if start_date is None:
                        start_date = ""
                    return (today_index, start_date)

                result.sort(key=safe_sort_key)
                formatted_todos = [format_todo(todo) for todo in result]
                # Only log success AFTER the fallback actually succeeds
                if result:
                    log_operation_end("get-today", True, time.time() - start_time, count=len(result))
                    return "\n\n---\n\n".join(formatted_todos)
                else:
                    log_operation_end("get-today", True, time.time() - start_time, count=0)
                    return "No items due today"

            except Exception as fallback_error:
                log_operation_end("get-today", False, time.time() - start_time, error=f"Fallback failed: {fallback_error!s}")
                return f"Error: Unable to get today's items due to a sorting issue in the Things library. Fallback also failed: {fallback_error!s}"
        else:
            log_operation_end("get-today", False, time.time() - start_time, error=str(e))
            raise
    except Exception as e:
        log_operation_end("get-today", False, time.time() - start_time, error=str(e))
        raise


@register_tool(name="get_upcoming")
def get_upcoming() -> str:
    """Get all upcoming todos (those with a start date in the future)."""
    todos = things.upcoming(include_items=True)

    if not todos:
        return "No upcoming items"

    formatted_todos = [format_todo(todo) for todo in todos]
    return "\n\n---\n\n".join(formatted_todos)


@register_tool(name="get_anytime")
def get_anytime() -> str:
    """Get all todos from Anytime list. Note that this will return an extensive list of tasks. It is generally recommended to use get_todos with filters or search_todos instead."""
    todos = things.anytime(include_items=True)

    if not todos:
        return "No items in Anytime list"

    formatted_todos = [format_todo(todo) for todo in todos]
    return "\n\n---\n\n".join(formatted_todos)


@register_tool(name="get_random_inbox")
def get_random_inbox(count: int = 5) -> str:
    """Get a random sample of todos from Inbox.

    Args:
    ----
        count: Number of random items to return. Defaults to 5.
    """
    import time

    start_time = time.time()
    log_operation_start("get-random-inbox")

    try:
        items = things.inbox(include_items=True)

        if not items:
            log_operation_end("get-random-inbox", True, time.time() - start_time, count=0)
            return "No items found in Inbox"

        # Sample without replacement up to the number of available items
        if count <= 0:
            sampled = []
        elif len(items) <= count:
            sampled = items
        else:
            sampled = random.sample(items, count)  # nosec B311 - not used for cryptographic purposes  # nosec B311 - not used for cryptographic purposes

        if not sampled:
            log_operation_end("get-random-inbox", True, time.time() - start_time, count=0)
            return "No items found in Inbox"

        formatted = [format_todo(item) for item in sampled]
        log_operation_end("get-random-inbox", True, time.time() - start_time, count=len(sampled))
        return "\n\n---\n\n".join(formatted)
    except Exception as e:
        log_operation_end("get-random-inbox", False, time.time() - start_time, error=str(e))
        raise


@register_tool(name="get_random_anytime")
def get_random_anytime(count: int = 5) -> str:
    """Get a random sample of items from the Anytime list.

    Note: The Anytime list can contain both todos and projects. This returns a
    random subset without filtering types.

    Args:
    ----
        count: Number of random items to return. Defaults to 5.
    """
    items = things.anytime(include_items=True)

    if not items:
        return "No items in Anytime list"

    if count <= 0:
        sampled = []
    elif len(items) <= count:
        sampled = items
    else:
        sampled = random.sample(items, count)  # nosec B311 - not used for cryptographic purposes

    if not sampled:
        return "No items in Anytime list"

    formatted = [format_todo(item) for item in sampled]
    return "\n\n---\n\n".join(formatted)


@register_tool(name="get_someday")
def get_someday() -> str:
    """Get todos from Someday list."""
    todos = things.someday(include_items=True)

    if not todos:
        return "No items in Someday list"

    formatted_todos = [format_todo(todo) for todo in todos]
    return "\n\n---\n\n".join(formatted_todos)


@register_tool(name="get_logbook")
def get_logbook(period: str = "7d", limit: int = 50) -> str:
    """Get completed todos from Logbook, defaults to last 7 days.

    Args:
    ----
        period: Time period to look back (e.g., '3d', '1w', '2m', '1y'). Defaults to '7d'.
        limit: Maximum number of entries to return. Defaults to 50.
    """
    import time
    from datetime import datetime, timedelta

    start_time = time.time()
    log_operation_start("get-logbook")

    try:
        # Parse period (e.g., "1d", "7d", "2w", "1m", "1y")
        if not period or period[-1] not in ["d", "w", "m", "y"]:
            log_operation_end("get-logbook", False, time.time() - start_time, error=f"Invalid period format: {period}")
            return f"Error: Invalid period format '{period}'. Expected format: '3d', '1w', '2m', '1y'"

        number = int(period[:-1])
        unit = period[-1]

        # Calculate start date based on period
        if unit == "d":
            start_date = (datetime.now() - timedelta(days=number)).strftime("%Y-%m-%d")
        elif unit == "w":
            start_date = (datetime.now() - timedelta(weeks=number)).strftime("%Y-%m-%d")
        elif unit == "m":
            start_date = (datetime.now() - timedelta(days=number * 30)).strftime("%Y-%m-%d")
        elif unit == "y":
            start_date = (datetime.now() - timedelta(days=number * 365)).strftime("%Y-%m-%d")

        logger.debug(f"Logbook query: period={period}, start_date>={start_date}")

        # Query using stop_date (completion date) instead of last (creation date)
        # This fixes the bug where items were filtered by creation date instead of completion date
        todos = things.tasks(status="completed", stop_date=f">={start_date}", include_items=True)

        if not todos:
            log_operation_end("get-logbook", True, time.time() - start_time, count=0)
            return "No completed items found"

        # Sort by completion date, newest first
        # Use 'or ""' to handle None values safely (prevents TypeError in Python 3)
        todos.sort(key=lambda x: x.get("stop_date") or "", reverse=True)

        if len(todos) > limit:
            todos = todos[:limit]

        formatted_todos = [format_todo(todo) for todo in todos]
        log_operation_end("get-logbook", True, time.time() - start_time, count=len(todos))
        return "\n\n---\n\n".join(formatted_todos)

    except ValueError as e:
        log_operation_end("get-logbook", False, time.time() - start_time, error=str(e))
        return f"Error: Invalid period format '{period}'. Expected format: '3d', '1w', '2m', '1y'"
    except Exception as e:
        log_operation_end("get-logbook", False, time.time() - start_time, error=str(e))
        raise


@register_tool(name="get_trash")
def get_trash() -> str:
    """Get trashed todos."""
    todos = things.trash(include_items=True)

    if not todos:
        return "No items in trash"

    formatted_todos = [format_todo(todo) for todo in todos]
    return "\n\n---\n\n".join(formatted_todos)


@register_tool(name="get_todos")
def get_todos(project_uuid: str | None = None) -> str:
    """Get todos from Things, optionally filtered by project.

    Args:
    ----
        project_uuid: Optional UUID of a specific project to get todos from.
    """
    if project_uuid:
        project = things.get(project_uuid)
        if not project or project.get("type") != "project":
            return f"Error: Invalid project UUID '{project_uuid}'"

    todos = things.todos(project=project_uuid, start=None, include_items=True)

    if not todos:
        return "No todos found"

    formatted_todos = [format_todo(todo) for todo in todos]
    return "\n\n---\n\n".join(formatted_todos)


@register_tool(name="get_random_todos")
def get_random_todos(project_uuid: str | None = None, count: int = 5) -> str:
    """Get a random sample of todos, optionally filtered by project.

    Args:
    ----
        project_uuid: Optional UUID of a specific project to draw todos from.
        count: Number of todos to return. Defaults to 5.
    """
    if project_uuid:
        project = things.get(project_uuid)
        if not project or project.get("type") != "project":
            return f"Error: Invalid project UUID '{project_uuid}'"

    items = things.todos(project=project_uuid, start=None, include_items=True)

    if not items:
        return "No todos found"

    if count <= 0:
        sampled = []
    elif len(items) <= count:
        sampled = items
    else:
        sampled = random.sample(items, count)  # nosec B311 - not used for cryptographic purposes

    if not sampled:
        return "No todos found"

    formatted = [format_todo(todo) for todo in sampled]
    return "\n\n---\n\n".join(formatted)


async def _build_projects_response(include_items: bool, ctx: Context | None) -> str:
    """Build projects response with response-level caching."""
    response_key = CacheKeys.projects_response(include_items)

    if ctx is not None:
        cached_response = await ctx.get_state(response_key)
        if cached_response is not None:
            logger.debug("[CACHE] RESPONSE HIT for %s", response_key)
            return cached_response
        logger.debug("[CACHE] RESPONSE MISS for %s", response_key)

    projects = await _fetch_projects_with_cache(ctx, include_items)

    if not projects:
        return "No projects found"

    formatted_projects = [format_project(project, include_items) for project in projects]
    response = "\n\n---\n\n".join(formatted_projects)

    if ctx is not None:
        await ctx.set_state(response_key, response)
        await _track_cache_key(ctx, response_key)
        logger.debug("[CACHE] RESPONSE STORED for %s", response_key)

    return response


@register_tool(name="get_projects")
async def _get_projects_tool(include_items: bool = False, ctx: Context | None = None) -> str:
    """Get all projects from Things via MCP (cached per session)."""
    return await _build_projects_response(include_items, ctx)


def get_projects(include_items: bool = False) -> str:
    """Get all projects from Things.

    Args:
    ----
        include_items: Include tasks within projects.
    """
    return asyncio.run(_build_projects_response(include_items, None))


async def _build_areas_response(include_items: bool, ctx: Context | None) -> str:
    """Build areas response with response-level caching."""
    response_key = CacheKeys.areas_response(include_items)

    if ctx is not None:
        cached_response = await ctx.get_state(response_key)
        if cached_response is not None:
            logger.debug("[CACHE] RESPONSE HIT for %s", response_key)
            return cached_response
        logger.debug("[CACHE] RESPONSE MISS for %s", response_key)

    areas = await _fetch_areas_with_cache(ctx, include_items)

    if not areas:
        return "No areas found"

    formatted_areas = [format_area(area, include_items) for area in areas]
    response = "\n\n---\n\n".join(formatted_areas)

    if ctx is not None:
        await ctx.set_state(response_key, response)
        await _track_cache_key(ctx, response_key)
        logger.debug("[CACHE] RESPONSE STORED for %s", response_key)

    return response


@register_tool(name="get_areas")
async def _get_areas_tool(include_items: bool = False, ctx: Context | None = None) -> str:
    """Get all areas from Things via MCP (cached per session)."""
    return await _build_areas_response(include_items, ctx)


def get_areas(include_items: bool = False) -> str:
    """Get all areas from Things. Use these names when assigning a task or project to an area.

    Args:
    ----
        include_items: Include projects and tasks within areas
    """
    return asyncio.run(_build_areas_response(include_items, None))


# TAG OPERATIONS


@register_tool(name="get_tags")
async def _get_tags_tool(include_items: bool = False, ctx: Context | None = None) -> str:
    """Get all tags via MCP (cached per session)."""
    return await _build_tags_response(include_items, ctx)


def get_tags(include_items: bool = False) -> str:
    """Get all tags.

    Args:
    ----
        include_items: Include items tagged with each tag
    """
    return asyncio.run(_build_tags_response(include_items, None))


@register_tool(name="get_tagged_items")
async def _get_tagged_items_tool(tag: str, ctx: Context | None = None) -> str:
    """Get items with a specific tag via MCP (cached per session)."""
    return await _build_tagged_items_response(tag, ctx)


def get_tagged_items(tag: str) -> str:
    """Get items with a specific tag.

    Args:
    ----
        tag: Tag title to filter by
    """
    return asyncio.run(_build_tagged_items_response(tag, None))


# SEARCH OPERATIONS


async def _build_search_todos_response(query: str, ctx: Context | None) -> str:
    """Build search todos response with response-level caching."""
    response_key = CacheKeys.search_todos_response(query)

    if ctx is not None:
        cached_response = await ctx.get_state(response_key)
        if cached_response is not None:
            logger.debug("[CACHE] RESPONSE HIT for %s", response_key)
            return cached_response
        logger.debug("[CACHE] RESPONSE MISS for %s", response_key)

    params = {"query": query}
    cache_key = _build_cache_key("search_todos", params)
    todos = await _fetch_search_cache(
        ctx,
        cache_key,
        lambda: things.search(query, include_items=True),
        label=f"search_todos:{query}",
    )

    if not todos:
        return f"No todos found matching '{query}'"

    formatted_todos = [format_todo(todo) for todo in todos]
    response = "\n\n---\n\n".join(formatted_todos)

    if ctx is not None:
        await ctx.set_state(response_key, response)
        await _track_cache_key(ctx, response_key)
        logger.debug("[CACHE] RESPONSE STORED for %s", response_key)

    return response


@register_tool(name="search_todos")
async def _search_todos_tool(query: str, ctx: Context | None = None) -> str:
    """Search todos by title or notes (cached per session)."""
    return await _build_search_todos_response(query, ctx)


def search_todos(query: str) -> str:
    """Search todos by title or notes.

    Args:
    ----
        query: Search term to look for in todo titles and notes
    """
    return asyncio.run(_build_search_todos_response(query, None))


async def _build_search_advanced_response(
    status: str | None = None,
    start_date: str | None = None,
    deadline: str | None = None,
    tag: str | None = None,
    area: str | None = None,
    type: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Build advanced search response with response-level caching."""
    kwargs: dict[str, Any] = {"include_items": True}
    if status:
        kwargs["status"] = status
    if start_date:
        kwargs["start"] = start_date
    if deadline:
        kwargs["deadline"] = deadline
    if tag:
        kwargs["tag"] = tag
    if area:
        kwargs["area"] = area
    if type:
        kwargs["type"] = type

    filters = {k: v for k, v in kwargs.items() if k != "include_items" and v is not None}
    cache_key = _build_cache_key("search_advanced", filters)
    response_key = CacheKeys.search_advanced_response(cache_key)

    if ctx is not None:
        cached_response = await ctx.get_state(response_key)
        if cached_response is not None:
            logger.debug("[CACHE] RESPONSE HIT for %s", response_key)
            return cached_response
        logger.debug("[CACHE] RESPONSE MISS for %s", response_key)

    try:
        todos = await _fetch_search_cache(
            ctx,
            cache_key,
            lambda: things.todos(**kwargs),
            label=f"search_advanced:{cache_key}",
        )

        if not todos:
            return "No items found matching your search criteria"

        formatted_todos = [format_todo(todo) for todo in todos]
        response = "\n\n---\n\n".join(formatted_todos)

        if ctx is not None:
            await ctx.set_state(response_key, response)
            await _track_cache_key(ctx, response_key)
            logger.debug("[CACHE] RESPONSE STORED for %s", response_key)

        return response
    except Exception as e:
        return f"Error in advanced search: {e!s}"


@register_tool(name="search_advanced")
async def _search_advanced_tool(
    status: str | None = None,
    start_date: str | None = None,
    deadline: str | None = None,
    tag: str | None = None,
    area: str | None = None,
    type: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Advanced todo search with multiple filters (cached per session)."""
    return await _build_search_advanced_response(
        status=status,
        start_date=start_date,
        deadline=deadline,
        tag=tag,
        area=area,
        type=type,
        ctx=ctx,
    )


def search_advanced(
    status: str | None = None,
    start_date: str | None = None,
    deadline: str | None = None,
    tag: str | None = None,
    area: str | None = None,
    type: str | None = None,
) -> str:
    """Advanced todo search with multiple filters.

    Args:
    ----
        status: Filter by todo status (incomplete/completed/canceled)
        start_date: Filter by start date (YYYY-MM-DD)
        deadline: Filter by deadline (YYYY-MM-DD)
        tag: Filter by tag
        area: Filter by area UUID
        type: Filter by item type (to-do/project/heading)
    """
    return asyncio.run(
        _build_search_advanced_response(
            status=status,
            start_date=start_date,
            deadline=deadline,
            tag=tag,
            area=area,
            type=type,
            ctx=None,
        )
    )


# MODIFICATION OPERATIONS


@register_tool(name="add_todo")
async def add_task(
    title: str,
    *,
    ctx: Context | None = None,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | str | None = None,
    list_id: str | None = None,
    list_title: str | None = None,
) -> str:
    """Create a new todo in Things.

    Args:
    ----
        title: Title of the todo
        ctx: FastMCP context for session state (auto-injected by MCP, optional for direct calls)
        notes: Notes for the todo
        when: When to schedule the todo (today, tomorrow, evening, anytime, someday, or YYYY-MM-DD)
        deadline: Deadline for the todo (YYYY-MM-DD)
        tags: Tags to apply to the todo. IMPORTANT: Always pass as an array of
            strings (e.g., ["tag1", "tag2"]) NOT as a comma-separated string.
            Passing as a string will treat each character as a separate tag.
        list_id: ID of project/area to add to (takes priority over list_title if both provided)
        list_title: Title of project/area to add to (must exactly match an existing area or project title — look them up with get_areas or get_projects).
            If both list_id and list_title are provided, list_id takes priority.
    """
    try:
        # Debug: Log all input parameters
        logger.debug("MCP add_todo called with parameters:")
        logger.debug(f"  title: {title!r}")
        logger.debug(f"  notes: {notes!r}")
        logger.debug(f"  when: {when!r}")
        logger.debug(f"  deadline: {deadline!r}")
        logger.debug(f"  tags: {tags!r} (type: {type(tags)})")
        logger.debug(f"  list_id: {list_id!r}")
        logger.debug(f"  list_title: {list_title!r}")

        # Preprocess parameters to handle MCP array serialization issues
        params = preprocess_array_params(tags=tags)
        tags = params["tags"]
        logger.debug(f"  processed tags: {tags!r} (type: {type(tags)})")

        # Clean up title and notes to handle URL encoding
        if isinstance(title, str):
            title = title.replace("+", " ").replace("%20", " ")

        if isinstance(notes, str):
            notes = notes.replace("+", " ").replace("%20", " ")

        # Use the direct AppleScript approach which is more reliable
        logger.info(f"Creating todo using AppleScript: {title}")

        try:
            task_id = add_todo(title=title, notes=notes, when=when, deadline=deadline, tags=tags, list_id=list_id, list_title=list_title)
        except Exception as bridge_error:
            logger.error(f"AppleScript bridge error: {bridge_error}")
            return f"⚠️ AppleScript bridge error: {bridge_error}"

        # Check if the returned value is actually an error message rather than a valid task ID
        if not task_id:
            return "⚠️ Error: Failed to create todo using AppleScript"

        # Check if the returned value is actually an error message rather than a valid task ID
        if isinstance(task_id, str) and ("script error" in task_id or task_id.startswith("/var/folders/") or task_id.startswith("Error:")):
            logger.error("AppleScript returned error instead of task ID: %s", task_id)
            return f"⚠️ AppleScript error: {task_id}"

        # Get location information for the success message
        # Use cached data when ctx is available (MCP invocation), fallback to direct lookup otherwise
        try:
            todo = things.get(task_id)
            if todo:
                if todo.get("project"):
                    if ctx is not None:
                        # Use cached projects for efficient lookup
                        projects = await get_cached_projects(ctx)
                        project = next((p for p in projects if p["uuid"] == todo["project"]), None)
                        location = f"Project: {project['title']}" if project else f"Project: {todo['project']}"
                    else:
                        # Direct lookup when no context
                        project_item = things.get(todo["project"])
                        location = f"Project: {project_item['title']}" if project_item else f"Project: {todo['project']}"
                elif todo.get("area"):
                    if ctx is not None:
                        # Use cached areas for efficient lookup
                        areas = await get_cached_areas(ctx)
                        area = next((a for a in areas if a["uuid"] == todo["area"]), None)
                        location = f"Area: {area['title']}" if area else f"Area: {todo['area']}"
                    else:
                        # Direct lookup when no context
                        area_item = things.get(todo["area"])
                        location = f"Area: {area_item['title']}" if area_item else f"Area: {todo['area']}"
                else:
                    location = f"List: {todo.get('start', 'Unknown')}"
            else:
                location = "Unknown"
        except Exception:
            location = "Unknown"

        # Invalidate caches that may contain todo data
        if ctx is not None:
            await invalidate_todos_cache(ctx)

        return f"✅ Successfully created todo: {title} (ID: {task_id}) in {location}"

    except Exception as e:
        logger.error(f"Error creating todo: {e!s}")
        import traceback

        logger.error(f"Full traceback: {traceback.format_exc()}")
        return f"⚠️ Error creating todo: {e!s}"


@register_tool(name="add_project")
async def add_new_project(
    title: str,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | str | None = None,
    area_id: str | None = None,
    area_title: str | None = None,
    todos: list[str] | str | None = None,
    *,
    ctx: Context | None = None,
) -> str:
    """Create a new project in Things.

    Args:
    ----
        title: Title of the project
        notes: Notes for the project
        when: When to schedule the project
        deadline: Deadline for the project
        tags: Tags to apply to the project. IMPORTANT: Always pass as an array of
            strings (e.g., ["tag1", "tag2"]) NOT as a comma-separated string.
            Passing as a string will treat each character as a separate tag.
        area_id: ID of area to add to
        area_title: Title of area to add to (must exactly match an existing area title — look them up with get_areas)
        todos: Initial todos to create in the project
        ctx: FastMCP context for session state (auto-injected by MCP, optional for direct calls)
    """
    try:
        # Preprocess parameters to handle MCP array serialization issues
        params = preprocess_array_params(tags=tags, todos=todos)
        tags = params["tags"]
        todos = params["todos"]

        # Clean up title and notes to handle URL encoding
        if isinstance(title, str):
            title = title.replace("+", " ").replace("%20", " ")

        if isinstance(notes, str):
            notes = notes.replace("+", " ").replace("%20", " ")

        # Use the direct AppleScript approach which is more reliable
        logger.info(f"Creating project using AppleScript: {title}")

        # Call the AppleScript bridge directly
        try:
            project_id = add_project(title=title, notes=notes, when=when, deadline=deadline, tags=tags, area_title=area_title, area_id=area_id, todos=todos)
        except Exception as bridge_error:
            logger.error(f"AppleScript bridge error: {bridge_error}")
            return f"⚠️ AppleScript bridge error: {bridge_error}"

        if not project_id:
            return "Error: Failed to create project using AppleScript"

        # Invalidate caches since we created a new project
        if ctx is not None:
            await invalidate_projects_cache(ctx)
            await invalidate_areas_cache(ctx)  # Area item counts change
            await invalidate_todos_cache(ctx)  # Projects appear in searches/recent

        # Look up the project to get location information
        # Use cached data when ctx is available (MCP invocation), fallback to direct lookup otherwise
        try:
            project = things.get(project_id)
            if project:
                if project.get("area"):
                    if ctx is not None:
                        # Use cached areas for efficient lookup
                        areas = await get_cached_areas(ctx)
                        area = next((a for a in areas if a["uuid"] == project["area"]), None)
                        location = f"Area: {area['title']}" if area else f"Area: {project['area']}"
                    else:
                        # Direct lookup when no context
                        area_item = things.get(project["area"])
                        location = f"Area: {area_item['title']}" if area_item else f"Area: {project['area']}"
                else:
                    location = "List: Inbox"
            else:
                location = "Unknown"
        except Exception:
            location = "Unknown"

        return f"✅ Successfully created project: {title} (ID: {project_id}) in {location}"

    except Exception as e:
        logger.error(f"Error creating project: {e!s}")
        import traceback

        logger.error(f"Full traceback: {traceback.format_exc()}")
        return f"⚠️ Error creating project: {e!s}"


@register_tool(name="update_todo")
async def update_task(
    id: str,
    title: str | None = None,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | str | None = None,
    completed: bool | None = None,
    canceled: bool | None = None,
    list_id: str | None = None,
    list_name: str | None = None,
    *,
    ctx: Context | None = None,
) -> str:
    """Update an existing todo in Things.

    Args:
    ----
        id: ID of the todo to update.
        title: New title.
        notes: New notes.
        when: When to schedule the todo (today, tomorrow, anytime, someday, or YYYY-MM-DD).
        deadline: New deadline (YYYY-MM-DD).
        tags: New tags. IMPORTANT: Always pass as an array of strings (e.g., ["tag1", "tag2"]) NOT as a comma-separated string. Passing as a string will treat each character as a separate tag.
        completed: Mark as completed.
        canceled: Mark as canceled.
        list_id: ID of project/area to move the todo to (takes priority over list_name if both provided).
        list_name: Name of built-in list, project, or area to move the todo to. For built-in lists use: "Inbox", "Today", "Anytime", "Someday". For projects or areas, use the exact name.
            If both list_id and list_name are provided, list_id takes priority.
        ctx: FastMCP context for session state (auto-injected by MCP, optional for direct calls)
    """
    # Note: ctx is available for future caching of list_name lookups when provided
    try:
        # Preprocess parameters to handle MCP array serialization issues
        params = preprocess_array_params(tags=tags)
        tags = params["tags"]

        # Clean up string parameters to handle URL encoding
        if isinstance(title, str):
            title = title.replace("+", " ").replace("%20", " ")
        if isinstance(notes, str):
            notes = notes.replace("+", " ").replace("%20", " ")
        if isinstance(list_name, str):
            list_name = list_name.replace("+", " ").replace("%20", " ")

        logger.info(f"Updating todo using AppleScript: {id}")

        # Call the AppleScript bridge directly
        try:
            success = update_todo(
                id=id,
                title=title,
                notes=notes,
                when=when,
                deadline=deadline,
                tags=tags,
                completed=completed,
                canceled=canceled,
                list_id=list_id,
                list_name=list_name,
            )
            logger.debug(f"AppleScript bridge returned: {success!r} (type: {type(success)})")

            # Handle various success cases
            if "true" in str(success).lower():
                logger.debug("Success case matched: 'true' in result")

                # Invalidate caches since we updated a todo
                if ctx is not None:
                    await invalidate_todos_cache(ctx)

                return f"✅ Successfully updated todo with ID: {id}"
            elif success.startswith("Error:"):
                logger.error(f"AppleScript error: {success}")
                return success
            else:
                logger.error(f"AppleScript update failed with result: {success!r}")
                return f"Error: Failed to update todo using AppleScript. Result: {success}"

        except Exception as bridge_error:
            logger.error(f"AppleScript bridge error: {bridge_error}")
            logger.error(f"Full bridge error traceback: {traceback.format_exc()}")
            return f"⚠️ AppleScript bridge error: {bridge_error}"

    except Exception as e:
        logger.error(f"Error updating todo: {e!s}")
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return f"⚠️ Error updating todo: {e!s}"


@register_tool(name="update_project")
async def update_existing_project(
    id: str,
    title: str | None = None,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | str | None = None,
    completed: bool | None = None,
    canceled: bool | None = None,
    list_name: str | None = None,
    area_title: str | None = None,
    area_id: str | None = None,
    *,
    ctx: Context | None = None,
) -> str:
    """Update an existing project in Things.

    Args:
    ----
        id: ID of the project to update
        title: New title
        notes: New notes
        when: New schedule (today, tomorrow, anytime, someday, or YYYY-MM-DD)
        deadline: New deadline (YYYY-MM-DD)
        tags: New tags. IMPORTANT: Always pass as an array of strings (e.g., ["tag1", "tag2"]) NOT as a comma-separated string. Passing as a string will treat each character as a separate tag.
        completed: Mark as completed
        canceled: Mark as canceled
        list_name: Move project directly to a built-in list. Must be one of:
                  - "Today": Move to Today list
                  - "Anytime": Move to Anytime list
                  - "Someday": Move to Someday list
                  - "Trash": Move to trash
                  Note: Projects cannot be moved to Inbox or Logbook. To move a project
                  to Logbook, mark it as completed instead.
        area_title: Title of the area to move the project to
        area_id: ID of the area to move the project to
        ctx: FastMCP context for session state (auto-injected by MCP, optional for direct calls)
    """
    # Note: ctx is available for future caching of area_title lookups when provided
    try:
        # Log all input parameters for debugging
        logger.info("Raw input parameters for update_project:")
        for param_name, param_value in locals().items():
            if param_name != "self":  # Skip self parameter
                logger.info(f"  {param_name}: {param_value!r}")

        # Preprocess only the tags parameter
        params = preprocess_array_params(tags=tags)
        tags = params["tags"]

        # Clean up string parameters to handle URL encoding
        if isinstance(title, str):
            title = title.replace("+", " ").replace("%20", " ")
        if isinstance(notes, str):
            notes = notes.replace("+", " ").replace("%20", " ")
        if isinstance(area_title, str):
            area_title = area_title.replace("+", " ").replace("%20", " ")
            logger.info(f"Cleaned area_title: {area_title!r}")

        # Use the direct AppleScript approach which is more reliable
        logger.info(f"Updating project using AppleScript: {id}")

        # Call the AppleScript bridge directly
        try:
            success = update_project(
                id=id,
                title=title,
                notes=notes,
                when=when,
                deadline=deadline,
                tags=tags,
                completed=completed,
                canceled=canceled,
                list_name=list_name,
                area_title=area_title,
                area_id=area_id,
            )
            logger.debug(f"AppleScript bridge returned: {success!r} (type: {type(success)})")

            # Handle various success cases
            if "true" in str(success).lower():
                logger.debug("Success case matched: 'true' in result")

                # Invalidate caches since we updated a project
                if ctx is not None:
                    await invalidate_projects_cache(ctx)
                    await invalidate_areas_cache(ctx)  # Area item counts may change
                    await invalidate_todos_cache(ctx)  # Projects appear in searches/recent

                return f"✅ Successfully updated project with ID: {id}"
            elif success.startswith("Error:"):
                logger.error(f"AppleScript error: {success}")
                return success
            else:
                logger.error(f"AppleScript update failed with result: {success!r}")
                return f"Error: Failed to update project using AppleScript. Result: {success}"

        except Exception as bridge_error:
            logger.error(f"AppleScript bridge error: {bridge_error}")
            logger.error(f"Full bridge error traceback: {traceback.format_exc()}")
            return f"⚠️ AppleScript bridge error: {bridge_error}"

    except Exception as e:
        logger.error(f"Error updating project: {e!s}")
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return f"⚠️ Error updating project: {e!s}"


@register_tool(name="show_item")
def show_item(id: str, query: str | None = None, filter_tags: list[str] | None = None) -> str:
    """Show a specific item or list in Things.

    Args:
    ----
        id: ID of item to show, or one of: inbox, today, upcoming, anytime, someday, logbook
        query: Optional query to filter by
        filter_tags: Optional tags to filter by. IMPORTANT: Always pass as an
        array of strings (e.g., ["tag1", "tag2"]) NOT as a comma-separated
        string. Passing as a string will treat each character as a separate tag.
    """
    try:
        # For built-in lists, return the appropriate data
        if id == "inbox":
            return get_inbox()
        elif id == "today":
            return get_today()
        elif id == "upcoming":
            return get_upcoming()
        elif id == "anytime":
            return get_anytime()
        elif id == "someday":
            return get_someday()
        elif id == "logbook":
            return get_logbook()
        elif id == "trash":
            return get_trash()
        else:
            # For specific item IDs, try to get the item
            try:
                item = things.get(id)
                if item:
                    if item.get("type") == "to-do":
                        return format_todo(item)
                    elif item.get("type") == "project":
                        return format_project(item, include_items=True)
                    elif item.get("type") == "area":
                        return format_area(item, include_items=True)
                    else:
                        return f"Found item: {item}"
                else:
                    return f"No item found with ID: {id}"
            except Exception as e:
                return f"Error retrieving item '{id}': {e!s}"
    except Exception as e:
        logger.error(f"Error showing item: {e!s}")
        return f"Error showing item: {e!s}"


@register_tool(name="search_items")
def search_all_items(query: str) -> str:
    """Search for items in Things.

    Args:
    ----
        query: Search query
    """
    try:
        # Use the Python things library for search (same as search_todos)
        todos = things.search(query, include_items=True)

        if not todos:
            return f"No items found matching '{query}'"

        formatted_todos = [format_todo(todo) for todo in todos]
        return "\n\n---\n\n".join(formatted_todos)
    except Exception as e:
        logger.error(f"Error searching: {e!s}")
        return f"Error searching: {e!s}"


@register_tool(name="get_recent")
async def _get_recent_tool(period: str, ctx: Context | None = None) -> str:
    """Get recently created items via MCP (cached per session)."""
    try:
        return await _build_recent_response(period, ctx)
    except ValueError as err:
        return f"Error: {err}"
    except Exception as err:
        logger.error(f"Error getting recent items: {err!s}")
        return f"Error getting recent items: {err!s}"


def get_recent(period: str) -> str:
    """Get recently created items.

    Args:
    ----
        period: Time period (e.g., '3d', '1w', '2m', '1y')
    """
    try:
        return asyncio.run(_build_recent_response(period, None))
    except ValueError as err:
        return f"Error: {err}"
    except Exception as err:
        logger.error(f"Error getting recent items: {err!s}")
        return f"Error getting recent items: {err!s}"


# Main entry point
def run_things_mcp_server():
    """Run the Things MCP server."""
    # Check if Things app is available
    if ensure_things_ready():
        logger.info("Things app is running and ready for operations")
    else:
        logger.warning("Things app is not running at startup. Operations will attempt to connect when needed.")

    # Run the MCP server
    mcp.run()


if __name__ == "__main__":
    run_things_mcp_server()
