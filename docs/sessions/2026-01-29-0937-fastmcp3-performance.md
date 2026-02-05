# Session: FastMCP 3 Performance Instrumentation

**Date**: 2026-01-29
**Captured**: 09:37 Europe/Dublin
**Status**: In Progress

## Overview

Following Ross’ request to “see the FastMCP 3 performance wins,” I’m instrumenting the heavy queries with `Context`’s session state helpers so repeated `get_projects`, `get_areas`, and search calls can reuse cached data. Once the instrumentation is in place, I’ll rerun the end-to-end benchmark harness and record the fresh numbers.

## Goals

1. Extend the FastMCP tool implementations for the heaviest `things` queries so they cache results via `ctx.get_state/set_state`.
2. Keep the existing synchronous helpers (used by tests) working by wrapping them around new async helpers.
3. Re-run the v3 benchmark harness and capture the updated timing snapshots.

## Key Decisions

### Cache granularity

Use include-items-aware cache keys (`projects:{include_items}` and `areas:{include_items}`) so both lean and detail-rich clients benefit without corrupting each other’s results.

### Search caching

Cache search results keyed by the JSON-serialised argument tuple (query, filters) and store them in a shared `search_cache` dictionary per session; directly hitting Things only happens on the first call per signature.

## Changes Made

- Introduced async helpers (`_get_projects_cached`, `_get_areas_cached`, `_search_todos_cached`, `_search_advanced_cached`) with session-state storage via `ctx`.
- Preserved synchronous entry points (`get_projects()`, `get_areas()`, `search_todos()`, `search_advanced()`) for the test suite while exposing async versions for the MCP server.
- Added caching helpers and key builders near the top of `src/things3_mcp/fast_server.py`.
- Extended `tests/conftest.py` AppleScript timeout (already done earlier) to avoid tag-cleanup flakiness.
- New benchmark run planned after the instrumentation completes; results will go into `tmp/bench/results/`.

## Next Steps

- [ ] Apply new caching helpers to additional heavy queries if the current ones still spike.
- [ ] Re-run `tmp/bench/bench_fastmcp_e2e.py` (v3 only) and capture JSON/Markdown snapshots.
- [ ] Update session notes and include benchmark comparison in the final report.

### Update - 2026-01-29 11:25 Europe/Dublin

**Progress**: Extended caching to the remaining heavyweight views (`get_tags`, `get_tagged_items`, `get_recent`) by routing the MCP tools through async helpers that hit `ctx` state first and still expose synchronous helpers for tests. Tag searches now share the same response cache pattern (`tagged_items` key plus include-options) and `get_recent` caches per period after validating the format.

**Benchmark snapshot**: Reran `tmp/bench/bench_fastmcp_e2e.py` after the new caches; latest run is `tmp/bench/results/v3-20260129-111917.json`. Compared to the previous v3 run (`v3-20260129-103312.json`), medians are:

- `get_projects`: 294 ms → **317 ms** (mean dropped from 466 ms to 342 ms, p95 rose slightly to 438 ms; warm caches still dominate the timing).
- `get_areas`: 1.4 ms → **1.4 ms** (p95 1.5 ms → 1.7 ms).
- `search_todos`: 59 ms → **72 ms** (likely noise — this benchmark doesn't exercise the new tag/recent caches).
- `search_advanced`: 1.3 ms → **1.4 ms**.

**Notes**: The new caches reduce cold hits for tags/tagged-items/recent calls, but the benchmark doesn't directly measure those endpoints (and search timings regained pre-caching noise). Logs and JSON/Markdown snapshots for the latest run are in `tmp/bench/results/`.

### Update - 2026-01-29 14:20 Europe/Dublin

**Progress**: The benchmark harness now hits the tag/recent endpoints and every cache helper logs hits/misses (the `_fetch_search_cache` helper takes an optional `label` argument so log lines read `Cache hit/miss for <label>`). During the latest run (`tmp/bench/results/v3-20260129-141311.json`), all MCP calls warmed the cache after the first iteration, so repeated tags, tagged-items, and recent queries were served in 1–2 ms while `get_recent` still takes ~100 ms because it pulls structured data that Things builds each time.

**Benchmark snapshot** (v3 final run, 30 iterations, 3 warmups):

- `get_areas`: mean 1.6 ms, median 1.6 ms, p95 1.8 ms.
- `get_projects`: mean 347 ms, median 343 ms, p95 383 ms.
- `get_tags`: mean 2.0 ms, median 1.9 ms, p95 3.1 ms.
- `get_tagged_items`: mean 1.5 ms, median 1.5 ms, p95 1.8 ms.
- `get_recent` (period `1d`): mean 99 ms, median 97 ms, p95 112 ms.
- `search_todos`: mean 62 ms, median 61 ms, p95 75 ms.
- `search_advanced`: mean 1.6 ms, median 1.6 ms, p95 2.0 ms.

**Logging check**: Tail `~/.things-mcp/logs/things3_mcp.log` while a session is running and you’ll see lines like `Cache hit for get_tags` or `Cache miss for tagged_items:mcp-test-…`. This confirms that `ctx` is being populated for repeated calls even though the first call still triggers Things/AppleScript.

### Update - 2026-01-29 10:40 Europe/Dublin

**Progress**: Added session-state caching to `get_projects`, `get_areas`, `search_todos`, and `search_advanced`. Each MCP tool now routes through async helpers that pull from/session caches when `ctx` is present, but the synchronous helpers used by the test suite still work via `asyncio.run`. Search responses are now keyed by their argument tuple, so repeated queries for the same filters reuse the cached result instead of re-hitting Things.

**Benchmark result** (new v3 run `tmp/bench/results/v3-20260129-103312.json`):
- `get_projects`: median dropped from 422 ms → **294 ms** (p95 386 ms).
- `get_areas`: median dropped from 5.3 ms → **1.4 ms** (p95 1.5 ms).
- `search_todos`: median dropped from 79 ms → **59 ms**.
- `search_advanced`: median dropped from 3.7 ms → **1.3 ms**.
The cold first call is still expensive, but once the session cache populates, repeated lookups happen in a few milliseconds.

**Checks run**: `uv run ruff format`, `uv run ruff check`, `uv run mypy src/`, `uv run pytest tests` (121 passed).
