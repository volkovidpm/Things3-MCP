# Session: Signed Local Bridge for Robust Things Access

**Date**: 2026-05-03
**Status**: In Progress
**Branch**: `feature/signed-local-bridge`
**Worktree**: `.worktrees/signed-local-bridge`

## Goal

Implement a Things3-MCP architecture that gives agent clients robust AFK read access to Things data without repeatedly triggering macOS TCC prompts for transient runtimes such as `node`, Homebrew Python, virtualenv console scripts, or `/usr/bin/osascript`.

Target outcome for Ross/Clawd:

- The MCP server can call `get_inbox`, `get_today`, and `search_todos` while Ross is AFK.
- If macOS authorization is missing, the MCP server does not hang or spawn repeated prompts; it returns a clear bridge authorization/cache status.
- Once the bridge is authorized once, later MCP reads work through a stable signed macOS identity rather than whatever process launched the MCP server.

## Important Constraint

macOS TCC permissions cannot be silently granted by code. A signed/self-signed helper can provide a stable identity, but the first authorization still requires either:

1. Ross granting Full Disk Access / App Data / Automation to the helper in System Settings, or
2. an MDM/configuration profile installed by the user/admin.

Therefore “no permission prompt ever” is only achievable by avoiding live protected access and serving stale cache. The real durable success criterion is: no recurring prompts after a one-time helper authorization, and no AFK wedging/prompts from the MCP process.

## Proposed Architecture

```text
Claude/OpenClaw/other MCP client
  -> Things3-MCP server
     -> BridgeThingsProvider client
        -> local signed Things3 MCP Bridge.app / LaunchAgent
           -> direct Things SQLite read
           -> optional Things AppleEvents/URL-scheme writes
           -> cache snapshot under Application Support
```

## Implementation Phases

### Phase 1 — Provider Abstraction

Create a provider layer so MCP tools stop importing/calling `things-py` directly.

New package/module shape:

```text
src/things3_mcp/providers/
  __init__.py
  base.py
  direct.py
  bridge.py
  cache.py
```

`base.py` should define a minimal protocol/interface used by `fast_server.py`:

```python
class ThingsProvider(Protocol):
    def inbox(self, include_items: bool = True) -> list[dict]: ...
    def today(self, include_items: bool = True) -> list[dict]: ...
    def upcoming(self, include_items: bool = True) -> list[dict]: ...
    def anytime(self, include_items: bool = True) -> list[dict]: ...
    def someday(self, include_items: bool = True) -> list[dict]: ...
    def tasks(self, **kwargs) -> list[dict]: ...
    def search(self, query: str, include_items: bool = True) -> list[dict]: ...
    def get(self, uuid: str) -> dict | None: ...
    def projects(self, include_items: bool = False) -> list[dict]: ...
    def areas(self, include_items: bool = False) -> list[dict]: ...
    def tags(self, include_items: bool = False) -> list[dict]: ...
```

`direct.py` wraps the existing `things-py` module, preserving current behaviour.

`bridge.py` talks to a local bridge API.

Provider selection:

- `THINGS3_MCP_PROVIDER=auto` default
- `THINGS3_MCP_PROVIDER=direct`
- `THINGS3_MCP_PROVIDER=bridge`
- `THINGS3_MCP_PROVIDER=cache`

Auto mode should try bridge, then cache, then direct only if explicitly allowed by `THINGS3_MCP_ALLOW_DIRECT_FALLBACK=1`. Default auto mode should not hit protected DB directly from the MCP server when bridge/cache is unavailable, because that recreates the TCC prompt problem.

### Phase 2 — Local Bridge API

Add a bridge server package:

```text
src/things3_mcp_bridge/
  __init__.py
  server.py
  db_reader.py
  cache.py
  auth_status.py
  protocol.py
```

Expose a console entry point:

```toml
[project.scripts]
Things3-MCP-server = "things3_mcp.fast_server:run_things_mcp_server"
Things3-MCP-bridge = "things3_mcp_bridge.server:main"
```

Bridge transport:

- Prefer Unix domain socket at:
  `~/Library/Application Support/Things3-MCP/bridge.sock`
- Optional HTTP fallback bound to `127.0.0.1` only.
- Require a local token file with mode `0600`:
  `~/Library/Application Support/Things3-MCP/bridge.token`

Bridge endpoints/commands:

- `GET /health`
- `GET /auth-status`
- `POST /snapshot`
- `GET /cache/status`
- `GET /things/inbox`
- `GET /things/today`
- `GET /things/upcoming`
- `GET /things/anytime`
- `GET /things/someday`
- `POST /things/tasks`
- `POST /things/search`
- `GET /things/projects`
- `GET /things/areas`
- `GET /things/tags`

Response envelope:

```json
{
  "ok": true,
  "source": "live|cache",
  "generated_at": "2026-05-03T12:06:00+01:00",
  "cache_age_seconds": 12,
  "data": []
}
```

Error envelope:

```json
{
  "ok": false,
  "error_code": "bridge_not_authorized|things_db_timeout|things_db_unreadable|cache_missing|cache_stale|bridge_unavailable",
  "message": "Human-readable explanation",
  "authorization_hint": "Grant Full Disk Access to Things3 MCP Bridge.app",
  "cache_status": { "available": false }
}
```

### Phase 3 — Safe Privileged Read Worker

All protected filesystem access must happen inside a killable worker process, not the bridge server's main loop.

Requirements:

- No `Path.exists()`, `glob`, `sqlite3.connect()`, or `things-py` default path resolution against the Things Group Container in the MCP process.
- In the bridge, run protected DB access in a child worker with a hard timeout.
- Parent kills worker after timeout and returns `things_db_timeout`.
- Use read-only SQLite URI: `file:/path/main.sqlite?mode=ro&immutable=1` where safe.
- Explicitly locate Things DB inside the worker only:
  `~/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/ThingsData-*/Things Database.thingsdatabase/main.sqlite`
- On successful live read, atomically write cache.

Cache path:

```text
~/Library/Application Support/Things3-MCP/cache/
  latest.json
  latest.sqlite? optional future
  snapshots/YYYYMMDD-HHMMSS.json
```

Atomic write pattern:

1. Write `.tmp` file
2. `fsync`
3. Rename to `latest.json`

### Phase 4 — Signed App Bundle / LaunchAgent Packaging

Add packaging scripts:

```text
scripts/build_bridge_app.sh
scripts/sign_bridge_app.sh
scripts/install_bridge_launchagent.sh
scripts/uninstall_bridge_launchagent.sh
scripts/check_bridge_authorization.py
packaging/macos/Things3 MCP Bridge.app/Contents/Info.plist.template
packaging/macos/com.rossshannon.things3-mcp.bridge.plist.template
```

Bundle identity:

- `CFBundleIdentifier`: `com.rossshannon.things3-mcp.bridge`
- App name: `Things3 MCP Bridge`
- Binary name: `Things3-MCP-bridge`

Signing modes:

- `--identity "Things3 MCP Local"` for local self-signed cert.
- `--identity -` for ad-hoc fallback, clearly documented as less stable.
- Future: Developer ID signing for public release.

LaunchAgent:

```text
~/Library/LaunchAgents/com.rossshannon.things3-mcp.bridge.plist
```

Runs the app's embedded bridge binary at login, with stdout/stderr logs under:

```text
~/Library/Logs/Things3-MCP/bridge.log
~/Library/Logs/Things3-MCP/bridge.err.log
```

### Phase 5 — MCP Tool Integration

Update `fast_server.py` to import/use provider facade instead of direct `things` calls for read paths.

Priority tools for first implementation:

1. `get_inbox`
2. `get_today`
3. `get_upcoming`
4. `search_todos`
5. `get_projects`
6. `get_areas`
7. `get_tags`

Writes can remain existing AppleScript/URL scheme initially, but must be documented as not AFK-safe until moved through the bridge.

### Phase 6 — CLI Commands

Add diagnostic commands or scripts:

```bash
Things3-MCP-bridge --health
Things3-MCP-bridge --snapshot-once
Things3-MCP-bridge --socket ~/Library/Application\ Support/Things3-MCP/bridge.sock
python scripts/check_bridge.py
```

`check_bridge.py` should print:

- Bridge running? yes/no
- Bundle path
- Code signature summary (`codesign -dv`)
- Socket reachable? yes/no
- Cache available? yes/no
- Last successful snapshot time
- Authorization status inferred from live snapshot result
- Next human action if needed

### Phase 7 — Tests

Add tests that do not require real Things DB:

- Provider selection tests
- Bridge client envelope parsing
- Cache fallback tests
- Worker timeout test using a fake hanging worker
- Atomic cache write/read tests
- MCP read tool tests with fake Bridge provider

Acceptance command target:

```bash
uv run pytest tests
uv run ruff check .
uv run ruff format --check .
```

### Phase 8 — Local Acceptance on Exocortex

Local success sequence:

```bash
# Build bridge app
scripts/build_bridge_app.sh
scripts/sign_bridge_app.sh --identity "Things3 MCP Local"

# Install launch agent
scripts/install_bridge_launchagent.sh

# Check without causing repeated prompts
python scripts/check_bridge.py

# If authorized, snapshot live Things data
Things3-MCP-bridge --snapshot-once

# Force MCP to use bridge/cache only
THINGS3_MCP_PROVIDER=auto THINGS3_MCP_ALLOW_DIRECT_FALLBACK=0 \
  mcporter call things.get_inbox
```

Expected successful result:

- `mcporter call things.get_inbox` returns real Things data or a clear cache response.
- No `node would like to access data from other apps` prompt appears because the MCP process no longer accesses the protected Things Group Container.
- If the bridge lacks permission, MCP returns `bridge_not_authorized` or cache fallback, not a hung tool call.

## Codex Implementation Instructions

1. Work only in `.worktrees/signed-local-bridge` on branch `feature/signed-local-bridge`.
2. Preserve existing FastMCP 3.0 branch history; do not modify `/Users/ross/Development/Things3-MCP` main checkout.
3. Keep the first PR minimal: provider abstraction + bridge/cache for read tools + packaging scripts. Do not fully rewrite write operations unless time remains.
4. Do not attempt to modify TCC databases or bypass macOS privacy controls.
5. Never run commands that delete Ross's Things data or write to the Things DB directly.
6. Prefer read-only DB access and cache snapshots.
7. If a command risks a TCC prompt, run it only through the bridge worker with timeout and document the observed result.
8. Commit in logical chunks.
9. End with a session update and exact local verification evidence.

## First Milestone Definition of Done

- Branch compiles/tests.
- `get_inbox` and `get_today` use provider abstraction.
- Bridge server can serve fake/test data from cache.
- On Exocortex, MCP read paths do not directly touch the Things Group Container.
- If live bridge authorization is absent, calls fail fast with clear status.
- If live bridge authorization is present, calls return real Things data through the bridge.

## Implementation Update — 2026-05-03 12:20 Europe/Dublin

### Implemented

- Added `things3_mcp.providers` abstraction with `direct`, `bridge`, `cache`, and `auto` provider modes.
- Added conservative provider selection:
  - `THINGS3_MCP_PROVIDER=auto` defaults to bridge → cache.
  - Direct Things DB fallback requires `THINGS3_MCP_ALLOW_DIRECT_FALLBACK=1`.
- Converted these MCP read tools to use the provider facade:
  - `get_inbox`
  - `get_today`
  - `get_upcoming`
  - `get_projects`
  - `get_areas`
  - `get_tags`
  - `search_todos`
- Updated formatters so provider-backed read tools resolve project/area/item details through the selected provider instead of directly calling `things-py` from formatting code.
- Added `things3_mcp_bridge` package and `Things3-MCP-bridge` console entry point.
- Added local bridge server scaffolding:
  - Unix socket default: `~/Library/Application Support/Things3-MCP/bridge.sock`
  - token file default: `~/Library/Application Support/Things3-MCP/bridge.token`
  - `/health`, `/auth-status`, `/cache/status`, `/snapshot`, `/things/...` endpoints
  - live read worker via killable child process with hard timeout
  - cache fallback for bridge read failures
- Added JSON snapshot cache with atomic write/read support.
- Added diagnostics and macOS packaging templates:
  - `scripts/check_bridge.py`
  - `scripts/build_bridge_app.sh`
  - `scripts/sign_bridge_app.sh`
  - `scripts/install_bridge_launchagent.sh`
  - `scripts/uninstall_bridge_launchagent.sh`
  - `packaging/macos/Things3 MCP Bridge.app/Contents/Info.plist.template`
  - `packaging/macos/com.rossshannon.things3-mcp.bridge.plist.template`
- Added provider/cache/bridge tests that avoid real Things DB access.

### Validation Run

- `uv run --locked pytest tests/test_provider_bridge_cache.py` → 6 passed.
- `uv run --locked ruff check .` → passed.
- `uv run --locked ruff format --check .` → passed.
- `uv run --locked Things3-MCP-bridge --health` → returned bridge/cache health without live Things DB access.
- `scripts/build_bridge_app.sh` → built `build/macos/Things3 MCP Bridge.app`.
- `uv run --locked python scripts/check_bridge.py` → reported bridge not running, no installed bundle, no cache; no protected Things access attempted.
- `THINGS3_MCP_PROVIDER=auto THINGS3_MCP_ALLOW_DIRECT_FALLBACK=0 uv run --locked python - <<'PY' ... get_inbox()` → returned a fast `cache_missing` provider diagnostic instead of direct Things DB access.

Full existing integration suite was intentionally not run because it creates/modifies Things tasks through AppleScript and would write to Ross's Things database.

### Authorization / Live Things Status

Live Things access was **not verified** in this pass. I did not run `Things3-MCP-bridge --snapshot-once` because it would trigger protected Things DB access and may require macOS authorization. The implemented path now isolates that read behind the bridge worker and reports `things_db_timeout` / `things_db_unreadable` / authorization hints rather than letting the MCP process wedge.

### Known Follow-ups

- Build, sign, install, and authorize `Things3 MCP Bridge.app` as a one-time local setup step.
- Run `Things3-MCP-bridge --snapshot-once` after authorization to populate `latest.json`.
- Consider moving remaining read tools (`get_anytime`, random tools, `get_todos`, `search_items`, `search_advanced`, logbook/trash/tagged reads) onto the provider facade in a follow-up PR.
- Consider richer cache query support for filtered `tasks()` beyond simple snapshot/search fallback.
