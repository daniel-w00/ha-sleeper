"""Async client for the Sleeper API.

This module intentionally has no Home Assistant imports so it can be extracted
into a standalone PyPI package later. Keep it that way.

API reference: https://docs.sleeper.com/
The API is read-only, requires no authentication and asks for < 1000 calls/min.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, Self

from aiohttp import ClientError, ClientResponseError, ClientSession

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.sleeper.app/v1"
DEFAULT_TIMEOUT = 10


class SleeperError(Exception):
    """Base error for the Sleeper client."""


class SleeperConnectionError(SleeperError):
    """Raised when the API cannot be reached or answers with an error."""


class SleeperNotFoundError(SleeperError):
    """Raised when the requested resource does not exist."""


@dataclass(frozen=True, slots=True)
class SleeperUser:
    """A Sleeper user account."""

    user_id: str
    username: str
    display_name: str
    avatar: str | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a user from an API response object."""
        return cls(
            user_id=str(data["user_id"]),
            username=data["username"],
            display_name=data.get("display_name") or data["username"],
            avatar=data.get("avatar"),
        )


@dataclass(frozen=True, slots=True)
class SleeperSportState:
    """State of a sport's season as reported by Sleeper."""

    week: int
    season: str
    season_type: str
    display_week: int
    league_season: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a sport state from an API response object."""
        return cls(
            week=int(data["week"]),
            season=str(data["season"]),
            season_type=data["season_type"],
            display_week=int(data.get("display_week", data["week"])),
            league_season=str(data.get("league_season", data["season"])),
        )


class SleeperClient:
    """Minimal async client for the Sleeper API.

    The aiohttp session is injected so that Home Assistant can share its
    session; the client never creates or closes sessions itself.
    """

    def __init__(
        self,
        session: ClientSession,
        *,
        sport: str = "nfl",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the client. Performs no I/O."""
        self._session = session
        self._sport = sport
        self._timeout = timeout

    @property
    def sport(self) -> str:
        """Return the sport this client is scoped to."""
        return self._sport

    async def _get(self, path: str) -> Any:
        """Perform a GET request and return the decoded JSON body."""
        url = f"{BASE_URL}/{path.lstrip('/')}"
        _LOGGER.debug("GET %s", url)
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._session.get(url)
                response.raise_for_status()
                return await response.json()
        except ClientResponseError as err:
            if err.status == 404:
                raise SleeperNotFoundError(path) from err
            raise SleeperConnectionError(f"HTTP {err.status} for {url}") from err
        except (ClientError, TimeoutError) as err:
            raise SleeperConnectionError(f"Error connecting to {url}: {err}") from err

    async def get_user(self, username_or_id: str) -> SleeperUser:
        """Fetch a user by username or user ID.

        Sleeper answers unknown users with HTTP 200 and a ``null`` body.
        """
        data = await self._get(f"user/{username_or_id}")
        if not data:
            raise SleeperNotFoundError(f"User {username_or_id!r} not found")
        return SleeperUser.from_json(data)

    async def get_sport_state(self) -> SleeperSportState:
        """Fetch the current season/week state for the client's sport."""
        data = await self._get(f"state/{self._sport}")
        if not data:
            raise SleeperNotFoundError(f"No state for sport {self._sport!r}")
        return SleeperSportState.from_json(data)
