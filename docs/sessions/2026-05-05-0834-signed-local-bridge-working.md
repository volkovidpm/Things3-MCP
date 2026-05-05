# Signed Local Bridge — Working on macOS Tahoe

**Date**: 2026-05-05 08:34 UTC
**Branch**: `feature/signed-local-bridge`
**Status**: Live SQLite reads working end-to-end via signed bundle

## TL;DR

The signed-local-bridge architecture works on macOS 26 Tahoe with a self-signed
Code Signing certificate. No Apple Developer ID required, no notarization
required. The path that worked:

1. Self-sign the PyInstaller `.app` with a local cert (`Things3 MCP Local`).
2. LaunchAgent plist with `AssociatedBundleIdentifiers`, `THINGS3_MCP_NO_DISCLAIM=1`.
3. Bundle Info.plist with `LSUIElement`, `NSAppleEventsUsageDescription`.
4. Reboot the Mac to clear Tahoe's TCC responsibility cache.
5. Bootstrap the LaunchAgent post-reboot from a clean shell (Warp / Terminal.app
   / iTerm — anything not running OpenClaw's Bun daemon).
6. Grant Full Disk Access to the bundle in System Settings.
7. SQLite reads succeed; cache populates with real Things data.

Verified data flow end-to-end:
- `inbox`: 39 items
- `today`: 27 items
- `anytime`: 1524 items
- `projects`: 141, `areas`: 21, `tags`: 316
- Cache file: `~/Library/Application Support/Things3-MCP/cache/latest.json` (2.9 MB)

## Dead-end attempts (documented to save future debugging)

### 1. `responsibility_spawnattrs_setdisclaim()` — broken on Tahoe

We added `src/things3_mcp_bridge/_disclaim.py` with ctypes wrappers for
`posix_spawn` + `responsibility_spawnattrs_setdisclaim` to make the worker
its own TCC responsible code regardless of parent poisoning. The function is
documented (Apple DTS forum threads, Mojave-era articles, the disclaim repo at
github.com/torarnv/disclaim) but on Tahoe it returns `EINVAL` (errno 22)
unconditionally — confirmed by a pure-C reproducer (`/tmp/disclaim_test.c`).

The module is retained but gated on `THINGS3_MCP_NO_DISCLAIM=1` (set by default
in the LaunchAgent plist). If Apple ever fixes the Tahoe regression, removing
the env var re-enables disclaim without other changes.

### 2. Broader cert trust (`security add-trusted-cert -p basic`)

`spctl --assess` rejected the bundle on Tahoe even with the cert added as a
trusted root in both user and admin domains, with `-p codeSign` and `-p basic`.
The 6–9 second `spctl` time suggested an online notarization check that
self-signed code can't pass. This was a red herring — `spctl --assess` is
distribution-policy validation, not what TCC uses for runtime attribution.
TCC matches on bundle identity / DR / csreq, which a self-signed cert satisfies
fine once the responsibility chain is clean.

### 3. Tahoe TCC responsibility caching

Before the reboot, `tcc.db` and/or `launchd` had cached "the bridge's
responsibility = Bun (OpenClaw daemon, `pid=7254`)" — confirmed by
`AUTHREQ_ATTRIBUTION` log lines showing `responsible_path=/opt/homebrew/Cellar/bun/1.3.6/bin/bun`
during every snapshot attempt. Killing the OpenClaw daemon and re-bootstrapping
the bridge was insufficient to clear this; only a full reboot wiped the cache.
Post-reboot, the bridge correctly self-attributes to its own bundle code identity,
matches the FDA grant, and reads succeed.

## What's wired up now

- `packaging/macos/com.rossshannon.things3-mcp.bridge.plist.template`
  - `AssociatedBundleIdentifiers` → `com.rossshannon.things3-mcp.bridge`
  - `THINGS3_MCP_NO_DISCLAIM=1` (so the broken disclaim path is bypassed)
- `packaging/macos/Things3 MCP Bridge.app/Contents/Info.plist.template`
  - `LSUIElement=true` (replaces `LSBackgroundOnly` so TCC prompts can surface)
  - `NSAppleEventsUsageDescription` (required for any future Apple Events use)
- `scripts/install_bridge_launchagent.sh`
  - Refuses to install if the calling shell tree contains a literal Bun runtime
    (`bun server.ts`–style invocation). Heuristic — won't catch Bun-compiled
    binaries like `claude`. Override with `THINGS3_MCP_INSTALL_FORCE=1`.
  - Uses `launchctl bootout` + `launchctl bootstrap` instead of legacy
    `unload`/`load` for cleaner re-registration.
- `src/things3_mcp_bridge/db_reader.py`
  - Worker enumerates the protected Things group container directly (it has FDA).
  - `thingscli defaults` discovery path provides a fast hint for the
    `ThingsData-XXXX` folder name without needing FDA on the calling process.
  - Stage-by-stage stderr traces (`sqlite-attempt`, `sqlite-ok`, `jxa-attempt`,
    `jxa-failed`) flow into `bridge.err.log` for diagnosis.
- `src/things3_mcp_bridge/server.py`
  - Worker stderr passes through to bridge stderr (was previously captured).
  - `THINGS3_MCP_NO_DISCLAIM` env var honored.
  - `/diagnose` endpoint exposes the worker's `diagnose_access()` output
    over the bridge socket.
- `scripts/seed_cache.py` — bypasses the bridge entirely; runs Apple Events
  directly from the calling terminal (which already has Automation grant) and
  writes a snapshot to the cache file. Useful escape hatch if the signed bridge
  ever breaks again.

## Operational rules (write these into the README)

1. **Never `launchctl bootstrap` the bridge from OpenClaw, Claude Desktop, or
   any other Bun-running daemon.** The bridge inherits Bun as its TCC
   responsible code and silently fails. Always install/reinstall from
   Terminal.app, Warp, iTerm, or another non-Bun shell.

2. **If the bridge starts silently failing (worker hangs at `sqlite-attempt`),
   check `tccd` log for `responsible_path`.** If it shows Bun or any
   non-bridge identity, you've been re-poisoned. The fix is a reboot followed
   by a fresh bootstrap.

3. **Don't enable Hardened Runtime** (`codesign --options runtime`) unless you
   also generate an entitlements file with `com.apple.security.cs.allow-jit`,
   `cs.allow-unsigned-executable-memory`, `cs.disable-library-validation`.
   Self-signed PyInstaller bundles crash under Hardened Runtime without these.

4. **Don't run `tccutil reset SystemPolicyAllFiles com.rossshannon.things3-mcp.bridge`
   casually.** It removes the bundle from FDA, requiring a re-toggle in
   System Settings. Reserve for csreq mismatch debugging.

## Validation done in this session

- `uv run --locked pytest tests/test_provider_bridge_cache.py` → 11 passed
- `uv run --locked ruff check .` → All checks passed!
- `uv run --locked ruff format --check .` → 31 files already formatted
- `uv run --locked mypy src/` → Success: no issues found in 18 source files
- Bridge `/snapshot` → `{"ok": true, "source": "live", ...}` (live SQLite read)
- Bridge `/things/inbox` → 39 items, real UUIDs and titles
- Bridge `/things/today` → 27 items, real area/tag/project metadata
- Cache file written to `~/Library/Application Support/Things3-MCP/cache/latest.json` (2.9 MB)
- TCC log during success: zero `responsible_path` entries — clean code-identity
  match against the bundle's FDA grant, no responsibility-chain walking needed

## Next steps (not blocking)

1. **Bridge writes** via Apple Events. The `NSAppleEventsUsageDescription`
   purpose string is in place; first AE call from the bridge will prompt for
   Automation grant. Then move existing AppleScript write helpers to run
   inside the bridge worker so writes are also AFK-safe.

2. **Pre-flight prompt** subcommand. `Things3-MCP-bridge --authorize-once`
   that calls `AEDeterminePermissionToAutomateTarget(..., askUserIfNeeded=True)`
   to surface the Automation prompt under the bundle's identity, before the
   first MCP call needs it.

3. **`Things3-MCP-bridge --doctor`** subcommand exposing `check_bridge.py`-like
   diagnostics with exact remediation strings (e.g. "Open System Settings →
   Privacy & Security → Full Disk Access → toggle Things3 MCP Bridge.app on").

4. **MCP tool `readOnlyHint: true`** annotations so Claude Desktop and other
   clients auto-approve read calls without per-tool prompts.

5. **Strip the disclaim module** if it's confirmed not needed for any rollback
   scenario. Until then, leaving it in place gated on the env var costs
   nothing.

## Key files touched in this PR

- `src/things3_mcp_bridge/server.py` — disclaim wiring, env-var gating, diagnose endpoint, stderr flow-through
- `src/things3_mcp_bridge/db_reader.py` — worker glob enumeration, thingscli defaults, FDA probe, stage tracing
- `src/things3_mcp_bridge/_disclaim.py` — new ctypes wrapper for posix_spawn + disclaim (currently disabled)
- `src/things3_mcp_bridge/cache.py` — unchanged
- `scripts/build_bridge_app.sh` — Info.plist patches (LSUIElement, NSAppleEventsUsageDescription)
- `scripts/sign_bridge_app.sh` — unchanged
- `scripts/install_bridge_launchagent.sh` — Bun-shell guard, bootout/bootstrap modernization
- `scripts/seed_cache.py` — new escape-hatch for cache priming
- `packaging/macos/com.rossshannon.things3-mcp.bridge.plist.template` — AssociatedBundleIdentifiers, NO_DISCLAIM env
- `packaging/macos/Things3 MCP Bridge.app/Contents/Info.plist.template` — LSUIElement, AE purpose string
- `tests/test_provider_bridge_cache.py` — updated to reflect worker-glob-allowed semantics

---

## Update — 2026-05-05 10:41 IST: Phase B (writes) + Phase C (remaining reads)

After confirming the architecture worked end-to-end for live SQLite reads, I
extended the same pattern to writes (Phase B) and the 8 read tools that were
still bypassing the provider (Phase C). All 18 MCP tools that talk to Things
now route through the same `AutoThingsProvider` chain.

### What shipped in Phase B

**Write API on the provider:**
- `WriteThingsProvider` protocol + `WriteUnsupportedError` in `providers/base.py`
- `BridgeThingsProvider.add_task` / `update_task` / `add_project` / `update_project`
  POST and PATCH over the Unix socket with the same JSON envelope as reads
- `CacheThingsProvider` raises `WriteUnsupportedError` for any write — cache
  cannot satisfy mutations and silent fallback would lose data
- `DirectThingsProvider` write methods preserved as opt-in fallback when
  `THINGS3_MCP_ALLOW_DIRECT_FALLBACK=1`. They call the existing
  `applescript_bridge` from inside the MCP server process — same as the old
  pre-bridge behaviour, so the fallback path is identical to historical
  semantics
- `AutoThingsProvider._call_write` chains bridge → optional direct, **never
  cache**. Writes that can't reach Things must surface as errors.

**Bridge HTTP write endpoints:**
- `POST /things/todo` → `run_worker("add_task", body)`
- `PATCH /things/todo/{uuid}` → `run_worker("update_task", body+uuid)`
- `POST /things/project` → `run_worker("add_project", body)`
- `PATCH /things/project/{uuid}` → `run_worker("update_project", body+uuid)`
- New `do_PATCH` HTTP handler with auth + body parsing
- `_run_write` helper deliberately omits cache fallback

**Worker write dispatch in `db_reader.py`:**
- `WRITE_ACTIONS = {"add_task", "update_task", "add_project", "update_project"}`
- `run_write_action()` lazily imports `applescript_bridge` and dispatches
- `_coerce_write_result()` normalises legacy AppleScript return values
  (UUID strings, `"true"`, `False`, `"Error: …"` markers) into clean envelopes
- `run_action()` recognises write actions before the SQLite/JXA branches
- Stage tracing `[worker:PID] write-attempt {action}` → `write-ok` / `write-failed`

**MCP routing:**
- `fast_server.add_task`, `add_new_project`, `update_task`, `update_existing_project`
  now call `get_provider().add_task(...)` etc. instead of importing
  `applescript_bridge` directly. Removed unused imports.

### What shipped in Phase C

**Two new methods on `ThingsProvider`:**
- `trash(include_items)` — wraps `things.trash()`
- `last(period, include_items)` — wraps `things.last(period)`

These were the only things-py top-level functions not already covered by the
existing protocol; everything else fits `tasks(**kwargs)` or `todos(**kwargs)`.

**New bridge endpoints:**
- `GET /things/trash`
- `GET /things/last/{period}` — period segment lifted from URL path

**Worker dispatcher (`run_sqlite_action`)** handles `trash` and `last`
explicitly because their kwargs shapes differ from the other read actions.

**MCP routing for the 8 lifted tools:**
- `get_logbook` → `provider.tasks(status="completed", stop_date=…)`
- `get_trash` → `provider.trash()`
- `get_todos` → `provider.todos(project=…)` and `provider.get(uuid)`
- `get_random_todos` → same, plus `_sample_items`
- `get_tagged_items` → `provider.todos(tag=…)`
- `search_advanced` → `provider.todos(**filters)`
- `search_all_items` → `provider.search(query)`
- `get_recent` → `provider.last(period)`

All catch `ProviderError` and route through `_provider_error_response`. All
use `_format_todo_items(todos, provider)` for output.

### New tests added

| Phase | Count | Coverage |
|---|---|---|
| B | 9 | Bridge HTTP write client, cache refusal, auto-provider write chain, worker write dispatch, applescript-bridge return-value coercion |
| C | 16 | All 8 lifted MCP tools route through provider, period validation, provider write/read protocol surface for `trash`/`last`, worker dispatch for `trash`/`last`, cache empty-list semantics |

Total: **36 unit tests passing** (was 11).

### End-to-end smoke tests done in this session

All against the user's real Things database:

| Operation | Endpoint | Result |
|---|---|---|
| Create todo | `POST /things/todo` | new UUID `Ua8DgKsHcWJGUwQrYSQcNk` |
| Read it back | `GET /things/get/{uuid}` | SQLite returned correct title/notes |
| Update title + notes | `PATCH /things/todo/{uuid}` | `{"ok": true}` |
| Cancel | `PATCH /things/todo/{uuid}` `{"canceled":true}` | `{"ok": true}` |
| List trash | `GET /things/trash` | Real trashed items returned |
| Recent items | `GET /things/last/7d` | Real recent activity returned |
| Live snapshot | `POST /snapshot` | 39 inbox / 27 today / 1524 anytime / 141 projects |

Worker traces showed every operation flowing as `write-attempt` → `write-ok`
or `sqlite-attempt` → `sqlite-ok`. No TCC prompts were needed for writes
because Automation had already been granted to the bundle from earlier JXA
testing in this session.

### Updated lessons learned

7. **`spctl --assess` rejection is not the same as TCC denial.** Apple DTS
   warns that spctl is a distribution-policy check (notarization, Developer
   ID), not the runtime trust evaluation TCC actually uses. Self-signed code
   that spctl rejects can still pass TCC csreq matching just fine. We chased
   this red herring for an hour before pivoting.

8. **`responsibility_spawnattrs_setdisclaim` is broken on Tahoe.** A pure-C
   reproducer (`/tmp/disclaim_test.c`) confirms `EINVAL` even with the
   correct calling convention (`posix_spawnattr_t` by value, not by
   pointer). The dead-end is preserved in `_disclaim.py` and gated off via
   `THINGS3_MCP_NO_DISCLAIM=1` in the LaunchAgent plist.

9. **macOS Tahoe (26+) caches per-bundle-ID launchd responsibility.** The
   bridge was originally bootstrapped from an OpenClaw (Bun) context, which
   stamped its responsibility chain. Killing OpenClaw and re-bootstrapping
   was insufficient — the cache survived. Only a reboot cleared it. After
   reboot, the bridge auto-loaded with a clean responsibility chain and has
   stayed clean across re-installs from various shells.

10. **Reboot was the actual fix, not Developer ID.** We had a strong
    hypothesis that self-signed code couldn't get FDA on Tahoe and were
    about to recommend $99/yr Developer ID. The empirical evidence said
    otherwise once we cleared the responsibility cache.

11. **Writes intentionally don't cache-fall-back.** `AutoThingsProvider`'s
    write chain is `bridge → optional direct`, never cache. Silent fallback
    to a stale cache for mutations would lose data — clear `ProviderError`
    surfacing is correct.

12. **`spctl --assess` takes 6–9 seconds because it's making a network call.**
    Likely OCSP / CRL revocation against Apple's servers. Useful timing
    fingerprint for diagnosing whether it's hitting the network vs failing
    locally.

### Validation in this update

```
uv run --locked pytest tests/test_provider_bridge_cache.py  → 36 passed
uv run --locked ruff check .                                 → All checks passed!
uv run --locked ruff format --check .                        → 31 files already formatted
uv run --locked mypy src/                                    → no issues found in 18 source files
pre-commit hooks (ruff/format/mypy/bandit/whitespace/eof)    → all passed
```

### Commits added in this session

| Commit | Scope |
|---|---|
| `e4ed992` | Add bridge write API and resolve Tahoe TCC attribution |
| `63443e8` | Route remaining 8 read tools through the bridge provider |

### Final state

Every TCC-protected Things operation in the MCP server now flows through
`com.rossshannon.things3-mcp.bridge` — the single signed bundle that holds
FDA + Automation grants. Whatever transient runtime hosts the MCP server
(Claude Desktop, OpenClaw, terminal-launched, Codex), it inherits no
responsibility for Things data access; the bridge does the work under its
stable identity.

| Layer | Tools | Routed through bridge |
|---|---|---|
| MCP read tools | 19 | 19/19 ✓ |
| MCP write tools | 4 | 4/4 ✓ |
| Bridge endpoints | 17 (GET + POST + PATCH) | live + cache fallback (reads only) |
| Provider chain | bridge → cache (reads) / bridge → direct (writes) | yes |

### Updated next steps (still non-blocking)

1. **`Things3-MCP-bridge --authorize-once` preflight** — call
   `AEDeterminePermissionToAutomateTarget(..., askUserIfNeeded=True)` so the
   first-time Automation prompt happens explicitly rather than on the first
   write attempt. Still relevant; not done.
2. **`--doctor` subcommand** — wrap `check_bridge.py` with exact-remediation
   strings (settings paths, `tccutil reset` commands, `launchctl bootout`
   guidance). Still relevant; not done.
3. **`readOnlyHint: true` MCP annotations** — auto-approval in Claude Desktop
   for the 19 read tools. Still relevant; not done.
4. **Decide on `_disclaim.py` long-term** — keep gated as future-proofing for
   if Apple un-breaks the API, or strip as dead code. No action needed.
5. **Mark integration tests with `@pytest.mark.integration`** — the ~70
   tests that modify real Things data should be opt-in via `pytest -m
   integration`. Currently they run by default, which we deliberately avoid.
6. **Cleanup**: the smoke-test todo `bridge-write-smoke-test (updated, delete
   me)` (UUID `Ua8DgKsHcWJGUwQrYSQcNk`) is marked canceled in your Things
   logbook. Empty trash to remove permanently.
