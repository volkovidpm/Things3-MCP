# ruff: noqa: E501
"""Killable live Things read worker.

The bridge parent process invokes this module in a child process with a hard
timeout. It first attempts the historical SQLite-backed ``things-py`` reader,
then falls back to Things' Apple Events scripting model when macOS blocks the
protected app-group container.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SNAPSHOT_ACTIONS = ("inbox", "today", "upcoming", "anytime", "someday", "todos", "projects", "areas", "tags")

DB_PATTERNS = (
    "~/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/ThingsData-*/Things Database.thingsdatabase/main.sqlite",
    "~/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/Things Database.thingsdatabase/main.sqlite",
)

JXA_TIMEOUT_SECONDS = float(os.environ.get("THINGS3_MCP_JXA_TIMEOUT", "300"))

# Compact one-line-JXA for simple list reads (fast list enumeration)
_JXA_SIMPLE = r"""
function run(argv) {
  const app = Application('Things3');
  function v(fn, fallback) { try { const r = fn(); return r===undefined ? fallback : (r instanceof Date&&isNaN(r.getTime())?fallback:r); } catch(e) { return fallback; } }
  function s(todo) { return { uuid: v(()=>todo.id(),null), title: v(()=>todo.name(),''), type:'to-do', status: String(v(()=>todo.status(),'open'))==='completed'?'completed':'incomplete', notes: v(()=>todo.notes(),'')||'', project: null, project_title: null, area: null, area_title: null, tags: null, tag_titles: [], tag_names: '' }; }
  const action = argv[0];
  if (action==='inbox') { const l=app.lists().find(x=>String(v(()=>x.name(),'')).toLowerCase()==='inbox'); return JSON.stringify(l?l.toDos().map(s):[]); }
  if (action==='today') { const l=app.lists().find(x=>String(v(()=>x.name(),'')).toLowerCase()==='today'); return JSON.stringify(l?l.toDos().map(s):[]); }
  if (action==='upcoming') { const l=app.lists().find(x=>String(v(()=>x.name(),'')).toLowerCase()==='upcoming'); return JSON.stringify(l?l.toDos().map(s):[]); }
  if (action==='anytime') { const l=app.lists().find(x=>String(v(()=>x.name(),'')).toLowerCase()==='anytime'); return JSON.stringify(l?l.toDos().map(s):[]); }
  if (action==='someday') { const l=app.lists().find(x=>String(v(()=>x.name(),'')).toLowerCase()==='someday'); return JSON.stringify(l?l.toDos().map(s):[]); }
  if (action==='areas') { return JSON.stringify(app.areas().map(a=>({uuid:v(()=>a.id(),null),title:v(()=>a.name(),'')}))); }
  if (action==='tags') { return JSON.stringify(app.tags().map(t=>({uuid:v(()=>t.id(),null),title:v(()=>t.name(),''),name:v(()=>t.name(),'')}))); }
  if (action==='projects') { return JSON.stringify(app.projects().map(p=>({uuid:v(()=>p.id(),null),title:v(()=>p.name(),''),type:'project'}))); }
  throw new Error('Unknown action: '+action);
}
"""

# Parallelised snapshot JXA split into two phases to avoid osascript hangs
_JXA_LISTS = r"""
function run(argv) {
  const app = Application('Things3');
  function v(fn, fallback) { try { const r = fn(); return r===undefined ? fallback : (r instanceof Date&&isNaN(r.getTime())?fallback:r); } catch(e) { return fallback; } }
  function s(todo) {
    return {
      uuid: v(()=>todo.id(),null),
      title: v(()=>todo.name(),''),
      type: 'to-do',
      status: String(v(()=>todo.status(),'open'))==='completed'?'completed':'incomplete',
      notes: v(()=>todo.notes(),'')||'',
      project: null,
      project_title: null,
      area: null,
      area_title: null,
      tags: null,
      tag_titles: [],
      tag_names: '',
    };
  }
  function todosForList(name) {
    const lower = name.toLowerCase();
    const list = app.lists().find(x=>String(v(()=>x.name(),'')).toLowerCase()===lower);
    return list ? list.toDos().map(s) : [];
  }
  return JSON.stringify({ inbox: todosForList('Inbox'), today: todosForList('Today'), upcoming: todosForList('Upcoming'), anytime: todosForList('Anytime'), someday: todosForList('Someday') });
}
"""

_JXA_META = r"""
function run(argv) {
  const app = Application('Things3');
  function v(fn, fallback) { try { const r = fn(); return r===undefined ? fallback : (r instanceof Date&&isNaN(r.getTime())?fallback:r); } catch(e) { return fallback; } }
  function projectProps(p) { return { uuid: v(()=>p.id(),null), title: v(()=>p.name(),''), type: 'project' }; }
  function areaProps(a) { const tagNames=String(v(()=>a.tagNames(),'')||'').split(',').map(x=>x.trim()).filter(Boolean); return { uuid: v(()=>a.id(),null), title: v(()=>a.name(),''), type: 'area', tags: tagNames.length?1:null, tag_titles: tagNames, tag_names: tagNames.join(', ') }; }
  function tagProps(t) { return { uuid: v(()=>t.id(),null), title: v(()=>t.name(),''), name: v(()=>t.name(),''), keyboard_shortcut: v(()=>t.keyboardShortcut(),null) }; }
  return JSON.stringify({ projects: app.projects().map(projectProps), areas: app.areas().map(areaProps), tags: app.tags().map(tagProps) });
}
"""


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


def _run_jxa_script(script_content: str, action: str, params: dict[str, Any] | None = None) -> str:
    """Execute JXA from a temp file and return stdout."""
    params = params or {}
    with tempfile.NamedTemporaryFile(suffix=".jxa", mode="w", delete=False) as f:
        f.write(script_content)
        script_path = f.name
    try:
        completed = subprocess.run(  # noqa: S603 - fixed executable, no shell
            ["/usr/bin/osascript", "-l", "JavaScript", script_path, action, json.dumps(params)],
            capture_output=True,
            text=True,
            timeout=JXA_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0:
            msg = completed.stderr.strip() or completed.stdout.strip() or f"osascript rc={completed.returncode}"
            raise RuntimeError(msg)
        return completed.stdout.strip()
    finally:
        os.unlink(script_path)


def run_jxa_action(action: str, params: dict[str, Any] | None = None) -> Any:
    """Read Things through its Apple Events scripting interface."""
    params = params or {}
    if action == "snapshot":
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            lists_future = executor.submit(_run_jxa_script, _JXA_LISTS, "_snapshot_lists", {})
            meta_future = executor.submit(_run_jxa_script, _JXA_META, "_snapshot_meta", {})
            lists_raw = lists_future.result(timeout=JXA_TIMEOUT_SECONDS)
            meta_raw = meta_future.result(timeout=JXA_TIMEOUT_SECONDS)

        lists_data: dict[str, Any] = json.loads(lists_raw)
        meta_data: dict[str, Any] = json.loads(meta_raw)
        inbox = lists_data.get("inbox", [])
        today = lists_data.get("today", [])
        upcoming = lists_data.get("upcoming", [])
        anytime = lists_data.get("anytime", [])
        someday = lists_data.get("someday", [])
        todos: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in [*inbox, *today, *upcoming, *anytime, *someday]:
            uuid = item.get("uuid") if isinstance(item, dict) else None
            if uuid and uuid not in seen:
                seen.add(uuid)
                todos.append(item)
        return {
            "inbox": inbox,
            "today": today,
            "upcoming": upcoming,
            "anytime": anytime,
            "someday": someday,
            "todos": todos,
            "projects": meta_data.get("projects", []),
            "areas": meta_data.get("areas", []),
            "tags": meta_data.get("tags", []),
        }

    output = _run_jxa_script(_JXA_SIMPLE, action, params)
    return json.loads(output)


def run_sqlite_action(action: str, params: dict[str, Any] | None = None) -> Any:
    """Run a read action against the direct Things SQLite provider."""
    params = params or {}
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


def run_action(action: str, params: dict[str, Any] | None = None) -> Any:
    """Run a read action against Things, preferring SQLite and falling back to Apple Events."""
    params = params or {}
    if action == "diagnose":
        return diagnose_access()
    try:
        return run_sqlite_action(action, params)
    except Exception as sqlite_exc:  # noqa: BLE001 - fallback path preserves both failures in diagnostics
        try:
            data = run_jxa_action(action, params)
        except Exception as jxa_exc:  # noqa: BLE001
            raise RuntimeError(f"SQLite read failed ({sqlite_exc}); Apple Events read failed ({jxa_exc})") from jxa_exc
        if isinstance(data, dict):
            data.setdefault("_source", "apple_events")
        return data


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
        print(json.dumps({"ok": False, "error_code": "things_read_unavailable", "message": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
