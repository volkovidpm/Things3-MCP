# Session: FastMCP v3 Benchmarking

**Date**: 2026-01-28
**Captured**: 15:10 Europe/Dublin
**Status**: In Progress

## Overview

Starting an end-to-end benchmarking effort to compare the FastMCP v3 server implementation against the existing FastMCP v2 setup. The focus is on repeated access to areas/projects and search performance, using real Things 3 data via MCP stdio client calls.

## Goals

1. Create a baseline worktree at current `HEAD` for the v2 implementation.
2. Build a repeatable benchmark harness in `tmp/bench/` targeting `get_areas`, `get_projects`, and search operations.
3. Run benchmarks for v2 vs v3 and capture median/p95/ops-per-second metrics.
4. Run relevant lint/tests once changes are in place.

## Key Decisions

### Keep FastMCP v3 dependency floating

Ross wants to stay on `fastmcp>=3.0.0b1` to be first-to-market on FastMCP 3 support, despite beta churn risk.

### Benchmark scope

Benchmarks should emphasise search and repeated access of areas/projects rather than add-todo throughput.

## Changes Made

### New Files

- None yet.

### Modified Files

- None yet.

## Next Steps

- [ ] Create baseline worktree at `HEAD`.
- [ ] Implement benchmark harness under `tmp/bench/`.
- [ ] Run v2 vs v3 benchmarks and record results.
- [ ] Run lint/tests and update session notes.

### Update - 2026-01-28 15:49 Europe/Dublin

**Question**: Ross asked whether using `cast(Callable[[], str], get_trash)()` in `show_item` is the best approach.
**Answer**: It works but is ugly; recommended to split tool registration from implementation (e.g., `_get_trash_impl()` plus a thin `@mcp.tool` wrapper) so `show_item` can call the real function without casts.

### Update - 2026-01-28 16:05 Europe/Dublin

**Summary**: Implemented a cleaner tool registration approach, added end-to-end benchmarking harness/results, and stabilised a flaky tag cleanup test.

**Key Decisions**
- Switched to a `register_tool` decorator that registers tools without replacing the original callable, removing the need for casts in `show_item`.
- Increased tag cleanup AppleScript timeout in tests to reduce flaky failures.

**Changes Made**
- New benchmark harness and docs: `tmp/bench/bench_fastmcp_e2e.py`, `tmp/bench/README.md`.
- Benchmark results stored in `tmp/bench/results/`.
- Updated tool registration and `show_item` calls: `src/things3_mcp/fast_server.py`.
- Increased tag cleanup timeout: `tests/conftest.py`.

**Benchmark Snapshot (single run)**
- v2: `get_projects` ~0.45s median, `get_areas` ~0.005s median, `search_todos` ~0.078s median, `search_advanced` ~0.0038s median.
- v3: `get_projects` ~0.42–0.60s median (variance), `get_areas` ~0.005s median, `search_todos` ~0.079s median, `search_advanced` ~0.0037s median.

**Checks Run**
- `uv run ruff check src/things3_mcp/fast_server.py tests/conftest.py`
- `uv run ruff format --check src/things3_mcp/fast_server.py tests/conftest.py`
- `uv run mypy src/`
- `uv run pytest tests` (121 passed)
