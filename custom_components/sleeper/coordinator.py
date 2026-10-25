"""Data update coordinator for the Sleeper integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging
import math
from types import MappingProxyType
from typing import TYPE_CHECKING, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    DRAFT_STATUS_COMPLETE,
    DRAFT_STATUS_DRAFTING,
    DRAFT_STATUS_PAUSED,
    DRAFT_STATUS_PRE_DRAFT,
    DRAFT_TYPE_AUCTION,
    GAME_STATUS_IN_GAME,
    GAME_STATUS_PRE_GAME,
    SleeperClient,
    SleeperDraft,
    SleeperDraftPick,
    SleeperError,
    SleeperGame,
    SleeperLeague,
    SleeperLeagueUser,
    SleeperMatchup,
    SleeperNotFoundError,
    SleeperPlayer,
    SleeperProjection,
    SleeperRoster,
    SleeperSportState,
    SleeperTradedPick,
    SleeperUser,
)
from .const import (
    DEFAULT_SPORT,
    DOMAIN,
    DRAFT_START_GRACE_PERIOD,
    DRAFT_UPDATE_INTERVAL,
    EMPTY_SLOT_PLAYER_ID,
    EVENT_DRAFT_ON_THE_CLOCK,
    EVENT_DRAFT_PICK,
    EVENT_PLAYER_SCORED,
    GAMES_REFRESH_INTERVAL,
    IDLE_OFFSEASON_UPDATE_INTERVAL,
    IDLE_SEASON_UPDATE_INTERVAL,
    KICKOFF_GRACE_PERIOD,
    LEAGUE_REFRESH_INTERVAL,
    LEAGUE_STATUS_IN_SEASON,
    LIVE_GRACE_PERIOD,
    LIVE_UPDATE_INTERVAL,
    MATCHUP_MARGIN_STDDEV,
    NOTABLE_POINTS_DELTA,
    PLAYER_STATUS_INACTIVE,
    PROJECTIONS_REFRESH_INTERVAL,
    SCORING_SEASON_TYPES,
    STARTER_OUT_INJURY_STATUSES,
)
from .players import SleeperPlayers


def league_device_identifier(user_id: str, league_id: str) -> tuple[str, str]:
    """Return the device registry identifier of a league device.

    League devices are per account: two accounts in the same league get two
    devices, each linked to its own account device.
    """
    return (DOMAIN, f"{user_id}_{league_id}")


_LOGGER = logging.getLogger(__name__)

# Draft statuses in which picks are being made (or waiting to be made).
DRAFT_ACTIVE_STATUSES = frozenset({DRAFT_STATUS_DRAFTING, DRAFT_STATUS_PAUSED})

type SleeperConfigEntry = ConfigEntry[SleeperCoordinator]

type PlayerLookup = Callable[[str], SleeperPlayer | None]


def _no_player(player_id: str) -> SleeperPlayer | None:
    """Player lookup used when no player list is available."""
    return None


@dataclass(frozen=True, slots=True)
class SleeperPointsChange:
    """A starter's fantasy points changed notably between two polls."""

    roster_id: int
    player_id: str
    player: SleeperPlayer | None
    picture: str | None
    previous: float
    points: float
    is_mine: bool

    @property
    def delta(self) -> float:
        """Return the change in points."""
        return round(self.points - self.previous, 2)

    @property
    def player_name(self) -> str:
        """Return the player's name, or the ID if the player is unknown."""
        return self.player.name if self.player is not None else self.player_id


@dataclass(frozen=True, slots=True)
class SleeperStarter:
    """A starting slot of the account's roster."""

    slot: str
    player_id: str
    player: SleeperPlayer | None

    @property
    def name(self) -> str:
        """Return the player's name, the ID if unknown, or ``None`` when empty."""
        if self.player is not None:
            return self.player.name
        return self.player_id

    @property
    def is_empty(self) -> bool:
        """Return whether the slot has no player."""
        return self.player_id == EMPTY_SLOT_PLAYER_ID

    @property
    def status(self) -> str | None:
        """Return the reason the starter is not expected to play, if any."""
        if self.is_empty:
            return "Empty"
        if self.player is None:
            return None
        if self.player.injury_status in STARTER_OUT_INJURY_STATUSES:
            return self.player.injury_status
        if self.player.status == PLAYER_STATUS_INACTIVE:
            return self.player.status
        return None


@dataclass(frozen=True, slots=True)
class SleeperOutlook:
    """Where one side of a matchup is heading, from its starters' projections.

    ``remaining`` is the projected points still to come: each starter's
    projection times the share of their game that is left. ``projection``
    is the line-up's pre-game projection, all games included.
    """

    points: float
    remaining: float
    projection: float
    yet_to_play: int
    playing: int

    @property
    def projected(self) -> float:
        """Return the points the side is projected to end the week with."""
        return round(self.points + self.remaining, 2)


@dataclass(frozen=True, slots=True)
class SleeperUpcomingPick:
    """A draft pick that has not been made yet, and the team that owns it."""

    pick_no: int
    round: int
    pick_in_round: int
    roster_id: int | None
    user: SleeperLeagueUser | None
    is_mine: bool

    @property
    def label(self) -> str:
        """Return the pick in Sleeper's ``round.pick`` notation, e.g. ``2.06``."""
        return f"{self.round}.{self.pick_in_round:02d}"

    @property
    def team_name(self) -> str | None:
        """Return the name of the team that owns the pick, if known."""
        return None if self.user is None else self.user.name


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
    player_lookup: PlayerLookup = _no_player
    # Notable changes since the last poll, and the biggest one seen so far.
    points_changes: tuple[SleeperPointsChange, ...] = ()
    last_big_play: SleeperPointsChange | None = None
    # The league's draft; ``None`` if the league has none. Picks are only
    # loaded once the draft has started, and the new ones since the last
    # poll are singled out for the events.
    draft: SleeperDraft | None = None
    draft_picks: tuple[SleeperDraftPick, ...] = ()
    traded_picks: tuple[SleeperTradedPick, ...] = ()
    new_draft_picks: tuple[SleeperDraftPick, ...] = ()
    # Projections of the starters in the account's matchup and the week's
    # games by team; ``None`` when they could not be loaded.
    projections: Mapping[str, SleeperProjection] | None = None
    games: Mapping[str, SleeperGame] | None = None

    def _starters(self, player_ids: tuple[str, ...]) -> tuple[SleeperStarter, ...]:
        """Resolve a list of starter IDs to slots and players."""
        slots = self.league.roster_positions
        return tuple(
            SleeperStarter(
                slot=slots[index] if index < len(slots) else "?",
                player_id=player_id,
                player=self.player_lookup(player_id),
            )
            for index, player_id in enumerate(player_ids)
        )

    @property
    def my_starters(self) -> tuple[SleeperStarter, ...]:
        """Return the account's current line-up as set on the roster.

        Sleeper lets you edit the line-up for the next week while the current
        week is still being played, so this can differ from
        ``matchup_starters``.
        """
        if self.my_roster is None:
            return ()
        return self._starters(self.my_roster.starters)

    @property
    def matchup_starters(self) -> tuple[SleeperStarter, ...]:
        """Return the line-up locked in for the current week's matchup."""
        if self.my_matchup is None:
            return ()
        return self._starters(self.my_matchup.starters)

    @property
    def starters_out(self) -> tuple[SleeperStarter, ...]:
        """Return starters not expected to play, including empty slots."""
        return tuple(
            starter for starter in self.my_starters if starter.status is not None
        )

    def user_for_roster(self, roster: SleeperRoster) -> SleeperLeagueUser | None:
        """Return the league member owning a roster, if any."""
        if roster.owner_id is None:
            return None
        return next(
            (user for user in self.users if user.user_id == roster.owner_id), None
        )

    def user_for_roster_id(self, roster_id: int | None) -> SleeperLeagueUser | None:
        """Return the league member owning the roster with an ID, if any."""
        roster = next(
            (roster for roster in self.rosters if roster.roster_id == roster_id), None
        )
        return None if roster is None else self.user_for_roster(roster)

    # Draft

    @property
    def draft_in_progress(self) -> bool:
        """Return whether the draft has started and is not finished."""
        return self.draft is not None and self.draft.status in DRAFT_ACTIVE_STATUSES

    @property
    def last_draft_pick(self) -> SleeperDraftPick | None:
        """Return the most recent pick of the draft, if any was made."""
        return max(self.draft_picks, key=lambda pick: pick.pick_no, default=None)

    @property
    def next_pick_no(self) -> int | None:
        """Return the number of the pick that is up now.

        ``None`` before the draft, once it is finished, and in auction drafts,
        which have no pick order.
        """
        if (draft := self.draft) is None or not self.draft_in_progress:
            return None
        if draft.type == DRAFT_TYPE_AUCTION:
            return None
        last = self.last_draft_pick
        pick_no = 1 if last is None else last.pick_no + 1
        return pick_no if pick_no <= draft.total_picks else None

    def pick_owner(self, pick_no: int) -> int | None:
        """Return the roster that owns a pick, taking trades into account.

        The slot's roster comes from the draft's slot map, or from the draft
        order and the rosters' owners when Sleeper omits the map.
        """
        if (draft := self.draft) is None or (slot := draft.slot_of(pick_no)) is None:
            return None
        roster_id = draft.slot_to_roster_id.get(slot)
        if roster_id is None:
            user_id = next(
                (
                    user
                    for user, user_slot in draft.draft_order.items()
                    if user_slot == slot
                ),
                None,
            )
            roster_id = next(
                (
                    roster.roster_id
                    for roster in self.rosters
                    if user_id is not None and roster.owner_id == user_id
                ),
                None,
            )
        if roster_id is None:
            return None
        round_no = draft.round_of(pick_no)
        return next(
            (
                trade.owner_id
                for trade in self.traded_picks
                if trade.round == round_no and trade.roster_id == roster_id
            ),
            roster_id,
        )

    def _upcoming_pick(self, pick_no: int) -> SleeperUpcomingPick:
        """Describe a pick that is still to be made."""
        if TYPE_CHECKING:
            assert self.draft is not None
        roster_id = self.pick_owner(pick_no)
        return SleeperUpcomingPick(
            pick_no=pick_no,
            round=self.draft.round_of(pick_no),
            pick_in_round=self.draft.pick_in_round(pick_no),
            roster_id=roster_id,
            user=self.user_for_roster_id(roster_id),
            is_mine=self.my_roster is not None
            and roster_id == self.my_roster.roster_id,
        )

    @property
    def on_the_clock(self) -> SleeperUpcomingPick | None:
        """Return the pick that is up now and the team that has to make it."""
        if (pick_no := self.next_pick_no) is None:
            return None
        return self._upcoming_pick(pick_no)

    @property
    def my_next_pick(self) -> SleeperUpcomingPick | None:
        """Return the account's next pick, ``None`` if it has none left."""
        if (pick_no := self.next_pick_no) is None or self.my_roster is None:
            return None
        if TYPE_CHECKING:
            assert self.draft is not None
        return next(
            (
                self._upcoming_pick(candidate)
                for candidate in range(pick_no, self.draft.total_picks + 1)
                if self.pick_owner(candidate) == self.my_roster.roster_id
            ),
            None,
        )

    @property
    def picks_until_mine(self) -> int | None:
        """Return how many picks are made before the account's next one."""
        if (mine := self.my_next_pick) is None or (
            pick_no := self.next_pick_no
        ) is None:
            return None
        return mine.pick_no - pick_no

    @property
    def pick_deadline(self) -> datetime | None:
        """Return when the current pick's timer runs out.

        Unknown while the draft is paused (by the commissioner or the
        overnight autopause), without a pick timer, and before the first
        pick of a draft without a start time.
        """
        if (draft := self.draft) is None or self.next_pick_no is None:
            return None
        if draft.status != DRAFT_STATUS_DRAFTING or draft.is_autopaused:
            return None
        if not draft.pick_timer:
            return None
        since = draft.last_picked or draft.start_time
        return None if since is None else since + draft.pick_timer

    def pick_is_mine(self, pick: SleeperDraftPick) -> bool:
        """Return whether the account made a pick."""
        if self.my_roster is None:
            return False
        if pick.roster_id is not None:
            return pick.roster_id == self.my_roster.roster_id
        return pick.picked_by == self.my_roster.owner_id

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

    def starter_game(self, player_id: str) -> SleeperGame | None:
        """Return the game a starter plays in this week, if any.

        The team comes from the projection, or from the player list for
        players without one; a team without a game this week is on a bye.
        """
        if self.games is None:
            return None
        projection = (
            None if self.projections is None else self.projections.get(player_id)
        )
        player = self.player_lookup(player_id)
        team = projection.team if projection else player.team if player else None
        return self.games.get(team) if team else None

    @property
    def matchup_games(self) -> tuple[SleeperGame, ...]:
        """Return the games that starters on either side of the matchup play in."""
        games = {
            game.game_id: game
            for matchup in (self.my_matchup, self.opponent_matchup)
            if matchup is not None
            for player_id in matchup.starters
            if player_id != EMPTY_SLOT_PLAYER_ID
            and (game := self.starter_game(player_id)) is not None
        }
        return tuple(games[game_id] for game_id in sorted(games))

    def _outlook(self, matchup: SleeperMatchup | None) -> SleeperOutlook | None:
        """Project where one side of the account's matchup ends the week.

        A projected player whose game is missing from the schedule counts as
        not started.
        """
        if matchup is None or self.projections is None or self.games is None:
            return None
        remaining = projection_total = 0.0
        yet_to_play = playing = 0
        for player_id in matchup.starters:
            if player_id == EMPTY_SLOT_PLAYER_ID:
                continue
            projection = self.projections.get(player_id)
            game = self.starter_game(player_id)
            if game is None and projection is None:
                continue
            share = 1.0 if game is None else game.remaining
            if game is not None and game.status == GAME_STATUS_IN_GAME:
                playing += 1
            elif share == 1.0:
                yet_to_play += 1
            if projection is not None:
                points = self.league.fantasy_points(projection.stats)
                projection_total += points
                remaining += points * share
        return SleeperOutlook(
            points=matchup.points,
            remaining=round(remaining, 2),
            projection=round(projection_total, 2),
            yet_to_play=yet_to_play,
            playing=playing,
        )

    @property
    def my_outlook(self) -> SleeperOutlook | None:
        """Return the projection of the account's side of the matchup."""
        return self._outlook(self.my_matchup)

    @property
    def opponent_outlook(self) -> SleeperOutlook | None:
        """Return the projection of the opponent's side of the matchup."""
        return self._outlook(self.opponent_matchup)

    @property
    def win_probability(self) -> float | None:
        """Return the account's chance to win its matchup, in percent.

        The final margin is taken as normally distributed around the
        projected margin. Its spread shrinks with the square root of the
        share of both line-ups' projected points still to come, so the
        chance settles at 0 or 100 % (50 % for a tie) once all games are
        over.
        """
        if (mine := self.my_outlook) is None or (
            theirs := self.opponent_outlook
        ) is None:
            return None
        margin = mine.projected - theirs.projected
        projection = mine.projection + theirs.projection
        to_come = mine.remaining + theirs.remaining
        spread = (
            MATCHUP_MARGIN_STDDEV * math.sqrt(to_come / projection)
            if projection > 0
            else 0.0
        )
        if spread == 0:
            return 50.0 if margin == 0 else 100.0 if margin > 0 else 0.0
        return round(50 * (1 + math.erf(margin / (spread * math.sqrt(2)))), 1)

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
    def my_user(self) -> SleeperLeagueUser | None:
        """Return the league member entry of the account's roster."""
        if self.my_roster is None:
            return None
        return self.user_for_roster(self.my_roster)

    @property
    def my_team_name(self) -> str | None:
        """Return the account's team name, ``None`` without a roster."""
        if (user := self.my_user) is None:
            return None
        return user.name

    @property
    def opponent_user(self) -> SleeperLeagueUser | None:
        """Return the league member the account plays against this week."""
        if (roster := self.opponent_roster) is None:
            return None
        return self.user_for_roster(roster)

    @property
    def opponent_name(self) -> str | None:
        """Return the opponent's team name, ``None`` for an unowned roster."""
        if (user := self.opponent_user) is None:
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

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SleeperConfigEntry,
        players: SleeperPlayers,
    ) -> None:
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
        self.sport: str = DEFAULT_SPORT
        self.client = SleeperClient(async_get_clientsession(hass), sport=self.sport)
        self.players = players
        self.user_id: str = config_entry.unique_id
        self._user: SleeperUser | None = None
        self._leagues: tuple[SleeperLeague, ...] = ()
        self._league_users: dict[str, tuple[SleeperLeagueUser, ...]] = {}
        self._league_season: str | None = None
        self._leagues_refreshed: datetime | None = None
        self._last_points: dict[tuple[str, int], float] = {}
        # league_id -> draft; draft_id -> its picks and trades
        self._drafts: dict[str, SleeperDraft] = {}
        self._draft_picks: dict[str, tuple[SleeperDraftPick, ...]] = {}
        self._traded_picks: dict[str, tuple[SleeperTradedPick, ...]] = {}
        # draft_id -> time of the last pick, highest pick number and pick on
        # the clock as of the last poll
        self._last_picked: dict[str, datetime | None] = {}
        self._last_pick_no: dict[str, int] = {}
        self._last_on_the_clock: dict[str, int | None] = {}
        # When matchup points or draft picks last changed between two polls.
        self._last_activity: datetime | None = None
        # (league_id, week, roster_id, player_id) -> points of the last poll
        self._last_player_points: dict[tuple[str, int, int, str], float] = {}
        # league_id -> biggest change of the last poll that had any
        self._last_big_play: dict[str, SleeperPointsChange] = {}
        # The running background download of the player list, if any.
        self._players_refresh: asyncio.Task[None] | None = None
        # Whether the last attempt to load projections and games failed.
        self._projections_failed = False
        # The week the projections and games below are of; the projections
        # requested, the ones that cannot change any more, the time of the
        # last full refresh of the others, and the games with their time.
        self._projections_week: tuple[str, str, int] | None = None
        self._projections: dict[str, SleeperProjection] = {}
        self._projections_requested: set[str] = set()
        self._final_projections: set[str] = set()
        self._projections_fetched: datetime | None = None
        self._games: tuple[SleeperGame, ...] | None = None
        self._games_fetched: datetime | None = None

    @override
    async def _async_update_data(self) -> SleeperData:
        """Fetch the latest data from Sleeper."""
        now = dt_util.utcnow()
        if self.players.stale:
            self._async_schedule_player_refresh()
        try:
            state = await self.client.get_sport_state()
            if self._leagues_need_refresh(now, state):
                await self._async_refresh_leagues(now, state)
            # One league takes three requests; fetching the leagues in
            # parallel keeps a poll as short as its slowest league.
            fetched = await asyncio.gather(
                *(self._async_fetch_league(league, state) for league in self._leagues)
            )
        except SleeperError as err:
            raise UpdateFailed(f"Error communicating with Sleeper: {err}") from err
        leagues = await self._async_add_projections(
            now, state, {data.league.league_id: data for data in fetched}
        )

        if TYPE_CHECKING:
            assert self._user is not None
        self._update_interval_for(now, state, leagues)
        leagues = self._detect_points_changes(leagues)
        leagues = self._detect_new_picks(leagues)
        for data in leagues.values():
            self._async_fire_scoring_events(data)
            self._async_fire_draft_events(data)
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
        self._user, leagues = await asyncio.gather(
            self.client.get_user(self.user_id),
            self.client.get_user_leagues(self.user_id, state.league_season),
        )
        self._league_users = dict(
            await asyncio.gather(
                *(self._async_refresh_league_members(league) for league in leagues)
            )
        )
        self._leagues = leagues
        self._league_season = state.league_season
        self._leagues_refreshed = now
        _LOGGER.debug(
            "Loaded %d leagues for season %s", len(leagues), state.league_season
        )
        self._async_remove_stale_league_devices()

    async def _async_refresh_league_members(
        self, league: SleeperLeague
    ) -> tuple[str, tuple[SleeperLeagueUser, ...]]:
        """Reload the members and the draft of one league."""
        users, _ = await asyncio.gather(
            self.client.get_league_users(league.league_id),
            self._async_refresh_draft(league),
        )
        return league.league_id, users

    @callback
    def _async_schedule_player_refresh(self) -> None:
        """Download the player list in the background.

        The list is over 10 MB and only supplies names, positions and injury
        statuses. Waiting for it would hold up the first poll, and with it
        the entities, for no gain: the entities are written again as soon as
        it has arrived.
        """
        if self._players_refresh is not None and not self._players_refresh.done():
            return
        self._players_refresh = self.config_entry.async_create_background_task(
            self.hass, self._async_refresh_players(), f"{DOMAIN} player list"
        )

    async def _async_refresh_players(self) -> None:
        """Refresh the shared player list and rewrite the entities."""
        try:
            await self.players.async_refresh_if_stale()
        except SleeperError as err:
            # Player names are a nicety: a failed download must not fail a poll.
            _LOGGER.warning("Could not download the Sleeper player list: %s", err)
            return
        # Within the retry interval of a failed download nothing was fetched,
        # so there is nothing new to write.
        if self.players.stale:
            return
        self.async_update_listeners()

    @callback
    def _async_remove_stale_league_devices(self) -> None:
        """Remove the devices of leagues the account is no longer part of.

        Removing a device also removes its entities. An empty league list is
        left alone: it is far more likely a hiccup than the account leaving
        every league at once, and the devices vanish on the next reload.
        """
        if not self._leagues:
            return
        keep = {
            (DOMAIN, self.user_id),
            *(
                league_device_identifier(self.user_id, league.league_id)
                for league in self._leagues
            ),
        }
        device_registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(
            device_registry, self.config_entry.entry_id
        ):
            if device.identifiers and not device.identifiers & keep:
                _LOGGER.debug("Removing device of stale league %s", device.name)
                device_registry.async_remove_device(device.id)

    async def _async_refresh_draft(self, league: SleeperLeague) -> None:
        """Load the draft of a league, its trades and, once complete, its picks.

        A finished draft never changes, so once it is loaded it is kept and
        not requested again; the picks of a finished draft are fetched once.
        """
        cached = self._drafts.pop(league.league_id, None)
        if league.draft_id is None:
            return
        if (
            cached is not None
            and cached.draft_id == league.draft_id
            and cached.status == DRAFT_STATUS_COMPLETE
        ):
            self._drafts[league.league_id] = cached
            return
        try:
            draft = await self.client.get_draft(league.draft_id)
        except SleeperNotFoundError:
            _LOGGER.debug("League %s has no draft %s", league.name, league.draft_id)
            return
        self._drafts[league.league_id] = draft
        self._traded_picks[draft.draft_id] = await self.client.get_draft_traded_picks(
            draft.draft_id
        )
        if (
            draft.status == DRAFT_STATUS_COMPLETE
            and draft.draft_id not in self._draft_picks
        ):
            self._draft_picks[draft.draft_id] = await self.client.get_draft_picks(
                draft.draft_id
            )

    async def _async_fetch_draft(
        self, league: SleeperLeague
    ) -> tuple[
        SleeperDraft | None,
        tuple[SleeperDraftPick, ...],
        tuple[SleeperTradedPick, ...],
    ]:
        """Fetch the draft state of a league that is not in season yet.

        Until the draft is complete, the draft object is reloaded every poll
        to notice when it starts, pauses and finishes. Once it has started,
        the picks and trades are reloaded too; the poll in which it turns
        complete fetches the final picks a last time.
        """
        if (draft := self._drafts.get(league.league_id)) is None:
            return None, (), ()
        if draft.status != DRAFT_STATUS_COMPLETE:
            draft = await self.client.get_draft(draft.draft_id)
            self._drafts[league.league_id] = draft
            if draft.status != DRAFT_STATUS_PRE_DRAFT:
                self._draft_picks[draft.draft_id] = await self.client.get_draft_picks(
                    draft.draft_id
                )
                self._traded_picks[
                    draft.draft_id
                ] = await self.client.get_draft_traded_picks(draft.draft_id)
        return (
            draft,
            self._draft_picks.get(draft.draft_id, ()),
            self._traded_picks.get(draft.draft_id, ()),
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
        draft, draft_picks, traded_picks = await self._async_fetch_draft(league)
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
            player_lookup=self.players.get,
            draft=draft,
            draft_picks=draft_picks,
            traded_picks=traded_picks,
        )

    async def _async_add_projections(
        self,
        now: datetime,
        state: SleeperSportState,
        leagues: dict[str, SleeperLeagueData],
    ) -> dict[str, SleeperLeagueData]:
        """Attach the starters' projections and the week's games to the leagues.

        Both come from Sleeper's undocumented GraphQL API, which is not
        cached, so they are kept and only requested again when they can have
        changed; see ``_games_due`` and ``_projections_due``. One request
        covers the starters of both sides of the account's matchups in all
        leagues. When a request fails, the projections are unknown for this
        poll and it is retried on the next one; the rest of the data is good.
        """
        starters = {
            player_id
            for data in leagues.values()
            for matchup in (data.my_matchup, data.opponent_matchup)
            if matchup is not None
            for player_id in matchup.starters
            if player_id != EMPTY_SLOT_PLAYER_ID
        }
        if not starters:
            return leagues
        week = (state.season, state.season_type, state.week)
        if week != self._projections_week:
            self._projections_week = week
            self._projections = {}
            self._projections_requested = set()
            self._final_projections = set()
            self._projections_fetched = None
            self._games = None
            self._games_fetched = None
        cached = self._with_projections(leagues)
        refresh, full_refresh = self._projections_due(now, starters, cached)
        games_due = self._games_due(now, cached)
        try:
            projections, games = await asyncio.gather(
                self._async_fetch_projections(state, refresh),
                self._async_fetch_games(state, due=games_due),
            )
        except SleeperError as err:
            if not self._projections_failed:
                _LOGGER.warning("Could not load the Sleeper projections: %s", err)
                self._projections_failed = True
            return leagues
        if self._projections_failed:
            _LOGGER.info("Sleeper projections are available again")
            self._projections_failed = False
        if games is not None:
            self._games = games
            self._games_fetched = now
        if projections is not None:
            # A player missing from the answer has no projection (any more).
            for player_id in refresh:
                self._projections.pop(player_id, None)
            self._projections.update(projections)
            self._projections_requested |= refresh
            if full_refresh:
                self._projections_fetched = now
        result = self._with_projections(leagues)
        if projections is not None:
            # Sleeper keeps a projection as it is once the game has started.
            self._final_projections |= {
                player_id
                for data in result.values()
                for player_id in refresh
                if (game := data.starter_game(player_id)) is not None
                and game.status != GAME_STATUS_PRE_GAME
            }
        return result

    def _with_projections(
        self, leagues: dict[str, SleeperLeagueData]
    ) -> dict[str, SleeperLeagueData]:
        """Attach the kept projections and games to the leagues, once loaded."""
        if self._games is None:
            return leagues
        projections = MappingProxyType(dict(self._projections))
        games = MappingProxyType(
            {team: game for game in self._games for team in game.teams}
        )
        return {
            league_id: replace(data, projections=projections, games=games)
            for league_id, data in leagues.items()
        }

    def _games_due(self, now: datetime, leagues: dict[str, SleeperLeagueData]) -> bool:
        """Return whether the week's games have to be requested again.

        They are needed every poll while a game with a starter of the
        account's matchups is running, or past its kickoff and not marked as
        started yet. Otherwise only the kickoff times matter, which are known
        weeks ahead, so an hourly request follows schedule changes.
        """
        if (
            self._games_fetched is None
            or now - self._games_fetched >= GAMES_REFRESH_INTERVAL
        ):
            return True
        return any(
            game.status == GAME_STATUS_IN_GAME
            or (
                game.status == GAME_STATUS_PRE_GAME
                and game.start_time is not None
                and game.start_time <= now
            )
            for data in leagues.values()
            for game in data.matchup_games
        )

    def _projections_due(
        self,
        now: datetime,
        starters: set[str],
        leagues: dict[str, SleeperLeagueData],
    ) -> tuple[set[str], bool]:
        """Return the starters whose projections have to be requested again.

        A starter new to a line-up is requested right away. All starters
        whose game had not started at their last request are requested
        hourly (Sleeper updates projections about that often) and after a
        kickoff of one of the matchups' games, which picks up players ruled
        out shortly before. The flag tells whether it is such a full refresh.
        """
        fetched = self._projections_fetched
        full_refresh = (
            fetched is None
            or now - fetched >= PROJECTIONS_REFRESH_INTERVAL
            or any(
                game.start_time is not None and fetched < game.start_time <= now
                for data in leagues.values()
                for game in data.matchup_games
            )
        )
        if full_refresh:
            return starters - self._final_projections, True
        return starters - self._projections_requested, False

    async def _async_fetch_projections(
        self, state: SleeperSportState, player_ids: set[str]
    ) -> dict[str, SleeperProjection] | None:
        """Request the projections of some players, ``None`` if there are none."""
        if not player_ids:
            return None
        return await self.client.get_projections(
            state.season, state.season_type, state.week, sorted(player_ids)
        )

    async def _async_fetch_games(
        self, state: SleeperSportState, *, due: bool
    ) -> tuple[SleeperGame, ...] | None:
        """Request the week's games if they are due, ``None`` otherwise."""
        if not due:
            return None
        return await self.client.get_games(state.season, state.season_type, state.week)

    def _detect_points_changes(
        self, leagues: dict[str, SleeperLeagueData]
    ) -> dict[str, SleeperLeagueData]:
        """Compare starter points of the account's matchups with the last poll.

        Only the starters of both rosters in the account's own matchup are
        watched, and only changes of at least ``NOTABLE_POINTS_DELTA`` in one
        poll count, so the yardage trickle stays quiet and touchdowns, field
        goals and fumbles come through. Keys include the week, so a new week
        starts from scratch instead of reporting every player dropping to
        zero. The biggest change of a poll is remembered as the league's last
        big play until the next poll with changes.
        """
        current: dict[tuple[str, int, int, str], float] = {}
        result: dict[str, SleeperLeagueData] = {}
        for league_id, data in leagues.items():
            changes: list[SleeperPointsChange] = []
            for matchup in (data.my_matchup, data.opponent_matchup):
                if matchup is None:
                    continue
                for player_id in matchup.starters:
                    if player_id == EMPTY_SLOT_PLAYER_ID:
                        continue
                    points = matchup.players_points.get(player_id, 0.0)
                    key = (league_id, data.week, matchup.roster_id, player_id)
                    current[key] = points
                    previous = self._last_player_points.get(key)
                    if (
                        previous is None
                        or abs(points - previous) < NOTABLE_POINTS_DELTA
                    ):
                        continue
                    player = self.players.get(player_id)
                    changes.append(
                        SleeperPointsChange(
                            roster_id=matchup.roster_id,
                            player_id=player_id,
                            player=player,
                            picture=(
                                None
                                if player is None
                                else player.picture_url(self.sport)
                            ),
                            previous=previous,
                            points=points,
                            is_mine=matchup is data.my_matchup,
                        )
                    )
            if changes:
                self._last_big_play[league_id] = max(
                    changes, key=lambda change: abs(change.delta)
                )
            result[league_id] = replace(
                data,
                points_changes=tuple(changes),
                last_big_play=self._last_big_play.get(league_id),
            )
        self._last_player_points = current
        return result

    @callback
    def _async_fire_scoring_events(self, data: SleeperLeagueData) -> None:
        """Fire one bus event per notable points change of a league.

        The league device's ID is included so the events show up on the
        device's activity feed; logbook.py renders them.
        """
        if not data.points_changes:
            return
        device = dr.async_get(self.hass).async_get_device_by_identifier(
            league_device_identifier(self.user_id, data.league.league_id),
            config_entry_id=self.config_entry.entry_id,
        )
        matchup_id = data.my_matchup.matchup_id if data.my_matchup else None
        for change in data.points_changes:
            player = change.player
            self.hass.bus.async_fire(
                EVENT_PLAYER_SCORED,
                {
                    ATTR_DEVICE_ID: device.id if device else None,
                    "user_id": self.user_id,
                    "league_id": data.league.league_id,
                    "league": data.league.name,
                    "player_id": change.player_id,
                    "player": change.player_name,
                    "position": player.position if player else None,
                    "team": player.team if player else None,
                    "picture": change.picture,
                    "roster_id": change.roster_id,
                    "is_mine": change.is_mine,
                    "previous_points": change.previous,
                    "points": change.points,
                    "delta": change.delta,
                    "week": data.week,
                    "matchup_id": matchup_id,
                },
            )

    def _detect_new_picks(
        self, leagues: dict[str, SleeperLeagueData]
    ) -> dict[str, SleeperLeagueData]:
        """Single out the draft picks made since the last poll.

        The first poll that sees a draft only records where it stands, so a
        restart during a draft does not replay every pick made so far.
        """
        result: dict[str, SleeperLeagueData] = {}
        for league_id, data in leagues.items():
            if (draft := data.draft) is None:
                result[league_id] = data
                continue
            last = data.last_draft_pick
            highest = 0 if last is None else last.pick_no
            previous = self._last_pick_no.get(draft.draft_id)
            self._last_pick_no[draft.draft_id] = highest
            new_picks = (
                ()
                if previous is None
                else tuple(pick for pick in data.draft_picks if pick.pick_no > previous)
            )
            result[league_id] = replace(data, new_draft_picks=new_picks)
        return result

    @callback
    def _async_fire_draft_events(self, data: SleeperLeagueData) -> None:
        """Fire one event per new draft pick and one when the clock moves on.

        The on-the-clock event fires whenever another pick comes up, for
        every team, with ``is_mine`` telling whether it is the account's
        turn. The first poll that sees a draft fires nothing.
        """
        if (draft := data.draft) is None:
            return
        on_the_clock = data.on_the_clock
        current = None if on_the_clock is None else on_the_clock.pick_no
        previous = self._last_on_the_clock.get(draft.draft_id, current)
        first_sighting = draft.draft_id not in self._last_on_the_clock
        self._last_on_the_clock[draft.draft_id] = current
        if first_sighting or (not data.new_draft_picks and current == previous):
            return

        device = dr.async_get(self.hass).async_get_device_by_identifier(
            league_device_identifier(self.user_id, data.league.league_id),
            config_entry_id=self.config_entry.entry_id,
        )
        common = {
            ATTR_DEVICE_ID: device.id if device else None,
            "user_id": self.user_id,
            "league_id": data.league.league_id,
            "league": data.league.name,
            "draft_id": draft.draft_id,
        }
        for pick in data.new_draft_picks:
            user = data.user_for_roster_id(pick.roster_id)
            self.hass.bus.async_fire(
                EVENT_DRAFT_PICK,
                {
                    **common,
                    "pick_no": pick.pick_no,
                    "round": pick.round,
                    "pick_in_round": draft.pick_in_round(pick.pick_no),
                    "pick": f"{pick.round}.{draft.pick_in_round(pick.pick_no):02d}",
                    "roster_id": pick.roster_id,
                    "picked_by": None if user is None else user.name,
                    "picked_by_user_id": pick.picked_by,
                    "player_id": pick.player_id,
                    "player": pick.player.name,
                    "position": pick.player.position,
                    "team": pick.player.team,
                    "picture": pick.player.picture_url(self.sport),
                    "is_mine": data.pick_is_mine(pick),
                    "is_keeper": pick.is_keeper,
                },
            )
        if on_the_clock is None or current == previous:
            return
        deadline = data.pick_deadline
        self.hass.bus.async_fire(
            EVENT_DRAFT_ON_THE_CLOCK,
            {
                **common,
                "pick_no": on_the_clock.pick_no,
                "round": on_the_clock.round,
                "pick_in_round": on_the_clock.pick_in_round,
                "pick": on_the_clock.label,
                "roster_id": on_the_clock.roster_id,
                "team": on_the_clock.team_name,
                "picture": (
                    None if on_the_clock.user is None else on_the_clock.user.avatar_url
                ),
                "is_mine": on_the_clock.is_mine,
                "deadline": None if deadline is None else deadline.isoformat(),
                "picks_until_mine": data.picks_until_mine,
            },
        )

    def _update_interval_for(
        self,
        now: datetime,
        state: SleeperSportState,
        leagues: dict[str, SleeperLeagueData],
    ) -> None:
        """Pick the next update interval from the games and the observed activity.

        A running game with a starter of the account's matchups, on either
        side, or changing points in those matchups switch to the live
        interval; games without such starters and other teams' matchups do
        not count. The next kickoff of such a game pulls the poll forward.
        Otherwise a running draft polls at the draft interval, and a draft
        scheduled to start before the next poll pulls the poll forward too.
        """
        points = {
            (league_id, matchup.roster_id): matchup.points
            for league_id, data in leagues.items()
            for matchup in (data.my_matchup, data.opponent_matchup)
            if matchup is not None
        }
        picked = {
            data.draft.draft_id: data.draft.last_picked
            for data in leagues.values()
            if data.draft is not None
        }
        changed = any(
            self._last_points[key] != value
            for key, value in points.items()
            if key in self._last_points
        ) or any(
            self._last_picked[key] != value
            for key, value in picked.items()
            if key in self._last_picked
        )
        self._last_points = points
        self._last_picked = picked
        if changed:
            self._last_activity = now

        interval: timedelta
        if (
            self._last_activity is not None
            and now - self._last_activity < LIVE_GRACE_PERIOD
        ):
            interval = LIVE_UPDATE_INTERVAL
        elif any(data.draft_in_progress for data in leagues.values()):
            interval = DRAFT_UPDATE_INTERVAL
        elif state.season_type in SCORING_SEASON_TYPES:
            interval = IDLE_SEASON_UPDATE_INTERVAL
        else:
            interval = IDLE_OFFSEASON_UPDATE_INTERVAL

        for data in leagues.values():
            draft = data.draft
            if (
                draft is None
                or draft.status != DRAFT_STATUS_PRE_DRAFT
                or draft.start_time is None
            ):
                continue
            until_start = draft.start_time - now
            if until_start > timedelta(0):
                interval = min(interval, max(until_start, LIVE_UPDATE_INTERVAL))
            elif -until_start < DRAFT_START_GRACE_PERIOD:
                interval = min(interval, DRAFT_UPDATE_INTERVAL)

        interval = self._interval_for_games(now, leagues, interval)

        if interval != self.update_interval:
            _LOGGER.debug("Switching update interval to %s", interval)
            self.update_interval = interval

    @staticmethod
    def _interval_for_games(
        now: datetime, leagues: dict[str, SleeperLeagueData], interval: timedelta
    ) -> timedelta:
        """Shorten the interval for the games of the account's matchups.

        Only games in which a starter of either side plays count. A running
        one switches to the live interval, and the next kickoff pulls the
        poll forward. A game past its kickoff that Sleeper still shows as not
        started keeps the live interval for a while.
        """
        games = {
            game.game_id: game
            for data in leagues.values()
            for game in data.matchup_games
        }
        for game in games.values():
            if game.status == GAME_STATUS_IN_GAME:
                return LIVE_UPDATE_INTERVAL
            if game.status != GAME_STATUS_PRE_GAME or game.start_time is None:
                continue
            until_kickoff = game.start_time - now
            if until_kickoff > timedelta(0):
                interval = min(interval, max(until_kickoff, LIVE_UPDATE_INTERVAL))
            elif -until_kickoff < KICKOFF_GRACE_PERIOD:
                return LIVE_UPDATE_INTERVAL
        return interval
