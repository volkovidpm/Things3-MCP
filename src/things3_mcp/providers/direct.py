"""Direct things-py provider.

This preserves historical behaviour and may touch the macOS-protected Things group
container from the current process. It is intentionally opt-in for auto fallback.
"""

from __future__ import annotations

from typing import Any

import things


class DirectThingsProvider:
    """Thin adapter around the ``things-py`` module."""

    source = "direct"

    def inbox(self, include_items: bool = True) -> list[dict[str, Any]]:
        return things.inbox(include_items=include_items)

    def today(self, include_items: bool = True) -> list[dict[str, Any]]:
        """Return Today, preserving the server's historical None-safe fallback."""
        try:
            return things.today(include_items=include_items)
        except TypeError as exc:
            if "'<' not supported between instances of 'NoneType' and 'str'" not in str(exc):
                raise
            result = [
                *things.tasks(start_date=True, start="Anytime", index="todayIndex", status="incomplete", include_items=include_items),
                *things.tasks(start_date="past", start="Someday", index="todayIndex", status="incomplete", include_items=include_items),
                *things.tasks(start_date=False, deadline="past", deadline_suppressed=False, status="incomplete", include_items=include_items),
            ]
            result.sort(key=lambda task: (task.get("today_index") or 999999, task.get("start_date") or ""))
            return result

    def upcoming(self, include_items: bool = True) -> list[dict[str, Any]]:
        return things.upcoming(include_items=include_items)

    def anytime(self, include_items: bool = True) -> list[dict[str, Any]]:
        return things.anytime(include_items=include_items)

    def someday(self, include_items: bool = True) -> list[dict[str, Any]]:
        return things.someday(include_items=include_items)

    def tasks(self, **kwargs: Any) -> list[dict[str, Any]]:
        return things.tasks(**kwargs)

    def todos(self, **kwargs: Any) -> list[dict[str, Any]]:
        return things.todos(**kwargs)

    def search(self, query: str, include_items: bool = True) -> list[dict[str, Any]]:
        return things.search(query, include_items=include_items)

    def get(self, uuid: str) -> dict[str, Any] | None:
        return things.get(uuid)

    def projects(self, include_items: bool = False, **kwargs: Any) -> list[dict[str, Any]]:
        projects = things.projects(**kwargs)
        if include_items:
            for project in projects:
                project.setdefault("items", things.todos(project=project.get("uuid"), include_items=True))
        return projects

    def areas(self, include_items: bool = False, **kwargs: Any) -> list[dict[str, Any]]:
        areas = things.areas(**kwargs)
        if include_items:
            for area in areas:
                area.setdefault("projects", things.projects(area=area.get("uuid")))
                area.setdefault("items", things.todos(area=area.get("uuid"), include_items=True))
        return areas

    def tags(self, include_items: bool = False, **kwargs: Any) -> list[dict[str, Any]]:
        tags = things.tags(**kwargs)
        if include_items:
            for tag in tags:
                tag.setdefault("items", things.todos(tag=tag.get("title"), include_items=True))
        return tags
