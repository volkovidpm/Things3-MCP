# Session: FastMCP 3.0 Feature Utilization Analysis

**Date**: 2026-02-04
**Captured**: 16:45 Europe/Dublin
**Status**: Complete

## Overview

Comprehensive analysis of how the Things3-MCP project utilizes FastMCP 3.0 capabilities following the refactor to use FastMCP v3. The project primarily leverages **Session-Scoped State** for sophisticated per-session caching, along with **Resources** and **Prompts** for developer experience. Several advanced v3 features remain unused, representing opportunities for future enhancement.

## Goals

1. [x] Document which FastMCP 3.0 features are actively used
2. [x] Document which features are not yet adopted
3. [x] Explain the architectural decisions behind feature choices
4. [x] Identify potential future adoption opportunities

## Key Findings

### Features Actively Used

#### 1. Session-Scoped State (Primary v3 Adoption)

**Location**: `src/things3_mcp/fast_server.py:181-351`

The project implements a sophisticated 3-level caching system using `ctx.get_state()` / `ctx.set_state()`:

| Cache Level | Purpose | Example Keys |
|-------------|---------|--------------|
| **Lookup** | Minimal reference data for UUID resolution | `AREAS_LOOKUP`, `PROJECTS_LOOKUP` |
| **Raw** | Full Things objects with parametrized variants | `projects_raw(include_items=True)` |
| **Response** | Pre-formatted output strings (avoids O(n) formatter) | `projects_response(include_items=False)` |

**Key Implementation Details**:
- FIFO eviction at 500 entries (`MAX_RESPONSE_CACHE_SIZE`)
- SHA256 hashing for long cache keys
- Targeted invalidation on mutations (not full cache clear)
- Individual state keys instead of shared dicts (avoids race conditions)

**Performance Impact**:
| Operation | Before | After | Improvement |
|-----------|--------|-------|-------------|
| `get_projects` | 422ms | 294ms | 30% |
| `get_areas` | 5.3ms | 1.4ms | 73% |
| `search_todos` | 79ms | 59ms | 25% |
| `search_advanced` | 3.7ms | 1.3ms | 65% |
| Repeated calls | - | 1-2ms | (from cache) |

#### 2. MCP Resources (`@mcp.resource`)

**Location**: `src/things3_mcp/fast_server.py:523-712`

7 reference resources for client introspection:

| Resource URI | Purpose |
|--------------|---------|
| `things://schema/todo` | Todo field schema (JSON) |
| `things://schema/project` | Project field schema |
| `things://lists` | Valid list views and descriptions |
| `things://status-values` | Valid status enum values |
| `things://areas` | Area directory with UUIDs |
| `things://projects` | Project directory with UUIDs |
| `things://tags` | Tag directory |

#### 3. MCP Prompts (`@mcp.prompt`)

**Location**: `src/things3_mcp/fast_server.py:719-894`

5 GTD workflow templates:

| Prompt Name | Purpose |
|-------------|---------|
| `weekly_review` | 6-step GTD weekly review workflow |
| `daily_planning` | Morning planning routine (5 steps) |
| `inbox_processing` | GTD inbox-to-zero with decision tree |
| `priority_matrix` | Eisenhower matrix prioritization |
| `project_review(project_title)` | Project health check and analysis |

#### 4. Context-Aware Tool Design

All 25+ tools accept optional `Context` parameter for session caching:

```python
@register_tool(name="get_projects")
async def _get_projects_tool(include_items: bool = False, ctx: Context | None = None) -> str:
    return await _build_projects_response(include_items, ctx)
```

Mutations trigger targeted cache invalidation via `invalidate_todos_cache(ctx)`, `invalidate_projects_cache(ctx)`, etc.

#### 5. Dual Sync/Async API Pattern

Each major tool has both async (for MCP context injection) and sync (for tests/direct calls) variants:

```python
# Async for MCP
async def _build_inbox_response(ctx: Context | None) -> str: ...

# Sync wrapper for tests
def get_inbox() -> str:
    return asyncio.run(_build_inbox_response(None))
```

### Features NOT Currently Used

| Feature | Notes |
|---------|-------|
| **Provider Architecture** | No FileSystemProvider, SkillsProvider, OpenAPIProvider, or ProxyProvider. Uses direct `@mcp.tool` decorators instead. |
| **Transforms** | No namespace mounting, renaming, filtering, ResourcesAsTools, or PromptsAsTools. |
| **Component Versioning** | No `@tool(version="2.0")` decorators. Single version of each tool. |
| **ctx.enable_components()** | No per-session dynamic component visibility. |
| **--reload flag** | No hot-reload during development. |
| **Tool timeouts** | No timeout parameter on tool decorators. |
| **Pagination** | Component lists not paginated. |
| **OpenTelemetry tracing** | Custom structured logging instead. |
| **Component Authorization** | No `@tool(auth=require_scopes())` or AuthMiddleware. |

## Architectural Decisions

### Why Session State Over Other Approaches

- **Read-heavy workload**: Things3 API calls are expensive; caching provides massive wins on repeated calls
- **Session isolation**: Each MCP session gets its own cache, avoiding cross-client contamination
- **Targeted invalidation**: Only affected caches cleared on mutations, not entire cache

### Why Response-Level Caching

- Avoids re-running O(n) formatters on each call
- Caches the final output string, not intermediate data
- Parametrized keys (e.g., `include_items`) prevent cache poisoning

### Why FIFO Eviction

- Simple, predictable behaviour
- Oldest entries evicted first
- 500-entry limit provides ample headroom for typical sessions

### Why Not Providers/Transforms

- Single-server architecture doesn't benefit from composition features
- Direct decorator pattern is simpler for this use case
- No need for namespace isolation or mounted sub-servers

## Potential Future Adoptions

| Feature | Potential Use Case |
|---------|-------------------|
| **Tool timeouts** | Prevent slow Things API calls from blocking indefinitely |
| **Component Versioning** | API evolution without breaking existing clients |
| **Transforms** | Namespace mounting if part of larger MCP ecosystem |
| **OpenTelemetry** | Production observability beyond custom logging |
| **Authorization** | Multi-tenant scenarios with scope-based access control |

## Key Files

- `src/things3_mcp/fast_server.py` - Main server with all v3 feature usage
- `src/things3_mcp/logging_config.py` - Structured JSON logging
- `pyproject.toml` - FastMCP dependency (`>=3.0.0b1`)

---

### Update - 2026-02-04 16:55 Europe/Dublin

**Summary**: Implemented tool timeouts across all 24 tools using FastMCP 3.0's `timeout` parameter.

**Changes Made**:
- Added timeout constants to `fast_server.py:84-94`:
  - `TIMEOUT_LIST_VIEW = 15.0` — Simple list reads
  - `TIMEOUT_HEAVY_READ = 45.0` — Reads with nested items
  - `TIMEOUT_SEARCH = 60.0` — Search operations
  - `TIMEOUT_WRITE = 30.0` — Write operations via AppleScript
- Updated `register_tool()` helper to accept `timeout: float | None` parameter
- Applied timeouts to all 24 tools based on category

**Tool Timeout Assignments**:

| Category | Timeout | Tools |
|----------|---------|-------|
| List views | 15s | get_inbox, get_today, get_upcoming, get_anytime, get_someday, get_trash, get_todos, get_random_*, get_tags, get_tagged_items, show_item, get_recent |
| Heavy reads | 45s | get_projects, get_areas, get_logbook |
| Search | 60s | search_todos, search_advanced, search_items |
| Write | 30s | add_todo, add_project, update_todo, update_project |

**Verification**:
- `ruff check` passed
- `mypy` passed

### Update - 2026-02-04 17:05 Europe/Dublin

**Summary**: Updated README.md to highlight FastMCP 3.0 adoption with user-friendly language.

**Changes Made**:
- Added "Built on FastMCP 3.0" section after Features
- **Faster Responses**: Explains caching benefit in plain language ("up to 73% faster")
- **Built-in Productivity Workflows**: Lists 5 GTD prompts with user-friendly descriptions
- **Reliable Connections**: Explains timeouts prevent Claude from hanging
- Positioned this as "one of the first MCP servers" to adopt FastMCP 3.0

**Approach**: Focused on user benefits rather than technical implementation details. Removed cache level tables, resource URIs, and timeout duration tables in favour of plain language explanations.

## Insights

1. **Focused adoption pays off**: By concentrating on session-state caching, the project achieved 30-73% performance improvements without complexity from unused features.

2. **Response-level caching is underrated**: Caching formatted output (not just raw data) eliminates the most expensive repeated work.

3. **Dual API pattern bridges async/sync divide**: Sync wrappers using `asyncio.run()` enable tests and direct Python usage without sacrificing MCP context benefits.

4. **Floating beta dependency is intentional**: `>=3.0.0b1` keeps the project at the bleeding edge of FastMCP development.

## References

- FastMCP 3.0 release notes: https://github.com/jlowin/fastmcp/releases/tag/v3.0.0b1
- Related session: `docs/sessions/2026-01-29-0937-fastmcp3-performance.md`
- Related session: `docs/sessions/2026-01-28-1510-fastmcp3-benchmark.md`
