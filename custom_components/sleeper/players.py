"""Shared cache of Sleeper's player list.

Sleeper's player list is over 10 MB and may be downloaded at most once per
day. One cache serves every config entry; a slim copy (name, position, team,
injury) is kept in memory and in Home Assistant's storage so a restart does
not trigger a download.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .api import SleeperClient, SleeperError, SleeperPlayer
from .const import DEFAULT_SPORT, DOMAIN, PLAYERS_MAX_AGE, PLAYERS_RETRY_INTERVAL

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
PLAYERS_KEY: HassKey[SleeperPlayers] = HassKey(f"{DOMAIN}_players")


class SleeperPlayers:
    """Player lookup shared by all config entries of one sport."""

    def __init__(self, hass: HomeAssistant, sport: str) -> None:
        """Initialize the cache. Performs no I/O."""
        self.sport = sport
        self._client = SleeperClient(async_get_clientsession(hass), sport=sport)
        # The stored copy is a few MB; serialize it off the event loop.
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.players_{sport}",
            serialize_in_event_loop=False,
        )
        self._players: dict[str, SleeperPlayer] = {}
        self._fetched: datetime | None = None
        self._next_attempt: datetime | None = None
        self._loaded = False
        self._lock = asyncio.Lock()

    def get(self, player_id: str) -> SleeperPlayer | None:
        """Return a player by ID, ``None`` if unknown."""
        return self._players.get(player_id)

    def name(self, player_id: str) -> str:
        """Return a player's name, falling back to the ID."""
        player = self._players.get(player_id)
        return player.name if player is not None else player_id

    @property
    def stale(self) -> bool:
        """Return whether the list is missing or older than a day."""
        return (
            self._fetched is None or dt_util.utcnow() - self._fetched >= PLAYERS_MAX_AGE
        )

    async def async_load(self) -> None:
        """Load the stored copy, once."""
        async with self._lock:
            if self._loaded:
                return
            self._loaded = True
            data = await self._store.async_load()
            if not data:
                return
            self._players = {
                player_id: SleeperPlayer.from_json(item)
                for player_id, item in data["players"].items()
            }
            self._fetched = dt_util.parse_datetime(data["fetched"])
            _LOGGER.debug(
                "Loaded %d stored players (fetched %s)",
                len(self._players),
                self._fetched,
            )

    async def async_refresh_if_stale(self) -> None:
        """Download the player list if the copy is older than a day.

        A failed download is retried after ``PLAYERS_RETRY_INTERVAL`` so that
        a Sleeper outage does not cause a 10 MB request on every poll.
        """
        async with self._lock:
            now = dt_util.utcnow()
            if not self.stale or (
                self._next_attempt is not None and now < self._next_attempt
            ):
                return
            try:
                players = await self._client.get_players()
            except SleeperError:
                self._next_attempt = now + PLAYERS_RETRY_INTERVAL
                raise
            self._players = players
            self._fetched = now
            self._next_attempt = None
            _LOGGER.debug("Downloaded %d players", len(players))
            await self._store.async_save(
                {
                    "fetched": now.isoformat(),
                    "players": {
                        player_id: player.as_dict()
                        for player_id, player in players.items()
                    },
                }
            )


async def async_get_players(hass: HomeAssistant) -> SleeperPlayers:
    """Return the shared player cache, creating and loading it on first use."""
    if PLAYERS_KEY not in hass.data:
        hass.data[PLAYERS_KEY] = SleeperPlayers(hass, DEFAULT_SPORT)
    players = hass.data[PLAYERS_KEY]
    await players.async_load()
    return players
