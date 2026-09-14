"""The Sleeper integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType

from .const import DOMAIN
from .coordinator import SleeperConfigEntry, SleeperCoordinator
from .players import async_get_players

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SleeperConfigEntry) -> bool:
    """Set up Sleeper from a config entry."""
    players = await async_get_players(hass)
    coordinator = SleeperCoordinator(hass, entry, players)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Register the account device up front so the league devices can link to
    # it through ``via_device_id`` when the platforms create their entities.
    if TYPE_CHECKING:
        assert entry.unique_id is not None
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id)},
        entry_type=DeviceEntryType.SERVICE,
        manufacturer="Sleeper",
        model="Account",
        name=entry.title,
        configuration_url="https://sleeper.com/",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SleeperConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
