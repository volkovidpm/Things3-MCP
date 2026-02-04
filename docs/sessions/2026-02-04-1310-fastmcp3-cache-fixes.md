# Session: FastMCP Cache Fixes

**Date**: 2026-02-04
**Captured**: 13:10 Europe/Dublin (In Progress)

## Overview

Addressing cache invalidation gaps and context injection compatibility uncovered in code review. The focus is on ensuring todo mutations correctly invalidate project/area/tag caches, making `invalidate_all_caches` actually comprehensive, and adding regression tests for cache invalidation behaviour.

## Goals

1. Fix cache invalidation to cover projects, areas, and tags after todo mutations.
2. Ensure `invalidate_all_caches` clears all known cache entries, including list views.
3. Make tool context injection robust to positional or keyword `ctx` injection.
4. Add focused tests for cache invalidation behaviour.
5. Run lint, format check, type-check, and tests.

## Key Decisions

### Invalidation Strategy

Prefer a conservative invalidation approach (clear projects/areas/tags caches when todos change) to prioritise correctness over micro-optimisation. This avoids stale responses in include-items views and tag listings.

## Changes Made

- Pending.

## References

- Related file: `src/things3_mcp/fast_server.py`
- Related tests: `tests/`

### Update - 2026-02-04 13:20 Europe/Dublin

**Summary**: Expanded cache invalidation coverage, made tool context parameters positional-or-keyword, added cache invalidation tests, and documented the pre-release FastMCP dependency.

**Git Status**:
- Branch: `feature/fastmcp3-performance-optimization`
- Modified: `.claude/settings.local.json`, `pyproject.toml`, `src/things3_mcp/fast_server.py`
- Added: `docs/sessions/2026-02-04-1310-fastmcp3-cache-fixes.md`, `tests/test_cache_invalidation.py`
- Untracked (pre-existing): `docs/sessions/2026-01-28-1510-fastmcp3-benchmark.md`, `docs/sessions/2026-01-29-0937-fastmcp3-performance.md`

### Update - 2026-02-04 13:17 Europe/Dublin

**Summary**: Ran lint, format check, and mypy successfully. Pytest execution required `HOME=/tmp` to avoid log directory permissions and still failed because the Things app was not running (autouse test fixture asserts readiness).

**Checks**:
- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff check .` (pass)
- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check .` (pass)
- `UV_CACHE_DIR=/tmp/uv-cache uv run mypy src/` (pass)
- `HOME=/tmp UV_CACHE_DIR=/tmp/uv-cache uv run pytest --cov=things3_mcp --cov-report=term-missing --cov-report=html tests` (failed: Things app not ready)

### Update - 2026-02-04 13:20 Europe/Dublin

**Summary**: Finalised cache invalidation changes, introduced cache invalidation unit tests, and reran checks. Pytest still fails because the Things app is not running in this environment.

**Outstanding**:
- Run the test suite on a machine where Things 3 is running.

### Update - 2026-02-04 13:25 Europe/Dublin

**Summary**: User reported `sqlite3.OperationalError: unable to open database file` in completion/deadline tests. Suspected causes include missing database path or macOS permissions (Full Disk Access). Prepared diagnostics to print `things.database.DEFAULT_FILEPATH`, verify file existence, and attempt a direct sqlite open.

**Question Logged**:
- User: "These all fail when I run them from my normal terminal too" with sqlite open errors in completion/deadline tests.
- Response: Investigate DB path visibility and permissions; verify `THINGSDB` or grant Full Disk Access to the terminal/Python binary.

### Update - 2026-02-04 13:35 Europe/Dublin

**Summary**: User confirmed tests pass when run via `uv run pytest`; failures were tied to non-uv runner. Likely runner mismatch rather than repository changes.

### Update - 2026-02-04 13:40 Europe/Dublin

**Summary**: Updated README test metrics to reflect the latest run (125 passed in 448.80s / 0:07:28).

### Update - 2026-02-04 13:44 Europe/Dublin

**Summary**: Adjusted README test timing to use "~7 minutes" since the runtime is not deterministic.

### Update - 2026-02-04 13:49 Europe/Dublin

**Summary**: Added temporary peak cache-size tracking in `_track_cache_key` to measure real-world key volume against the 500-entry FIFO limit.

### Update - 2026-02-04 13:53 Europe/Dublin

**Summary**: Made peak cache-size logging visible via `THINGS_MCP_CACHE_METRICS=1` (logs at INFO when enabled; remains DEBUG otherwise).

### Update - 2026-02-04 14:03 Europe/Dublin

**Summary**: Added temporary `print()` of peak cache size when `THINGS_MCP_CACHE_METRICS=1` so it shows up without log streaming.
