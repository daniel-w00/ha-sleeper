"""Base entities for the Sleeper integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    SleeperCoordinator,
    SleeperLeagueData,
    league_device_identifier,
)


class SleeperEntity(CoordinatorEntity[SleeperCoordinator]):
    """Base class for the entities of one config entry (account)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SleeperCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        if TYPE_CHECKING:
            assert coordinator.config_entry.unique_id is not None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.unique_id)},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Sleeper",
            model="Account",
            name=coordinator.config_entry.title,
            configuration_url="https://sleeper.com/",
        )


class SleeperLeagueEntity(SleeperEntity):
    """Base class for entities that belong to one league of one account.

    The entity becomes unavailable when the league is no longer in the
    coordinator data, e.g. after the account left it.
    """

    def __init__(self, coordinator: SleeperCoordinator, league_id: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        if TYPE_CHECKING:
            assert coordinator.config_entry.unique_id is not None
        self.league_id = league_id
        league = coordinator.data.leagues[league_id].league
        self._attr_device_info = DeviceInfo(
            identifiers={league_device_identifier(coordinator.user_id, league_id)},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Sleeper",
            name=league.name,
            model=f"{league.num_teams}-team {league.sport.upper()} league",
            configuration_url=f"https://sleeper.com/leagues/{league_id}",
            # The account device is registered in async_setup_entry before
            # the platforms load, so it can always be resolved here.
            via_device_id=dr.async_get_device_id_by_identifier(
                coordinator.hass,
                (DOMAIN, coordinator.config_entry.unique_id),
                config_entry_id=coordinator.config_entry.entry_id,
            ),
        )

    @property
    @override
    def available(self) -> bool:
        """Return whether the league is still part of the coordinator data."""
        return super().available and self.league_id in self.coordinator.data.leagues

    @property
    def league_data(self) -> SleeperLeagueData:
        """Return the current data of this entity's league."""
        return self.coordinator.data.leagues[self.league_id]
