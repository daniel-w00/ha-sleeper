"""The Sleeper integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType

from .const import DOMAIN
from .coordinator import SleeperConfigEntry, SleeperCoordinator
from .players import async_get_players

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# Unique ID suffixes of account-level sensors that earlier versions created
# (NFL week, season and season type, removed in 0.2.0).
REMOVED_ENTITY_KEYS = ("week", "season", "season_type")


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

    _async_remove_stale_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


@callback
def _async_remove_stale_entities(
    hass: HomeAssistant, entry: SleeperConfigEntry
) -> None:
    """Remove the registry entries of entities this version no longer creates."""
    entity_registry = er.async_get(hass)
    for key in REMOVED_ENTITY_KEYS:
        entity_id = entity_registry.async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{entry.unique_id}_{key}"
        )
        if entity_id is not None:
            _LOGGER.debug("Removing entity %s of an earlier version", entity_id)
            entity_registry.async_remove(entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: SleeperConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
