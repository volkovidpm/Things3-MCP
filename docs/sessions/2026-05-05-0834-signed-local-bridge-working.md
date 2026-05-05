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
