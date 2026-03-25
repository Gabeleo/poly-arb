from __future__ import annotations

from typing import Protocol

from polyarb.models import Event, Market


class DataProvider(Protocol):
    def get_active_markets(self) -> list[Market]: ...
    def get_events(self) -> list[Event]: ...


def group_events(
    markets: list[Market],
    titles: dict[str, str] | None = None,
) -> list[Event]:
    """Group neg_risk markets into Events by event_slug.

    If *titles* is provided, use it for event titles; otherwise fall back
    to ``"Event: {slug}"``.
    """
    titles = titles or {}
    events_map: dict[str, list[Market]] = {}
    for m in markets:
        if m.neg_risk:
            events_map.setdefault(m.event_slug, []).append(m)
    return [
        Event(
            slug=slug,
            title=titles.get(slug, f"Event: {slug}"),
            markets=tuple(mlist),
        )
        for slug, mlist in events_map.items()
    ]
