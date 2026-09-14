"""Describe Sleeper events in the logbook."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook.const import (
    LOGBOOK_ENTRY_ICON,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.components.logbook.models import LazyEventPartialState
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, EVENT_DRAFT_PICK, EVENT_PLAYER_SCORED


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[
        [str, str, Callable[[LazyEventPartialState], dict[str, Any]]], None
    ],
) -> None:
    """Describe logbook events."""

    @callback
    def async_describe_player_scored(event: LazyEventPartialState) -> dict[str, Any]:
        """Describe a notable points change, e.g. "scored 6.3 points"."""
        data = event.data
        delta: float = data["delta"]
        verb = "scored" if delta > 0 else "lost"
        side = "" if data["is_mine"] else " for the opponent"
        return {
            LOGBOOK_ENTRY_NAME: data["player"],
            LOGBOOK_ENTRY_MESSAGE: (
                f"{verb} {abs(delta):g} points{side} "
                f"({data['points']:g} total, {data['league']})"
            ),
            LOGBOOK_ENTRY_ICON: "mdi:football",
        }

    @callback
    def async_describe_draft_pick(event: LazyEventPartialState) -> dict[str, Any]:
        """Describe a draft pick, e.g. "picked Jahmyr Gibbs (1.07, Wombats League)"."""
        data = event.data
        team = data["picked_by"] or (
            f"Roster {data['roster_id']}"
            if data["roster_id"] is not None
            else "Someone"
        )
        keeper = " as a keeper" if data["is_keeper"] else ""
        return {
            LOGBOOK_ENTRY_NAME: team,
            LOGBOOK_ENTRY_MESSAGE: (
                f"picked {data['player']}{keeper} ({data['pick']}, {data['league']})"
            ),
            LOGBOOK_ENTRY_ICON: "mdi:clipboard-list-outline",
        }

    async_describe_event(DOMAIN, EVENT_PLAYER_SCORED, async_describe_player_scored)
    async_describe_event(DOMAIN, EVENT_DRAFT_PICK, async_describe_draft_pick)
