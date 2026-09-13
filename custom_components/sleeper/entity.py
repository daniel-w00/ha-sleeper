"""Base entity for the Sleeper integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SleeperCoordinator


class SleeperEntity(CoordinatorEntity[SleeperCoordinator]):
    """Base class for all Sleeper entities of one account."""

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
            name=coordinator.config_entry.title,
            configuration_url="https://sleeper.com/",
        )
