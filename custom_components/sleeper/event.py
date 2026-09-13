"""Event platform for the Sleeper integration."""

from __future__ import annotations

from typing import override

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_POINTS_CHANGED
from .coordinator import SleeperConfigEntry, SleeperCoordinator
from .entity import SleeperLeagueEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SleeperConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Sleeper event entities from a config entry."""
    coordinator = entry.runtime_data
    known_league_ids: set[str] = set()

    @callback
    def _async_add_league_events() -> None:
        """Add event entities for leagues that appeared since the last check."""
        known_league_ids.intersection_update(coordinator.data.leagues)
        new_league_ids = set(coordinator.data.leagues) - known_league_ids
        known_league_ids.update(new_league_ids)
        async_add_entities(
            SleeperPlayerScoringEvent(coordinator, league_id)
            for league_id in new_league_ids
        )

    _async_add_league_events()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_league_events))


class SleeperPlayerScoringEvent(SleeperLeagueEntity, EventEntity):
    """Fires whenever a player in the account's matchup changes points.

    One event per player and poll, covering both sides of the matchup.
    Events are fired in ascending order of the account's gain, so the state
    of the entity ends on the account's biggest play of the poll.
    """

    _attr_translation_key = "player_scoring"

    def __init__(self, coordinator: SleeperCoordinator, league_id: str) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator, league_id)
        self._attr_event_types = [EVENT_POINTS_CHANGED]
        self._attr_unique_id = (
            f"{coordinator.config_entry.unique_id}_{league_id}_player_scoring"
        )

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Fire one event per changed player, then write the state."""
        if self.available:
            data = self.league_data
            for change in sorted(
                data.points_changes, key=lambda item: (item.is_mine, item.delta)
            ):
                player = change.player
                self._trigger_event(
                    EVENT_POINTS_CHANGED,
                    {
                        "player_id": change.player_id,
                        "player": player.name if player else change.player_id,
                        "position": player.position if player else None,
                        "team": player.team if player else None,
                        "roster_id": change.roster_id,
                        "is_mine": change.is_mine,
                        "is_starter": change.is_starter,
                        "previous_points": change.previous,
                        "points": change.points,
                        "delta": change.delta,
                        "week": data.week,
                        "matchup_id": (
                            data.my_matchup.matchup_id if data.my_matchup else None
                        ),
                    },
                )
                self.async_write_ha_state()
        super()._handle_coordinator_update()
