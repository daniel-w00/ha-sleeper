"""Data update coordinator for the Sleeper integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SleeperClient,
    SleeperError,
    SleeperLeague,
    SleeperLeagueUser,
    SleeperMatchup,
    SleeperRoster,
    SleeperSportState,
    SleeperUser,
)
from .const import (
    DEFAULT_SPORT,
    DOMAIN,
    IDLE_OFFSEASON_UPDATE_INTERVAL,
    IDLE_SEASON_UPDATE_INTERVAL,
    LEAGUE_REFRESH_INTERVAL,
    LEAGUE_STATUS_IN_SEASON,
    LIVE_GRACE_PERIOD,
    LIVE_UPDATE_INTERVAL,
    SCORING_SEASON_TYPES,
)

_LOGGER = logging.getLogger(__name__)

type SleeperConfigEntry = ConfigEntry[SleeperCoordinator]


@dataclass(frozen=True, slots=True)
class SleeperLeagueData:
    """Everything the entities of one league consume.

    All derived values are computed from the API models here so the entities
    stay trivial. ``my_roster`` is ``None`` if the account owns no roster in
    the league (it left, or is a commissioner without a team).
    """

    league: SleeperLeague
    rosters: tuple[SleeperRoster, ...]
    users: tuple[SleeperLeagueUser, ...]
    matchups: tuple[SleeperMatchup, ...]
    week: int
    my_roster: SleeperRoster | None

    def user_for_roster(self, roster: SleeperRoster) -> SleeperLeagueUser | None:
        """Return the league member owning a roster, if any."""
        if roster.owner_id is None:
            return None
        return next(
            (user for user in self.users if user.user_id == roster.owner_id), None
        )

    @property
    def standings(self) -> tuple[SleeperRoster, ...]:
        """Return the rosters ordered by Sleeper's standings tiebreak.

        Win percentage (a tie counts half a win) first, then points for.
        """
        return tuple(
            sorted(
                self.rosters,
                key=lambda roster: (
                    -(roster.wins + roster.ties / 2),
                    -roster.points_for,
                ),
            )
        )

    @property
    def rank(self) -> int | None:
        """Return the account's position in the standings, starting at 1."""
        if self.my_roster is None:
            return None
        return self.standings.index(self.my_roster) + 1

    @property
    def my_matchup(self) -> SleeperMatchup | None:
        """Return the account's matchup of the week, ``None`` on a bye."""
        if self.my_roster is None:
            return None
        return next(
            (
                matchup
                for matchup in self.matchups
                if matchup.roster_id == self.my_roster.roster_id
                and matchup.matchup_id is not None
            ),
            None,
        )

    @property
    def opponent_matchup(self) -> SleeperMatchup | None:
        """Return the other side of the account's matchup."""
        if (mine := self.my_matchup) is None:
            return None
        return next(
            (
                matchup
                for matchup in self.matchups
                if matchup.matchup_id == mine.matchup_id
                and matchup.roster_id != mine.roster_id
            ),
            None,
        )

    @property
    def opponent_roster(self) -> SleeperRoster | None:
        """Return the roster the account plays against this week."""
        if (opponent := self.opponent_matchup) is None:
            return None
        return next(
            (
                roster
                for roster in self.rosters
                if roster.roster_id == opponent.roster_id
            ),
            None,
        )

    @property
    def opponent_name(self) -> str | None:
        """Return the opponent's team name, ``None`` for an unowned roster."""
        if (roster := self.opponent_roster) is None:
            return None
        if (user := self.user_for_roster(roster)) is None:
            return None
        return user.name


@dataclass(frozen=True, slots=True)
class SleeperData:
    """All data the entities of one config entry consume."""

    user: SleeperUser
    state: SleeperSportState
    leagues: dict[str, SleeperLeagueData] = field(default_factory=dict)


class SleeperCoordinator(DataUpdateCoordinator[SleeperData]):
    """Fetch data from Sleeper for one user account.

    Every poll fetches the sport state plus rosters and matchups of each
    league. The league list, league members and the user profile are cheap
    to keep but change rarely, so they are refreshed only hourly or when
    Sleeper moves to a new league season.
    """

    config_entry: SleeperConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: SleeperConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {config_entry.title}",
            update_interval=IDLE_SEASON_UPDATE_INTERVAL,
        )
        if TYPE_CHECKING:
            assert config_entry.unique_id is not None
        self.client = SleeperClient(async_get_clientsession(hass), sport=DEFAULT_SPORT)
        self.user_id: str = config_entry.unique_id
        self._user: SleeperUser | None = None
        self._leagues: tuple[SleeperLeague, ...] = ()
        self._league_users: dict[str, tuple[SleeperLeagueUser, ...]] = {}
        self._league_season: str | None = None
        self._leagues_refreshed: datetime | None = None
        self._last_points: dict[tuple[str, int], float] = {}
        self._last_points_change: datetime | None = None

    @override
    async def _async_update_data(self) -> SleeperData:
        """Fetch the latest data from Sleeper."""
        now = dt_util.utcnow()
        try:
            state = await self.client.get_sport_state()
            if self._leagues_need_refresh(now, state):
                await self._async_refresh_leagues(now, state)
            leagues = {
                league.league_id: await self._async_fetch_league(league, state)
                for league in self._leagues
            }
        except SleeperError as err:
            raise UpdateFailed(f"Error communicating with Sleeper: {err}") from err

        if TYPE_CHECKING:
            assert self._user is not None
        self._update_interval_for(now, state, leagues)
        return SleeperData(user=self._user, state=state, leagues=leagues)

    def _leagues_need_refresh(self, now: datetime, state: SleeperSportState) -> bool:
        """Return whether the league list is missing, stale or from an old season."""
        return (
            self._leagues_refreshed is None
            or now - self._leagues_refreshed >= LEAGUE_REFRESH_INTERVAL
            or self._league_season != state.league_season
        )

    async def _async_refresh_leagues(
        self, now: datetime, state: SleeperSportState
    ) -> None:
        """Reload the user profile, the league list and the league members."""
        self._user = await self.client.get_user(self.user_id)
        leagues = await self.client.get_user_leagues(self.user_id, state.league_season)
        self._league_users = {
            league.league_id: await self.client.get_league_users(league.league_id)
            for league in leagues
        }
        self._leagues = leagues
        self._league_season = state.league_season
        self._leagues_refreshed = now
        _LOGGER.debug(
            "Loaded %d leagues for season %s", len(leagues), state.league_season
        )
        self._async_remove_stale_league_devices()

    @callback
    def _async_remove_stale_league_devices(self) -> None:
        """Remove the devices of leagues the account is no longer part of.

        Removing a device also removes its entities. An empty league list is
        left alone: it is far more likely a hiccup than the account leaving
        every league at once, and the devices vanish on the next reload.
        """
        if not self._leagues:
            return
        keep = {self.user_id, *(league.league_id for league in self._leagues)}
        device_registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(
            device_registry, self.config_entry.entry_id
        ):
            identifiers = {
                identifier
                for domain, identifier in device.identifiers
                if domain == DOMAIN
            }
            if identifiers and not identifiers & keep:
                _LOGGER.debug("Removing device of stale league %s", device.name)
                device_registry.async_update_device(
                    device.id, remove_config_entry_id=self.config_entry.entry_id
                )

    async def _async_fetch_league(
        self, league: SleeperLeague, state: SleeperSportState
    ) -> SleeperLeagueData:
        """Fetch the frequently changing data of one league."""
        rosters = await self.client.get_rosters(league.league_id)
        matchups: tuple[SleeperMatchup, ...] = ()
        if (
            league.status == LEAGUE_STATUS_IN_SEASON
            and state.season_type in SCORING_SEASON_TYPES
        ):
            matchups = await self.client.get_matchups(league.league_id, state.week)
        return SleeperLeagueData(
            league=league,
            rosters=rosters,
            users=self._league_users.get(league.league_id, ()),
            matchups=matchups,
            week=state.week,
            my_roster=next(
                (roster for roster in rosters if roster.owner_id == self.user_id),
                None,
            ),
        )

    def _update_interval_for(
        self,
        now: datetime,
        state: SleeperSportState,
        leagues: dict[str, SleeperLeagueData],
    ) -> None:
        """Pick the next update interval from the observed scoring activity."""
        points = {
            (league_id, matchup.roster_id): matchup.points
            for league_id, data in leagues.items()
            for matchup in data.matchups
        }
        changed = any(
            self._last_points[key] != value
            for key, value in points.items()
            if key in self._last_points
        )
        self._last_points = points
        if changed:
            self._last_points_change = now

        interval: timedelta
        if (
            self._last_points_change is not None
            and now - self._last_points_change < LIVE_GRACE_PERIOD
        ):
            interval = LIVE_UPDATE_INTERVAL
        elif state.season_type in SCORING_SEASON_TYPES:
            interval = IDLE_SEASON_UPDATE_INTERVAL
        else:
            interval = IDLE_OFFSEASON_UPDATE_INTERVAL

        if interval != self.update_interval:
            _LOGGER.debug("Switching update interval to %s", interval)
            self.update_interval = interval
