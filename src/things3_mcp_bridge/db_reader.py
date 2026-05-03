"""Killable live Things DB read worker.

The bridge parent process invokes this module in a child process with a hard
timeout. Importing and calling ``things-py`` is isolated here so transient MCP
processes do not touch the protected Things group container.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from things3_mcp.providers.direct import DirectThingsProvider

SNAPSHOT_ACTIONS = ("inbox", "today", "upcoming", "anytime", "someday", "todos", "projects", "areas", "tags")


def run_action(action: str, params: dict[str, Any] | None = None) -> Any:
    """Run a read action against the direct Things provider."""
    params = params or {}
    provider = DirectThingsProvider()
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
