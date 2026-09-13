"""Binary sensor platform for the Sleeper integration."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SleeperConfigEntry, SleeperCoordinator
from .entity import SleeperLeagueEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SleeperConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Sleeper binary sensors from a config entry."""
    coordinator = entry.runtime_data
    known_league_ids: set[str] = set()

    @callback
    def _async_add_league_binary_sensors() -> None:
        """Add binary sensors for leagues that appeared since the last check."""
        # Entities of a vanished league are removed with its device; forget
        # the league so it gets entities again should the account rejoin.
        known_league_ids.intersection_update(coordinator.data.leagues)
        new_league_ids = set(coordinator.data.leagues) - known_league_ids
        known_league_ids.update(new_league_ids)
        async_add_entities(
            SleeperMatchupLeadingBinarySensor(coordinator, league_id)
            for league_id in new_league_ids
        )

    _async_add_league_binary_sensors()
    entry.async_on_unload(
        coordinator.async_add_listener(_async_add_league_binary_sensors)
    )


class SleeperMatchupLeadingBinarySensor(SleeperLeagueEntity, BinarySensorEntity):
    """Whether the account is ahead in this week's matchup."""

    _attr_translation_key = "matchup_leading"

    def __init__(self, coordinator: SleeperCoordinator, league_id: str) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, league_id)
        self._attr_unique_id = (
            f"{coordinator.config_entry.unique_id}_{league_id}_matchup_leading"
        )

    @property
    @override
    def is_on(self) -> bool | None:
        """Return whether the account's points exceed the opponent's."""
        data = self.league_data
        if data.my_matchup is None or data.opponent_matchup is None:
            return None
        return data.my_matchup.points > data.opponent_matchup.points

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the point margin over the opponent."""
        data = self.league_data
        if data.my_matchup is None or data.opponent_matchup is None:
            return None
        return {
            "margin": round(data.my_matchup.points - data.opponent_matchup.points, 2)
        }
