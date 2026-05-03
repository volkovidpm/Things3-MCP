"""Killable live Things DB read worker.

The bridge parent process invokes this module in a child process with a hard
timeout. Importing and calling ``things-py`` is isolated here so transient MCP
processes do not touch the protected Things group container.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

SNAPSHOT_ACTIONS = ("inbox", "today", "upcoming", "anytime", "someday", "todos", "projects", "areas", "tags")

DB_PATTERNS = (
    "~/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/ThingsData-*/Things Database.thingsdatabase/main.sqlite",
    "~/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/Things Database.thingsdatabase/main.sqlite",
)


def resolve_things_db_path() -> str:
    """Resolve the current Things SQLite path from inside the bridge process."""
    configured = os.environ.get("THINGSDB")
    if configured and Path(configured).exists():
        return configured
    for pattern in DB_PATTERNS:
        matches = sorted(glob.glob(os.path.expanduser(pattern)))
        for match in matches:
            if Path(match).exists():
                return match
    raise FileNotFoundError("Could not locate Things SQLite database under the Things group container")


def diagnose_access() -> dict[str, Any]:
    """Return local DB-path diagnostics from the bridge identity."""
    patterns: list[dict[str, Any]] = []
    for pattern in DB_PATTERNS:
        expanded = os.path.expanduser(pattern)
        matches = sorted(glob.glob(expanded))
        patterns.append({"pattern": expanded, "matches": matches})
    try:
        path = resolve_things_db_path()
        exists = Path(path).exists()
        readable = os.access(path, os.R_OK)
        sqlite_ok = False
        sqlite_error = None
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
                connection.execute("select 1").fetchone()
                sqlite_ok = True
        except Exception as exc:  # noqa: BLE001 - diagnostic only
            sqlite_error = str(exc)
        return {"ok": True, "path": path, "exists": exists, "readable": readable, "sqlite_ok": sqlite_ok, "sqlite_error": sqlite_error, "patterns": patterns}
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return {"ok": False, "error": str(exc), "patterns": patterns}


def direct_provider() -> Any:
    """Load the direct provider only after pinning THINGSDB to the resolved path."""
    os.environ.setdefault("THINGSDB", resolve_things_db_path())
    from things3_mcp.providers.direct import DirectThingsProvider

    return DirectThingsProvider()


def run_action(action: str, params: dict[str, Any] | None = None) -> Any:
    """Run a read action against the direct Things provider."""
    params = params or {}
    if action == "diagnose":
        return diagnose_access()
    provider = direct_provider()
    if action == "snapshot":
        return {
            "inbox": provider.inbox(include_items=True),
            "today": provider.today(include_items=True),
            "upcoming": provider.upcoming(include_items=True),
            "anytime": provider.anytime(include_items=True),
            "someday": provider.someday(include_items=True),
            "todos": provider.todos(include_items=True),
            "projects": provider.projects(include_items=False),
            "areas": provider.areas(include_items=False),
            "tags": provider.tags(include_items=False),
        }
    if action == "search":
        return provider.search(params.get("query", ""), include_items=bool(params.get("include_items", True)))
    if action == "get":
        return provider.get(params["uuid"])
    if action in {"tasks", "todos"}:
        return getattr(provider, action)(**params)
    if action in SNAPSHOT_ACTIONS:
        include_items = bool(params.get("include_items", True))
        return getattr(provider, action)(include_items=include_items)
    raise ValueError(f"Unsupported Things bridge action: {action}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for child worker reads."""
    parser = argparse.ArgumentParser(description="Things3 MCP bridge read worker")
    parser.add_argument("action")
    parser.add_argument("--params", default="{}")
    args = parser.parse_args(argv)

    try:
        params = json.loads(args.params)
        result = run_action(args.action, params)
        print(json.dumps({"ok": True, "data": result}))
        return 0
    except Exception as exc:  # noqa: BLE001 - serialize worker failures to parent
        print(json.dumps({"ok": False, "error_code": "things_db_unreadable", "message": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
