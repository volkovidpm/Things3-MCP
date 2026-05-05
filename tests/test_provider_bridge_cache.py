from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from things3_mcp.providers import AutoThingsProvider, ProviderError, get_provider
from things3_mcp.providers.bridge import BridgeThingsProvider
from things3_mcp.providers.cache import CacheStore, CacheThingsProvider
from things3_mcp_bridge import db_reader
from things3_mcp_bridge.server import run_worker


class FailingProvider:
    def inbox(self, include_items: bool = True):  # noqa: ARG002
        raise ProviderError("bridge_unavailable", "not running")


class FakeProvider:
    def inbox(self, include_items: bool = True):  # noqa: ARG002
        return [{"title": "Cached", "uuid": "1", "type": "to-do"}]


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def request(self, *_args, **_kwargs):
        return FakeResponse(self.payload)


def snapshot(path: Path) -> None:
    CacheStore(path).write_snapshot(
        {
            "version": 1,
            "source": "test",
            "generated_at": "2026-05-03T12:00:00+00:00",
            "data": {
                "inbox": [{"title": "Inbox item", "uuid": "inbox-1", "type": "to-do", "notes": "alpha"}],
                "today": [{"title": "Today item", "uuid": "today-1", "type": "to-do"}],
                "upcoming": [],
                "anytime": [],
                "someday": [],
                "todos": [{"title": "Searchable", "uuid": "todo-1", "type": "to-do", "notes": "needle"}],
                "projects": [],
                "areas": [],
                "tags": [],
            },
        }
    )


def test_provider_selection_env(monkeypatch):
    monkeypatch.setenv("THINGS3_MCP_PROVIDER", "cache")
    assert isinstance(get_provider(), CacheThingsProvider)

    monkeypatch.setenv("THINGS3_MCP_PROVIDER", "not-real")
    with pytest.raises(ProviderError) as exc:
        get_provider()
    assert exc.value.error_code == "provider_invalid"


def test_auto_provider_falls_back_to_cache_like_provider():
    provider = AutoThingsProvider(providers=[FailingProvider(), FakeProvider()])
    assert provider.inbox()[0]["title"] == "Cached"


def test_cache_store_atomic_write_and_search(tmp_path):
    cache_file = tmp_path / "latest.json"
    snapshot(cache_file)
    store = CacheStore(cache_file)
    assert store.status()["available"] is True

    provider = CacheThingsProvider(store)
    assert provider.inbox()[0]["uuid"] == "inbox-1"
    assert provider.search("needle")[0]["uuid"] == "todo-1"
    assert provider.get("today-1")["title"] == "Today item"


def test_bridge_client_parses_error_envelope(monkeypatch, tmp_path):
    token = tmp_path / "bridge.token"
    token.write_text("secret")
    provider = BridgeThingsProvider(token_file=token, socket_path=tmp_path / "bridge.sock")
    payload = {
        "ok": False,
        "error_code": "bridge_not_authorized",
        "message": "grant access",
        "authorization_hint": "Grant Full Disk Access",
        "cache_status": {"available": False},
    }
    monkeypatch.setattr(provider, "_client", lambda: FakeClient(payload))

    with pytest.raises(ProviderError) as exc:
        provider.inbox()
    assert exc.value.error_code == "bridge_not_authorized"
    assert "Full Disk Access" in exc.value.authorization_hint


def test_worker_timeout_returns_timeout_envelope(monkeypatch):
    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["worker"], timeout=0.01)

    monkeypatch.setattr(subprocess, "run", fake_run)
    envelope = run_worker("inbox", timeout=0.01)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "things_db_timeout"


def test_sqlite_probe_stays_inside_worker_process(monkeypatch, tmp_path):
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query):
            assert query == "select 1"
            return self

        def fetchone(self):
            return (1,)

    def fail_subprocess(*_args, **_kwargs):
        pytest.fail("SQLite validation must not spawn /usr/bin/sqlite3")

    monkeypatch.setattr(db_reader.subprocess, "run", fail_subprocess)
    monkeypatch.setattr(db_reader.sqlite3, "connect", lambda *_args, **_kwargs: FakeConnection())

    assert db_reader._can_open_sqlite(tmp_path / "main.sqlite") is True


def test_resolve_db_path_globs_group_container_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("THINGSDB", raising=False)
    monkeypatch.delenv("THINGS3_MCP_DATA_FOLDER", raising=False)

    fake_db = tmp_path / "ThingsData-FAKE" / "Things Database.thingsdatabase" / "main.sqlite"
    fake_db.parent.mkdir(parents=True)
    fake_db.touch()

    monkeypatch.setattr(db_reader, "_data_folder_from_thingscli_defaults", lambda: None)
    monkeypatch.setattr(db_reader.glob, "glob", lambda _pattern: [str(fake_db)])
    monkeypatch.setattr(db_reader, "_can_open_sqlite", lambda _path: True)

    assert db_reader.resolve_things_db_path() == str(fake_db)


def test_resolve_db_path_raises_when_glob_finds_nothing(monkeypatch):
    monkeypatch.delenv("THINGSDB", raising=False)
    monkeypatch.delenv("THINGS3_MCP_DATA_FOLDER", raising=False)

    monkeypatch.setattr(db_reader, "_data_folder_from_thingscli_defaults", lambda: None)
    monkeypatch.setattr(db_reader.glob, "glob", lambda _pattern: [])

    with pytest.raises(FileNotFoundError, match="Full Disk Access"):
        db_reader.resolve_things_db_path()


def test_diagnose_access_enumerates_group_container(monkeypatch, tmp_path):
    monkeypatch.delenv("THINGSDB", raising=False)
    monkeypatch.delenv("THINGS3_MCP_DATA_FOLDER", raising=False)

    fake_db = tmp_path / "ThingsData-FAKE" / "Things Database.thingsdatabase" / "main.sqlite"
    fake_db.parent.mkdir(parents=True)
    fake_db.touch()

    monkeypatch.setattr(db_reader, "_data_folder_from_thingscli_defaults", lambda: None)
    monkeypatch.setattr(db_reader.glob, "glob", lambda _pattern: [str(fake_db)])
    monkeypatch.setattr(db_reader, "_can_open_sqlite", lambda _path: True)

    result = db_reader.diagnose_access()

    assert result["ok"] is True
    assert result["path"] == str(fake_db)
    assert result["patterns"]
    assert result["patterns"][0]["matches"] == [str(fake_db)]


def test_snapshot_falls_back_to_apple_events_when_sqlite_path_unresolved(monkeypatch):
    def fail_sqlite(*_args, **_kwargs):
        raise FileNotFoundError("no db")

    monkeypatch.setattr(db_reader, "run_sqlite_action", fail_sqlite)
    monkeypatch.setattr(db_reader, "run_jxa_action", lambda action, _params: {"inbox": [{"title": action}]})

    result = db_reader.run_action("snapshot")

    assert result["_source"] == "apple_events"
    assert result["inbox"][0]["title"] == "snapshot"


def test_mcp_read_tools_use_provider_facade(monkeypatch):
    import things3_mcp.fast_server as fast_server

    class FacadeProvider:
        def inbox(self, include_items: bool = True):  # noqa: ARG002
            return [{"title": "Inbox via facade", "uuid": "i", "type": "to-do"}]

        def today(self, include_items: bool = True):  # noqa: ARG002
            return []

        def search(self, query: str, include_items: bool = True):  # noqa: ARG002
            return [{"title": f"Search {query}", "uuid": "s", "type": "to-do"}]

        def get(self, uuid: str):  # noqa: ARG002
            return None

    monkeypatch.setattr(fast_server, "get_provider", lambda: FacadeProvider())

    assert "Inbox via facade" in fast_server.get_inbox()
    assert fast_server.get_today() == "No items due today"
    assert "Search alpha" in fast_server.search_todos("alpha")


# --- Write path tests -----------------------------------------------------


def test_bridge_provider_add_task_posts_correct_payload(monkeypatch, tmp_path):
    """The HTTP client should POST to /things/todo with the params as JSON body."""
    token = tmp_path / "bridge.token"
    token.write_text("secret")
    provider = BridgeThingsProvider(token_file=token, socket_path=tmp_path / "bridge.sock")

    captured: dict[str, Any] = {}

    class CapturingClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, path, json=None):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = json
            return FakeResponse({"ok": True, "data": {"id": "new-uuid"}})

    monkeypatch.setattr(provider, "_client", lambda: CapturingClient())

    result = provider.add_task({"title": "Hello", "tags": ["a"]})
    assert captured["method"] == "POST"
    assert captured["path"] == "/things/todo"
    assert captured["json"] == {"title": "Hello", "tags": ["a"]}
    assert result == {"id": "new-uuid"}


def test_bridge_provider_update_task_uses_patch(monkeypatch, tmp_path):
    token = tmp_path / "bridge.token"
    token.write_text("secret")
    provider = BridgeThingsProvider(token_file=token, socket_path=tmp_path / "bridge.sock")

    captured: dict[str, Any] = {}

    class CapturingClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, path, json=None):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = json
            return FakeResponse({"ok": True, "data": {}})

    monkeypatch.setattr(provider, "_client", lambda: CapturingClient())

    provider.update_task("ABC123", {"title": "Updated"})
    assert captured["method"] == "PATCH"
    assert captured["path"] == "/things/todo/ABC123"
    assert captured["json"] == {"title": "Updated"}


def test_cache_provider_refuses_writes():
    provider = CacheThingsProvider()
    with pytest.raises(ProviderError) as exc:
        provider.add_task({"title": "x"})
    assert exc.value.error_code == "writes_unsupported"


def test_auto_provider_writes_skip_cache_and_use_bridge_only():
    """Writes must never silently fall back to the cache."""

    class FakeBridge(BridgeThingsProvider):
        def __init__(self):
            self.add_task_called_with: dict[str, Any] | None = None

        def add_task(self, params):
            self.add_task_called_with = params
            return {"ok": True, "id": "from-bridge"}

    bridge = FakeBridge()
    cache = CacheThingsProvider()
    auto = AutoThingsProvider(providers=[bridge, cache])

    result = auto.add_task({"title": "test"})
    assert result == {"ok": True, "id": "from-bridge"}
    assert bridge.add_task_called_with == {"title": "test"}


def test_auto_provider_writes_surface_bridge_error_when_no_fallback(monkeypatch):
    """Without ALLOW_DIRECT_FALLBACK, a bridge failure on writes propagates."""
    monkeypatch.delenv("THINGS3_MCP_ALLOW_DIRECT_FALLBACK", raising=False)

    class FailingBridge(BridgeThingsProvider):
        def __init__(self):
            pass

        def add_task(self, _params):
            raise ProviderError("bridge_unavailable", "socket gone")

    auto = AutoThingsProvider(providers=[FailingBridge(), CacheThingsProvider()])
    with pytest.raises(ProviderError) as exc:
        auto.add_task({"title": "x"})
    assert exc.value.error_code == "bridge_unavailable"


def test_worker_run_action_dispatches_add_task_to_applescript(monkeypatch):
    """run_action('add_task', params) should call applescript_bridge.add_todo."""
    import things3_mcp.applescript_bridge as ab

    captured: dict[str, Any] = {}

    def fake_add_todo(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "new-uuid-1234"

    monkeypatch.setattr(ab, "add_todo", fake_add_todo)

    result = db_reader.run_action("add_task", {"title": "Hello", "notes": "world"})
    assert captured == {"title": "Hello", "notes": "world"}
    assert result == {"ok": True, "id": "new-uuid-1234"}


def test_worker_run_action_update_task_extracts_uuid(monkeypatch):
    import things3_mcp.applescript_bridge as ab

    captured: dict[str, Any] = {}

    def fake_update_todo(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "true"

    monkeypatch.setattr(ab, "update_todo", fake_update_todo)

    result = db_reader.run_action("update_task", {"uuid": "ABC123", "title": "new"})
    assert captured == {"id": "ABC123", "title": "new"}
    assert result == {"ok": True}


def test_worker_run_action_update_task_requires_uuid():
    with pytest.raises(ValueError, match="uuid"):
        db_reader.run_action("update_task", {"title": "no uuid"})


def test_worker_coerces_applescript_error_into_runtime_error(monkeypatch):
    import things3_mcp.applescript_bridge as ab

    monkeypatch.setattr(ab, "add_todo", lambda **_kwargs: "Error: list not found")

    with pytest.raises(RuntimeError, match="list not found"):
        db_reader.run_action("add_task", {"title": "x"})
