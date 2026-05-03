#!/usr/bin/env python3
"""Diagnose the local Things3 MCP bridge without touching Things data directly."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from things3_mcp.providers.bridge import DEFAULT_SOCKET, DEFAULT_TOKEN_FILE, BridgeThingsProvider
from things3_mcp.providers.cache import CacheStore

APP_BUNDLE = Path.home() / "Applications" / "Things3 MCP Bridge.app"


def run_codesign_summary(bundle: Path) -> str:
    """Return a compact code signature summary, if available."""
    if not bundle.exists():
        return "bundle not found"
    if not shutil.which("codesign"):
        return "codesign not found"
    result = subprocess.run(["codesign", "-dv", str(bundle)], capture_output=True, text=True, check=False)  # noqa: S603, S607 - diagnostic fixed argv
    return (result.stderr or result.stdout or "no codesign output").strip()


def main() -> int:
    """Print bridge diagnostics as JSON."""
    diagnostics = {
        "bridge_running": False,
        "bundle_path": str(APP_BUNDLE),
        "bundle_exists": APP_BUNDLE.exists(),
        "code_signature": run_codesign_summary(APP_BUNDLE),
        "socket_path": str(DEFAULT_SOCKET),
        "socket_reachable": False,
        "token_file": str(DEFAULT_TOKEN_FILE),
        "token_file_exists": DEFAULT_TOKEN_FILE.exists(),
        "cache": CacheStore().status(),
        "authorization_status": None,
        "next_human_action": "If live reads fail, grant Full Disk Access to Things3 MCP Bridge.app and run Things3-MCP-bridge --snapshot-once.",
    }

    if DEFAULT_SOCKET.exists() and DEFAULT_TOKEN_FILE.exists():
        provider = BridgeThingsProvider()
        try:
            diagnostics["health"] = provider.health()
            diagnostics["bridge_running"] = True
            diagnostics["socket_reachable"] = True
            diagnostics["authorization_status"] = "bridge reachable; run snapshot-once to verify live Things authorization"
        except Exception as exc:  # noqa: BLE001 - diagnostic command
            diagnostics["health_error"] = str(exc)

    print(json.dumps(diagnostics, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
