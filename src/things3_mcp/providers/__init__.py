"""Provider facade for Things read access."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .base import ProviderError, ThingsProvider, WriteThingsProvider, WriteUnsupported
from .bridge import BridgeThingsProvider
from .cache import CacheThingsProvider
from .direct import DirectThingsProvider

PROVIDER_ENV = "THINGS3_MCP_PROVIDER"
ALLOW_DIRECT_FALLBACK_ENV = "THINGS3_MCP_ALLOW_DIRECT_FALLBACK"


def _direct_fallback_allowed() -> bool:
    return os.environ.get(ALLOW_DIRECT_FALLBACK_ENV, "0") in {"1", "true", "TRUE", "yes", "YES"}


class UnavailableThingsProvider:
    """Provider that fails fast with a useful diagnostic."""

    source = "unavailable"

    def __init__(self, error: ProviderError | None = None) -> None:
        self.error = error or ProviderError(
            "bridge_unavailable",
            "No Things provider is available. Start/authorize the local bridge or create a cache snapshot.",
            authorization_hint="Grant Full Disk Access to Things3 MCP Bridge.app, then run Things3-MCP-bridge --snapshot-once.",
        )

    def _raise(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise self.error

    inbox = today = upcoming = anytime = someday = tasks = todos = search = projects = areas = tags = _raise

    def get(self, uuid: str) -> dict[str, Any] | None:  # noqa: ARG002
        raise self.error


class AutoThingsProvider:
    """Provider that tries bridge, then cache, then optional direct fallback."""

    source = "auto"

    def __init__(self, providers: list[ThingsProvider] | None = None) -> None:
        self.providers = providers or _auto_provider_chain()

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        first_error: ProviderError | None = None
        last_error: ProviderError | None = None
        for provider in self.providers:
            try:
                return getattr(provider, method_name)(*args, **kwargs)
            except ProviderError as exc:
                if first_error is None:
                    first_error = exc
                last_error = exc
                continue
        if first_error:
            if last_error and last_error is not first_error and last_error.cache_status and not first_error.cache_status:
                first_error.cache_status = last_error.cache_status
            raise first_error
        raise ProviderError("bridge_unavailable", "No Things provider candidates are configured")

    def inbox(self, include_items: bool = True) -> list[dict[str, Any]]:
        return self._call("inbox", include_items=include_items)

    def today(self, include_items: bool = True) -> list[dict[str, Any]]:
        return self._call("today", include_items=include_items)

    def upcoming(self, include_items: bool = True) -> list[dict[str, Any]]:
        return self._call("upcoming", include_items=include_items)

    def anytime(self, include_items: bool = True) -> list[dict[str, Any]]:
        return self._call("anytime", include_items=include_items)

    def someday(self, include_items: bool = True) -> list[dict[str, Any]]:
        return self._call("someday", include_items=include_items)

    def tasks(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._call("tasks", **kwargs)

    def todos(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._call("todos", **kwargs)

    def search(self, query: str, include_items: bool = True) -> list[dict[str, Any]]:
        return self._call("search", query, include_items=include_items)

    def get(self, uuid: str) -> dict[str, Any] | None:
        return self._call("get", uuid)

    def projects(self, include_items: bool = False, **kwargs: Any) -> list[dict[str, Any]]:
        return self._call("projects", include_items=include_items, **kwargs)

    def areas(self, include_items: bool = False, **kwargs: Any) -> list[dict[str, Any]]:
        return self._call("areas", include_items=include_items, **kwargs)

    def tags(self, include_items: bool = False, **kwargs: Any) -> list[dict[str, Any]]:
        return self._call("tags", include_items=include_items, **kwargs)

    # --- Write API ---------------------------------------------------------
    # Writes never fall back to the cache (it can't satisfy mutations). They
    # try the bridge first, and only the direct AppleScript path as fallback
    # if THINGS3_MCP_ALLOW_DIRECT_FALLBACK=1.

    def _call_write(self, method_name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        first_error: ProviderError | None = None
        for provider in self._write_chain():
            try:
                return getattr(provider, method_name)(*args, **kwargs)
            except ProviderError as exc:
                if first_error is None:
                    first_error = exc
                continue
        if first_error:
            raise first_error
        raise ProviderError("bridge_unavailable", "No Things write provider candidates are configured")

    def _write_chain(self) -> list[ThingsProvider]:
        chain: list[ThingsProvider] = [p for p in self.providers if isinstance(p, BridgeThingsProvider)]
        if _direct_fallback_allowed():
            chain.extend(p for p in self.providers if isinstance(p, DirectThingsProvider))
        return chain

    def add_task(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._call_write("add_task", params)

    def update_task(self, uuid: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._call_write("update_task", uuid, params)

    def add_project(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._call_write("add_project", params)

    def update_project(self, uuid: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._call_write("update_project", uuid, params)


def _auto_provider_chain() -> list[ThingsProvider]:
    providers: list[ThingsProvider] = [BridgeThingsProvider(), CacheThingsProvider()]
    if _direct_fallback_allowed():
        providers.append(DirectThingsProvider())
    return providers


def get_provider() -> ThingsProvider:
    """Return the configured Things provider.

    ``auto`` is deliberately conservative: it does not hit the protected Things
    DB directly unless ``THINGS3_MCP_ALLOW_DIRECT_FALLBACK=1`` is set.
    """
    provider_name = os.environ.get(PROVIDER_ENV, "auto").casefold()
    providers: dict[str, Callable[[], ThingsProvider]] = {
        "auto": AutoThingsProvider,
        "bridge": BridgeThingsProvider,
        "cache": CacheThingsProvider,
        "direct": DirectThingsProvider,
    }
    try:
        return providers[provider_name]()
    except KeyError as exc:
        raise ProviderError("provider_invalid", f"Unknown THINGS3_MCP_PROVIDER={provider_name!r}") from exc


__all__ = [
    "ALLOW_DIRECT_FALLBACK_ENV",
    "PROVIDER_ENV",
    "AutoThingsProvider",
    "BridgeThingsProvider",
    "CacheThingsProvider",
    "DirectThingsProvider",
    "ProviderError",
    "ThingsProvider",
    "UnavailableThingsProvider",
    "WriteThingsProvider",
    "WriteUnsupported",
    "get_provider",
]
