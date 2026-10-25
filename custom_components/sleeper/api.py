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
- User and league ``avatar`` fields are image IDs on Sleeper's CDN. A team
  picture a manager uploaded for one league is a full URL in the league
  member's ``metadata.avatar``; most managers never set one.
- A draft does not say whose turn it is. The order of the picks follows from
  the draft type, the draft order and the traded picks; see
  :meth:`SleeperDraft.slot_of`.
- ``slot_to_roster_id`` and ``draft_order`` of a draft are ``null`` until the
  commissioner sets the order. The league's draft list omits
  ``slot_to_roster_id``; the single draft endpoint has it.
- Timestamps (``start_time``, ``last_picked``) are milliseconds since the
  epoch.
- Player headshots are 350x254 PNG files (despite the ``.jpg`` name and
  the ``image/jpeg`` content type) with a transparent background and the
  head in the middle. The CDN answers 403, not 404, for missing files.

Projections and the live state of the NFL games are not part of the
documented API. They come from the GraphQL API the Sleeper app uses
(``GRAPHQL_URL``), which answers these two queries without authentication;
most others, including the app's matchup projections, need a login.

- Matchup projections and win probabilities are computed by the app; the
  API only has each player's projected stat line (by Rotowire), which is
  scored with the league's ``scoring_settings`` like the real stats are.
- Projections keep their pre-game values while and after a game is played.
- Projections count every field goal of 50+ yards as ``fgm_50p`` and have
  no ``fgmiss``, while leagues usually score ``fgm_50_59``, ``fgm_60p``
  and ``fgmiss``; see :meth:`SleeperLeague.fantasy_points`.
- A game's clock is ``quarter_num`` (5 in overtime) and ``time_remaining``
  (``"M:SS"`` left in the quarter) in its ``metadata``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
from types import MappingProxyType
from typing import Any, Self

from aiohttp import ClientError, ClientResponseError, ClientSession

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.sleeper.app/v1"
GRAPHQL_URL = "https://sleeper.com/graphql"
AVATAR_URL = "https://sleepercdn.com/avatars"
PLAYER_PICTURE_URL = (
    "https://sleepercdn.com/content/{sport}/players/thumb/{player_id}.jpg"
)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TEAM_LOGO_URL = "https://sleepercdn.com/images/team_logos/{sport}/{team}.png"
# Position of a team defense; its player ID is the team abbreviation.
POSITION_DEFENSE = "DEF"
DEFAULT_TIMEOUT = 10
# The player list is >10 MB; give slow connections a chance.
PLAYERS_TIMEOUT = 60

# ``waiver_type`` values in the league settings.
WAIVER_TYPE_ROLLING = 0
WAIVER_TYPE_REVERSE_STANDINGS = 1
WAIVER_TYPE_FAAB = 2

# Draft statuses and types.
DRAFT_STATUS_PRE_DRAFT = "pre_draft"
DRAFT_STATUS_DRAFTING = "drafting"
DRAFT_STATUS_PAUSED = "paused"
DRAFT_STATUS_COMPLETE = "complete"
DRAFT_TYPE_SNAKE = "snake"
DRAFT_TYPE_LINEAR = "linear"
DRAFT_TYPE_AUCTION = "auction"

# Game statuses; a game in any other status counts as not started.
GAME_STATUS_PRE_GAME = "pre_game"
GAME_STATUS_IN_GAME = "in_game"
GAME_STATUS_COMPLETE = "complete"
QUARTERS = 4
QUARTER_LENGTH = timedelta(minutes=15)

PROJECTIONS_QUERY = """
query get_projections($sport: String!, $season: String!, $season_type: String!,
                      $week: Int!, $player_ids: [String]) {
  stats_for_players_in_week(sport: $sport, season: $season,
                            season_type: $season_type, week: $week,
                            category: "proj", player_ids: $player_ids) {
    player_id team game_id stats
  }
}
"""
GAMES_QUERY = """
query get_games($sport: String!, $season: String!, $season_type: String!,
                $week: Int!) {
  scores(sport: $sport, season: $season, season_type: $season_type,
         week: $week) {
    game_id status start_time metadata
  }
}
"""


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
    scoring_settings: Mapping[str, float]
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
            scoring_settings=MappingProxyType(
                {
                    str(key): float(value)
                    for key, value in scoring.items()
                    if isinstance(value, int | float)
                }
            ),
            roster_positions=_str_tuple(data.get("roster_positions")),
            previous_league_id=_optional_str(data.get("previous_league_id")),
            draft_id=_optional_str(data.get("draft_id")),
            avatar=data.get("avatar"),
        )

    @property
    def avatar_url(self) -> str | None:
        """Return the URL of the league's picture, if it has one."""
        return _avatar_url(self.avatar)

    def fantasy_points(self, stats: Mapping[str, float]) -> float:
        """Score a stat line with the league's scoring settings.

        Projections lump field goals of 50+ yards together and leave out
        missed field goals. Where the league does not score ``fgm_50p``
        itself, the long ones count at the 50-59 yard rate, and the misses
        are the attempts that were not made.
        """
        stats = dict(stats)
        if "fgm_50p" not in self.scoring_settings and "fgm_50_59" not in stats:
            stats["fgm_50_59"] = stats.get("fgm_50p", 0.0)
        if "fgmiss" not in stats:
            stats["fgmiss"] = max(stats.get("fga", 0.0) - stats.get("fgm", 0.0), 0.0)
        return sum(
            points * stats.get(key, 0.0)
            for key, points in self.scoring_settings.items()
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
    team_avatar: str | None
    is_owner: bool

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a league member from an API response object."""
        metadata: dict[str, Any] = data.get("metadata") or {}
        team_name = metadata.get("team_name")
        team_avatar = metadata.get("avatar")
        return cls(
            user_id=str(data["user_id"]),
            display_name=data["display_name"],
            avatar=data.get("avatar"),
            team_name=team_name.strip() if team_name else None,
            # Other managers control this value; only accept a web image URL.
            team_avatar=(
                team_avatar
                if isinstance(team_avatar, str) and team_avatar.startswith("https://")
                else None
            ),
            is_owner=bool(data.get("is_owner")),
        )

    @property
    def name(self) -> str:
        """Return the team name if set, otherwise the display name."""
        return self.team_name or self.display_name

    @property
    def avatar_url(self) -> str | None:
        """Return the team picture if set, otherwise the user's avatar."""
        return self.team_avatar or _avatar_url(self.avatar)


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

    def picture_url(self, sport: str) -> str | None:
        """Return the player's headshot, or the team logo for a team defense.

        Team defenses have no headshot (the CDN refuses the request), so
        they get their team's logo. A defense without a team has no picture.
        """
        if self.position == POSITION_DEFENSE:
            if self.team is None:
                return None
            return TEAM_LOGO_URL.format(sport=sport, team=self.team.lower())
        return PLAYER_PICTURE_URL.format(sport=sport, player_id=self.player_id)

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


def _timestamp(value: Any) -> datetime | None:
    """Convert milliseconds since the epoch to an aware datetime."""
    return None if value is None else datetime.fromtimestamp(int(value) / 1000, UTC)


@dataclass(frozen=True, slots=True)
class SleeperDraft:
    """The draft of a league.

    ``draft_order`` maps user IDs to draft slots (1-based) and
    ``slot_to_roster_id`` maps slots to rosters; both are empty until the
    commissioner sets the order. ``reversal_round`` is the round from which a
    snake draft reverses direction ("third round reversal"), 0 for a plain
    snake.
    """

    draft_id: str
    league_id: str | None
    status: str
    type: str
    season: str
    start_time: datetime | None
    last_picked: datetime | None
    rounds: int
    teams: int
    pick_timer: timedelta
    reversal_round: int
    draft_order: Mapping[str, int]
    slot_to_roster_id: Mapping[int, int]
    is_autopaused: bool

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a draft from an API response object."""
        settings: dict[str, Any] = data.get("settings") or {}
        metadata: dict[str, Any] = data.get("metadata") or {}
        draft_order: dict[str, Any] = data.get("draft_order") or {}
        slot_to_roster_id: dict[str, Any] = data.get("slot_to_roster_id") or {}
        return cls(
            draft_id=str(data["draft_id"]),
            league_id=_optional_str(data.get("league_id")),
            status=data["status"],
            type=data["type"],
            season=str(data["season"]),
            start_time=_timestamp(data.get("start_time")),
            last_picked=_timestamp(data.get("last_picked")),
            rounds=int(settings.get("rounds", 0)),
            teams=int(settings.get("teams", 0)),
            pick_timer=timedelta(seconds=int(settings.get("pick_timer", 0))),
            reversal_round=int(settings.get("reversal_round", 0)),
            draft_order=MappingProxyType(
                {str(key): int(value) for key, value in draft_order.items()}
            ),
            slot_to_roster_id=MappingProxyType(
                {int(key): int(value) for key, value in slot_to_roster_id.items()}
            ),
            is_autopaused=str(metadata.get("is_autopaused")).lower() == "true",
        )

    @property
    def total_picks(self) -> int:
        """Return the number of picks in the whole draft."""
        return self.rounds * self.teams

    def round_of(self, pick_no: int) -> int:
        """Return the round (1-based) an overall pick number falls in."""
        return (pick_no - 1) // self.teams + 1

    def pick_in_round(self, pick_no: int) -> int:
        """Return the position (1-based) of an overall pick number in its round."""
        return (pick_no - 1) % self.teams + 1

    def slot_of(self, pick_no: int) -> int | None:
        """Return the draft slot that originally owns an overall pick number.

        Auction drafts have no pick order, so they yield ``None``. In a snake
        draft even rounds run backwards; from ``reversal_round`` on, the
        direction of every round is flipped.
        """
        if self.type == DRAFT_TYPE_AUCTION or self.teams < 1:
            return None
        round_no = self.round_of(pick_no)
        position = self.pick_in_round(pick_no)
        forward = True
        if self.type == DRAFT_TYPE_SNAKE:
            forward = round_no % 2 == 1
            if self.reversal_round and round_no >= self.reversal_round:
                forward = not forward
        return position if forward else self.teams - position + 1


@dataclass(frozen=True, slots=True)
class SleeperDraftPick:
    """One pick of a draft.

    The player's details are copied from the pick's metadata, so picks are
    readable without the player list.
    """

    draft_id: str
    pick_no: int
    round: int
    draft_slot: int
    roster_id: int | None
    picked_by: str | None
    player_id: str
    player: SleeperPlayer
    is_keeper: bool

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a pick from an API response object."""
        player_id = str(data["player_id"])
        metadata: dict[str, Any] = data.get("metadata") or {}
        return cls(
            draft_id=str(data["draft_id"]),
            pick_no=int(data["pick_no"]),
            round=int(data["round"]),
            draft_slot=int(data["draft_slot"]),
            roster_id=_optional_int(data.get("roster_id")),
            picked_by=_optional_str(data.get("picked_by")) or None,
            player_id=player_id,
            player=SleeperPlayer.from_json({**metadata, "player_id": player_id}),
            is_keeper=bool(data.get("is_keeper")),
        )


@dataclass(frozen=True, slots=True)
class SleeperTradedPick:
    """A draft pick owned by another roster than the one it started with.

    ``roster_id`` is the original owner (the roster in that draft slot),
    ``owner_id`` the roster that owns the pick now.
    """

    season: str
    round: int
    roster_id: int
    owner_id: int
    previous_owner_id: int | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a traded pick from an API response object."""
        return cls(
            season=str(data["season"]),
            round=int(data["round"]),
            roster_id=int(data["roster_id"]),
            owner_id=int(data["owner_id"]),
            previous_owner_id=_optional_int(data.get("previous_owner_id")),
        )


@dataclass(frozen=True, slots=True)
class SleeperProjection:
    """A player's projected stat line for one week.

    Score it with :meth:`SleeperLeague.fantasy_points`; the projected points
    Sleeper includes (``pts_ppr`` etc.) only fit leagues with stock scoring.
    """

    player_id: str
    team: str | None
    game_id: str | None
    stats: Mapping[str, float]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a projection from an API response object."""
        stats: dict[str, Any] = data.get("stats") or {}
        return cls(
            player_id=str(data["player_id"]),
            team=data.get("team") or None,
            game_id=_optional_str(data.get("game_id")),
            stats=MappingProxyType(
                {
                    str(key): float(value)
                    for key, value in stats.items()
                    if isinstance(value, int | float)
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class SleeperGame:
    """One game of a week and how far it has got.

    ``clock`` is the time left in the current quarter, ``None`` before the
    game.
    """

    game_id: str
    status: str
    start_time: datetime | None
    home_team: str | None
    away_team: str | None
    quarter: int | None
    clock: timedelta | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """Build a game from an API response object."""
        metadata: dict[str, Any] = data.get("metadata") or {}
        return cls(
            game_id=str(data["game_id"]),
            status=data.get("status") or GAME_STATUS_PRE_GAME,
            start_time=_timestamp(data.get("start_time")),
            home_team=metadata.get("home_team") or None,
            away_team=metadata.get("away_team") or None,
            # An empty string before kickoff.
            quarter=_optional_int(metadata.get("quarter_num") or None),
            clock=_clock(metadata.get("time_remaining")),
        )

    @property
    def teams(self) -> tuple[str, ...]:
        """Return the abbreviations of the teams playing."""
        return tuple(team for team in (self.away_team, self.home_team) if team)

    @property
    def remaining(self) -> float:
        """Return the share of the game still to be played, from 1 to 0.

        Overtime counts as over: what is left of it is too short to matter.
        """
        if self.status == GAME_STATUS_COMPLETE:
            return 0.0
        if self.status != GAME_STATUS_IN_GAME or self.quarter is None:
            return 1.0
        if self.quarter > QUARTERS:
            return 0.0
        left = (QUARTERS - self.quarter) * QUARTER_LENGTH + (self.clock or timedelta())
        return max(0.0, min(1.0, left / (QUARTERS * QUARTER_LENGTH)))


def _clock(value: Any) -> timedelta | None:
    """Convert a game clock like ``"7:30"`` to the time it shows."""
    if not isinstance(value, str):
        return None
    minutes, _, seconds = value.partition(":")
    try:
        return timedelta(minutes=int(minutes), seconds=int(seconds or 0))
    except ValueError:
        return None


def _parse_players(text: str) -> dict[str, SleeperPlayer]:
    """Decode the player list. CPU heavy, meant to run in an executor."""
    data: dict[str, dict[str, Any]] = json.loads(text) or {}
    return {
        player_id: SleeperPlayer.from_json(item) for player_id, item in data.items()
    }


def _avatar_url(avatar_id: str | None) -> str | None:
    """Return the thumbnail URL (80x80) of an avatar ID, if there is one."""
    return f"{AVATAR_URL}/thumbs/{avatar_id}" if avatar_id else None


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
        """Perform a GET request on the API and return the raw body."""
        url = f"{BASE_URL}/{path.lstrip('/')}"
        body = await self._get_bytes(url, request_timeout=request_timeout)
        return body.decode()

    async def _get_bytes(
        self,
        url: str,
        *,
        request_timeout: float | None = None,
        not_found_statuses: tuple[int, ...] = (404,),
    ) -> bytes:
        """Perform a GET request and return the body."""
        return await self._request(
            "GET",
            url,
            request_timeout=request_timeout,
            not_found_statuses=not_found_statuses,
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        request_timeout: float | None = None,
        not_found_statuses: tuple[int, ...] = (404,),
    ) -> bytes:
        """Perform a request and return the body."""
        _LOGGER.debug("%s %s", method, url)
        try:
            async with asyncio.timeout(request_timeout or self._timeout):
                response = await self._session.request(method, url, json=payload)
                response.raise_for_status()
                return await response.read()
        except ClientResponseError as err:
            if err.status in not_found_statuses:
                raise SleeperNotFoundError(url) from err
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

    async def _graphql_list(
        self, query: str, field: str, variables: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Run a GraphQL query and return the list in one of its fields.

        GraphQL reports errors (a query that needs a login, for one) with
        HTTP 200 and an ``errors`` list next to the data.
        """
        body = await self._request(
            "POST", GRAPHQL_URL, payload={"query": query, "variables": variables}
        )
        data: dict[str, Any] = json.loads(body) or {}
        if errors := data.get("errors"):
            message = "; ".join(str(error.get("message")) for error in errors)
            raise SleeperConnectionError(f"GraphQL error in {field}: {message}")
        return (data.get("data") or {}).get(field) or []

    async def get_player_picture(self, player_id: str) -> tuple[bytes, str]:
        """Fetch a player's headshot and return it with its media type.

        The CDN labels headshots as JPEG but serves PNG, so the media type is
        taken from the file itself. It answers 403 for players without one.
        """
        picture = await self._get_bytes(
            PLAYER_PICTURE_URL.format(sport=self._sport, player_id=player_id),
            not_found_statuses=(403, 404),
        )
        media_type = "image/png" if picture.startswith(PNG_SIGNATURE) else "image/jpeg"
        return picture, media_type

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

    async def get_draft(self, draft_id: str) -> SleeperDraft:
        """Fetch a single draft."""
        data = await self._get_object(f"draft/{draft_id}", f"Draft {draft_id!r}")
        return SleeperDraft.from_json(data)

    async def get_draft_picks(self, draft_id: str) -> tuple[SleeperDraftPick, ...]:
        """Fetch the picks made so far in a draft, in pick order."""
        data = await self._get_list(f"draft/{draft_id}/picks")
        return tuple(
            sorted(
                (SleeperDraftPick.from_json(item) for item in data),
                key=lambda pick: pick.pick_no,
            )
        )

    async def get_draft_traded_picks(
        self, draft_id: str
    ) -> tuple[SleeperTradedPick, ...]:
        """Fetch the traded picks of a draft."""
        data = await self._get_list(f"draft/{draft_id}/traded_picks")
        return tuple(SleeperTradedPick.from_json(item) for item in data)

    async def get_projections(
        self, season: str, season_type: str, week: int, player_ids: Iterable[str]
    ) -> dict[str, SleeperProjection]:
        """Fetch the projections of some players for one week, keyed by player.

        Players without a projection (on a bye, for one) are left out.
        """
        data = await self._graphql_list(
            PROJECTIONS_QUERY,
            "stats_for_players_in_week",
            {
                "sport": self._sport,
                "season": season,
                "season_type": season_type,
                "week": week,
                "player_ids": list(player_ids),
            },
        )
        projections = (SleeperProjection.from_json(item) for item in data)
        return {projection.player_id: projection for projection in projections}

    async def get_games(
        self, season: str, season_type: str, week: int
    ) -> tuple[SleeperGame, ...]:
        """Fetch the games of one week with their live state."""
        data = await self._graphql_list(
            GAMES_QUERY,
            "scores",
            {
                "sport": self._sport,
                "season": season,
                "season_type": season_type,
                "week": week,
            },
        )
        return tuple(SleeperGame.from_json(item) for item in data)
