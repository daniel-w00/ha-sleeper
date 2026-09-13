"""Data update coordinator for the Sleeper integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SleeperClient, SleeperError, SleeperSportState, SleeperUser
from .const import DEFAULT_SPORT, DEFAULT_UPDATE_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type SleeperConfigEntry = ConfigEntry[SleeperCoordinator]


@dataclass(frozen=True, slots=True)
class SleeperData:
    """All data the entities of one config entry consume."""

    user: SleeperUser
    state: SleeperSportState


class SleeperCoordinator(DataUpdateCoordinator[SleeperData]):
    """Fetch data from Sleeper for one user account."""

    config_entry: SleeperConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: SleeperConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {config_entry.title}",
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )
        self.client = SleeperClient(async_get_clientsession(hass), sport=DEFAULT_SPORT)

    @override
    async def _async_update_data(self) -> SleeperData:
        """Fetch the latest data from Sleeper."""
        username = self.config_entry.data[CONF_USERNAME]
        try:
            user = await self.client.get_user(username)
            state = await self.client.get_sport_state()
        except SleeperError as err:
            raise UpdateFailed(f"Error communicating with Sleeper: {err}") from err
        return SleeperData(user=user, state=state)
