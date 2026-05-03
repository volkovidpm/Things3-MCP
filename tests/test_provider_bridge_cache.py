from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from things3_mcp.providers import AutoThingsProvider, ProviderError, get_provider
from things3_mcp.providers.bridge import BridgeThingsProvider
from things3_mcp.providers.cache import CacheStore, CacheThingsProvider
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
