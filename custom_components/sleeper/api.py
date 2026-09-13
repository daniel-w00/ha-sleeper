"""Async client for the Sleeper API.

This module intentionally has no Home Assistant imports so it can be extracted
into a standalone PyPI package later. Keep it that way.

API reference: https://docs.sleeper.com/
The API is read-only, requires no authentication and asks for < 1000 calls/min.
Every response is served through a CDN that caches it for 60 seconds, so
polling any endpoint faster than that never yields newer data.

Observed behaviour that the documentation does not mention:

- Unknown users and leagues are answered with HTTP 200 and a ``null`` body.
- Team defenses use the team abbreviation (e.g. ``"PIT"``) as player ID.
- A roster on a bye week has ``matchup_id: null`` in the matchups list.
- Roster records and season points only update once a week is finalised;
  the live points of the running week are in the matchups.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import json
import logging
from types import MappingProxyType
from typing import Any, Self

from aiohttp import ClientError, ClientResponseError, ClientSession

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.sleeper.app/v1"
DEFAULT_TIMEOUT = 10
# The player list is >10 MB; give slow connections a chance.
PLAYERS_TIMEOUT = 60

# ``waiver_type`` values in the league settings.
WAIVER_TYPE_ROLLING = 0
WAIVER_TYPE_REVERSE_STANDINGS = 1
WAIVER_TYPE_FAAB = 2


class SleeperError(Exception):
    """Base error for the Sleeper client."""


class SleeperConnectionError(SleeperError):
    """Raised when the API cannot be reached or answers with an error."""


class SleeperNotFoundError(SleeperError):
    """Raised when the requested resource does not exist."""


def _str_tuple(value: list[Any] | None) -> tuple[str, ...]:
    """Convert an optional list of IDs to a tuple of strings."""
    return tuple(str(item) for item in value or ())


def _float(value: Any) -> float:
    """Convert an optional numeric value to a float, treating ``None`` as 0."""
    return float(value or 0)


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
    leg: int
    season_start_date: str | None
    season_has_scores: bool

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a sport state from an API response object."""
        return cls(
            week=int(data["week"]),
            season=str(data["season"]),
            season_type=data["season_type"],
            display_week=int(data.get("display_week", data["week"])),
            league_season=str(data.get("league_season", data["season"])),
            leg=int(data.get("leg", data["week"])),
            season_start_date=data.get("season_start_date"),
            season_has_scores=bool(data.get("season_has_scores", False)),
        )


@dataclass(frozen=True, slots=True)
class SleeperLeague:
    """A fantasy league for one season."""

    league_id: str
    name: str
    status: str
    season: str
    sport: str
    total_rosters: int
    num_teams: int
    playoff_week_start: int | None
    playoff_teams: int | None
    waiver_type: int
    waiver_budget: int
    scoring_type: str
    roster_positions: tuple[str, ...]
    previous_league_id: str | None
    draft_id: str | None
    avatar: str | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a league from an API response object."""
        settings: dict[str, Any] = data.get("settings") or {}
        scoring: dict[str, Any] = data.get("scoring_settings") or {}
        return cls(
            league_id=str(data["league_id"]),
            name=data["name"],
            status=data["status"],
            season=str(data["season"]),
            sport=data["sport"],
            total_rosters=int(data["total_rosters"]),
            num_teams=int(settings.get("num_teams", data["total_rosters"])),
            playoff_week_start=_optional_int(settings.get("playoff_week_start")),
            playoff_teams=_optional_int(settings.get("playoff_teams")),
            waiver_type=int(settings.get("waiver_type", WAIVER_TYPE_ROLLING)),
            waiver_budget=int(settings.get("waiver_budget", 0)),
            scoring_type=_scoring_type(scoring.get("rec")),
            roster_positions=_str_tuple(data.get("roster_positions")),
            previous_league_id=_optional_str(data.get("previous_league_id")),
            draft_id=_optional_str(data.get("draft_id")),
            avatar=data.get("avatar"),
        )


@dataclass(frozen=True, slots=True)
class SleeperRoster:
    """One team (roster) in a league."""

    roster_id: int
    league_id: str
    owner_id: str | None
    players: tuple[str, ...]
    starters: tuple[str, ...]
    reserve: tuple[str, ...]
    taxi: tuple[str, ...]
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float
    waiver_position: int | None
    waiver_budget_used: int
    total_moves: int

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a roster from an API response object."""
        settings: dict[str, Any] = data.get("settings") or {}
        return cls(
            roster_id=int(data["roster_id"]),
            league_id=str(data["league_id"]),
            owner_id=_optional_str(data.get("owner_id")),
            players=_str_tuple(data.get("players")),
            starters=_str_tuple(data.get("starters")),
            reserve=_str_tuple(data.get("reserve")),
            taxi=_str_tuple(data.get("taxi")),
            wins=int(settings.get("wins", 0)),
            losses=int(settings.get("losses", 0)),
            ties=int(settings.get("ties", 0)),
            points_for=_points(settings, "fpts"),
            points_against=_points(settings, "fpts_against"),
            waiver_position=_optional_int(settings.get("waiver_position")),
            waiver_budget_used=int(settings.get("waiver_budget_used", 0)),
            total_moves=int(settings.get("total_moves", 0)),
        )

    @property
    def record(self) -> str:
        """Return the record as ``W-L`` or ``W-L-T`` when there are ties."""
        record = f"{self.wins}-{self.losses}"
        if self.ties:
            record += f"-{self.ties}"
        return record


@dataclass(frozen=True, slots=True)
class SleeperLeagueUser:
    """A user as a member of a specific league."""

    user_id: str
    display_name: str
    avatar: str | None
    team_name: str | None
    is_owner: bool

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a league member from an API response object."""
        metadata: dict[str, Any] = data.get("metadata") or {}
        team_name = metadata.get("team_name")
        return cls(
            user_id=str(data["user_id"]),
            display_name=data["display_name"],
            avatar=data.get("avatar"),
            team_name=team_name.strip() if team_name else None,
            is_owner=bool(data.get("is_owner")),
        )

    @property
    def name(self) -> str:
        """Return the team name if set, otherwise the display name."""
        return self.team_name or self.display_name


@dataclass(frozen=True, slots=True)
class SleeperMatchup:
    """One side of a weekly matchup.

    Two entries with the same ``matchup_id`` play each other. ``matchup_id`` is
    ``None`` on a bye week.
    """

    roster_id: int
    matchup_id: int | None
    points: float
    custom_points: float | None
    starters: tuple[str, ...]
    players: tuple[str, ...]
    starters_points: tuple[float, ...]
    players_points: Mapping[str, float]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a matchup from an API response object."""
        custom_points = data.get("custom_points")
        players_points: dict[str, Any] = data.get("players_points") or {}
        return cls(
            roster_id=int(data["roster_id"]),
            matchup_id=_optional_int(data.get("matchup_id")),
            points=_float(data.get("points")),
            custom_points=None if custom_points is None else float(custom_points),
            starters=_str_tuple(data.get("starters")),
            players=_str_tuple(data.get("players")),
            starters_points=tuple(
                _float(value) for value in data.get("starters_points") or ()
            ),
            players_points=MappingProxyType(
                {str(key): _float(value) for key, value in players_points.items()}
            ),
        )


@dataclass(frozen=True, slots=True)
class SleeperPlayer:
    """A player (or team defense) from Sleeper's player list.

    Only the fields the integration needs are kept; the full player objects
    carry ~50 fields each and the list is several megabytes.
    """

    player_id: str
    first_name: str
    last_name: str
    position: str | None
    team: str | None
    status: str | None
    injury_status: str | None
    fantasy_positions: tuple[str, ...]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a player from an API response object (or a stored copy)."""
        return cls(
            player_id=str(data["player_id"]),
            first_name=data.get("first_name") or "",
            last_name=data.get("last_name") or "",
            position=data.get("position") or None,
            team=data.get("team") or None,
            status=data.get("status") or None,
            injury_status=data.get("injury_status") or None,
            fantasy_positions=_str_tuple(data.get("fantasy_positions")),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the player in the shape ``from_json`` accepts."""
        return {
            "player_id": self.player_id,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "position": self.position,
            "team": self.team,
            "status": self.status,
            "injury_status": self.injury_status,
            "fantasy_positions": list(self.fantasy_positions),
        }

    @property
    def name(self) -> str:
        """Return the full name; team defenses read like "Pittsburgh Steelers"."""
        return f"{self.first_name} {self.last_name}".strip() or self.player_id


def _parse_players(text: str) -> dict[str, SleeperPlayer]:
    """Decode the player list. CPU heavy, meant to run in an executor."""
    data: dict[str, dict[str, Any]] = json.loads(text) or {}
    return {
        player_id: SleeperPlayer.from_json(item) for player_id, item in data.items()
    }


def _optional_str(value: Any) -> str | None:
    """Return the value as a string, or ``None`` if it is missing."""
    return None if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    """Return the value as an int, or ``None`` if it is missing."""
    return None if value is None else int(value)


def _points(settings: dict[str, Any], key: str) -> float:
    """Combine Sleeper's integer/decimal point pair into a float.

    Sleeper stores points as ``fpts`` (whole points) and ``fpts_decimal``
    (hundredths); the same pattern is used for ``fpts_against``.
    """
    return int(settings.get(key, 0)) + int(settings.get(f"{key}_decimal", 0)) / 100


def _scoring_type(points_per_reception: Any) -> str:
    """Classify the scoring format by the points awarded per reception."""
    ppr = _float(points_per_reception)
    if ppr == 1:
        return "ppr"
    if ppr == 0.5:
        return "half_ppr"
    if ppr == 0:
        return "standard"
    return "custom"


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
        return json.loads(await self._get_text(path))

    async def _get_text(
        self, path: str, *, request_timeout: float | None = None
    ) -> str:
        """Perform a GET request and return the raw body."""
        url = f"{BASE_URL}/{path.lstrip('/')}"
        _LOGGER.debug("GET %s", url)
        try:
            async with asyncio.timeout(request_timeout or self._timeout):
                response = await self._session.get(url)
                response.raise_for_status()
                return await response.text()
        except ClientResponseError as err:
            if err.status == 404:
                raise SleeperNotFoundError(path) from err
            raise SleeperConnectionError(f"HTTP {err.status} for {url}") from err
        except (ClientError, TimeoutError) as err:
            raise SleeperConnectionError(f"Error connecting to {url}: {err}") from err

    async def _get_object(self, path: str, description: str) -> dict[str, Any]:
        """GET a single object, mapping Sleeper's ``null`` answer to not found."""
        data = await self._get(path)
        if not data:
            raise SleeperNotFoundError(f"{description} not found")
        return data

    async def _get_list(self, path: str) -> list[dict[str, Any]]:
        """GET a list, treating a ``null`` answer as an empty list."""
        return await self._get(path) or []

    async def get_user(self, username_or_id: str) -> SleeperUser:
        """Fetch a user by username or user ID."""
        data = await self._get_object(
            f"user/{username_or_id}", f"User {username_or_id!r}"
        )
        return SleeperUser.from_json(data)

    async def get_sport_state(self) -> SleeperSportState:
        """Fetch the current season/week state for the client's sport."""
        data = await self._get_object(
            f"state/{self._sport}", f"State for sport {self._sport!r}"
        )
        return SleeperSportState.from_json(data)

    async def get_user_leagues(
        self, user_id: str, season: str
    ) -> tuple[SleeperLeague, ...]:
        """Fetch all leagues of a user for one season."""
        data = await self._get_list(f"user/{user_id}/leagues/{self._sport}/{season}")
        return tuple(SleeperLeague.from_json(item) for item in data)

    async def get_league(self, league_id: str) -> SleeperLeague:
        """Fetch a single league."""
        data = await self._get_object(f"league/{league_id}", f"League {league_id!r}")
        return SleeperLeague.from_json(data)

    async def get_rosters(self, league_id: str) -> tuple[SleeperRoster, ...]:
        """Fetch all rosters of a league."""
        data = await self._get_list(f"league/{league_id}/rosters")
        return tuple(SleeperRoster.from_json(item) for item in data)

    async def get_league_users(self, league_id: str) -> tuple[SleeperLeagueUser, ...]:
        """Fetch all members of a league."""
        data = await self._get_list(f"league/{league_id}/users")
        return tuple(SleeperLeagueUser.from_json(item) for item in data)

    async def get_players(self) -> dict[str, SleeperPlayer]:
        """Fetch all active players of the sport, keyed by player ID.

        The response is over 10 MB. Sleeper asks for this call to be made at
        most once per day; callers must cache the result. Decoding happens in
        the default executor so the event loop is not blocked.
        """
        text = await self._get_text(
            f"players/{self._sport}?active=true", request_timeout=PLAYERS_TIMEOUT
        )
        return await asyncio.get_running_loop().run_in_executor(
            None, _parse_players, text
        )

    async def get_matchups(
        self, league_id: str, week: int
    ) -> tuple[SleeperMatchup, ...]:
        """Fetch the matchups of a league for one week.

        Returns an empty tuple for leagues that have not started (pre-draft).
        """
        data = await self._get_list(f"league/{league_id}/matchups/{week}")
        return tuple(SleeperMatchup.from_json(item) for item in data)
