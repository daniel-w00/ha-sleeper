"""Tests for the Sleeper coordinator."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, call

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sleeper.api import (
    SleeperConnectionError,
    SleeperDraft,
    SleeperDraftPick,
    SleeperLeague,
    SleeperLeagueUser,
    SleeperMatchup,
    SleeperNotFoundError,
    SleeperPlayer,
    SleeperRoster,
    SleeperSportState,
    SleeperTradedPick,
)
from custom_components.sleeper.const import (
    DRAFT_START_GRACE_PERIOD,
    DRAFT_UPDATE_INTERVAL,
    IDLE_OFFSEASON_UPDATE_INTERVAL,
    IDLE_SEASON_UPDATE_INTERVAL,
    LEAGUE_REFRESH_INTERVAL,
    LIVE_GRACE_PERIOD,
    LIVE_UPDATE_INTERVAL,
)
from custom_components.sleeper.coordinator import (
    SleeperCoordinator,
    SleeperLeagueData,
    SleeperStarter,
)

from .conftest import (
    DRAFT_ID,
    DRAFTING_DRAFT_ID,
    LEAGUE_ID,
    PREDRAFT_LEAGUE_ID,
    TEST_USER_ID,
    async_poll,
    draft_for,
    draft_picks_for,
    drafting_picks,
    matchups_for,
)


@pytest.fixture
def no_running_draft(mock_client: MagicMock) -> None:
    """Report every draft as complete, so no draft drives the interval."""
    mock_client.get_draft.side_effect = lambda draft_id: replace(
        draft_for(draft_id), status="complete"
    )


def _matchups_with_points(points: float) -> tuple[SleeperMatchup, ...]:
    """Return the fixture matchups with the test user's points replaced."""
    return tuple(
        replace(matchup, points=points) if matchup.roster_id == 1 else matchup
        for matchup in matchups_for(LEAGUE_ID, 1)
    )


async def test_data(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Test the first refresh assembles the data of both leagues."""
    coordinator: SleeperCoordinator = init_integration.runtime_data
    data = coordinator.data

    assert data.user.user_id == TEST_USER_ID
    assert data.state.week == 1
    assert set(data.leagues) == {LEAGUE_ID, PREDRAFT_LEAGUE_ID}

    wombats = data.leagues[LEAGUE_ID]
    assert wombats.league.name == "Wombats League"
    assert wombats.week == 1
    assert len(wombats.rosters) == 12
    assert len(wombats.users) == 12
    assert wombats.my_roster is not None
    assert wombats.my_roster.roster_id == 1
    assert wombats.rank == 1
    assert wombats.my_matchup is not None
    assert wombats.my_matchup.matchup_id == 6
    assert wombats.my_matchup.points == 71.66
    assert wombats.opponent_matchup is not None
    assert wombats.opponent_matchup.roster_id == 5
    assert wombats.opponent_matchup.points == 35.9
    assert wombats.opponent_roster is not None
    assert wombats.opponent_roster.roster_id == 5
    # The opponent has not set a team name, so the display name is used.
    assert wombats.opponent_name == "user_12"
    assert wombats.my_user is not None
    assert wombats.my_user.user_id == TEST_USER_ID
    assert wombats.opponent_user is not None
    assert wombats.opponent_user.user_id == "000000000000000012"

    predraft = data.leagues[PREDRAFT_LEAGUE_ID]
    assert predraft.league.status == "pre_draft"
    assert predraft.my_roster is not None
    assert predraft.my_roster.roster_id == 1
    assert predraft.matchups == ()
    assert predraft.my_matchup is None
    assert predraft.opponent_matchup is None
    assert predraft.opponent_roster is None
    assert predraft.opponent_name is None
    assert predraft.rank == 1

    # Matchups are only requested for leagues that are in season.
    mock_client.get_matchups.assert_awaited_once_with(LEAGUE_ID, 1)
    mock_client.get_user.assert_awaited_once_with(TEST_USER_ID)
    # The running draft of the test league keeps the draft interval.
    assert coordinator.update_interval == DRAFT_UPDATE_INTERVAL


async def test_draft_data(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test the draft data of a finished and a running draft."""
    coordinator: SleeperCoordinator = init_integration.runtime_data
    wombats = coordinator.data.leagues[LEAGUE_ID]
    predraft = coordinator.data.leagues[PREDRAFT_LEAGUE_ID]

    # The in-season league's draft is complete: only the final pick is left.
    assert wombats.draft is not None
    assert wombats.draft.status == "complete"
    assert wombats.draft_in_progress is False
    assert len(wombats.draft_picks) == 168
    assert wombats.last_draft_pick is not None
    assert wombats.last_draft_pick.player.name == "Eddy Pineiro"
    assert wombats.next_pick_no is None
    assert wombats.on_the_clock is None
    assert wombats.my_next_pick is None
    assert wombats.picks_until_mine is None
    assert wombats.pick_deadline is None

    # The other league's slow draft is running: 5 of 60 picks are made, slot
    # 3 (roster 3) is up and the account (slot 2) picks right after.
    assert predraft.draft is not None
    assert predraft.draft_in_progress is True
    assert predraft.next_pick_no == 6
    assert predraft.on_the_clock is not None
    assert predraft.on_the_clock.roster_id == 3
    assert predraft.on_the_clock.label == "2.02"
    assert predraft.on_the_clock.team_name == "user_3"
    assert predraft.on_the_clock.is_mine is False
    assert predraft.my_next_pick is not None
    assert predraft.my_next_pick.pick_no == 7
    assert predraft.my_next_pick.is_mine is True
    assert predraft.picks_until_mine == 1
    assert predraft.pick_deadline == datetime(2026, 9, 6, 18, 0, tzinfo=UTC)
    assert predraft.last_draft_pick is not None
    assert predraft.last_draft_pick.player.name == "Saquon Barkley"
    assert predraft.pick_is_mine(predraft.last_draft_pick) is False
    assert predraft.pick_is_mine(predraft.draft_picks[1]) is True


def _roster(roster_id: int, owner_id: str | None, **settings: int) -> SleeperRoster:
    """Build a roster with the given standings settings."""
    return SleeperRoster.from_json(
        {
            "roster_id": roster_id,
            "league_id": "1",
            "owner_id": owner_id,
            "settings": settings,
        }
    )


def _league_data(
    rosters: tuple[SleeperRoster, ...],
    matchups: tuple[SleeperMatchup, ...] = (),
    users: tuple[SleeperLeagueUser, ...] = (),
    my_roster_id: int | None = 1,
    draft: SleeperDraft | None = None,
    draft_picks: tuple[SleeperDraftPick, ...] = (),
    traded_picks: tuple[SleeperTradedPick, ...] = (),
) -> SleeperLeagueData:
    """Build league data around hand-made rosters."""
    return SleeperLeagueData(
        league=SleeperLeague.from_json(
            {
                "league_id": "1",
                "name": "L",
                "status": "in_season",
                "season": "2026",
                "sport": "nfl",
                "total_rosters": len(rosters),
            }
        ),
        rosters=rosters,
        users=users,
        matchups=matchups,
        week=1,
        my_roster=next(
            (roster for roster in rosters if roster.roster_id == my_roster_id), None
        ),
        draft=draft,
        draft_picks=draft_picks,
        traded_picks=traded_picks,
    )


def test_standings_tiebreak() -> None:
    """Test standings order by win percentage, then points for."""
    data = _league_data(
        (
            _roster(1, "a", wins=5, losses=5, fpts=900),
            _roster(2, "b", wins=5, losses=4, ties=1, fpts=800),
            _roster(3, "c", wins=6, losses=4, fpts=700),
            _roster(4, "d", wins=5, losses=5, fpts=950),
        )
    )

    assert [roster.roster_id for roster in data.standings] == [3, 2, 4, 1]
    assert data.rank == 4


def test_no_roster_in_league() -> None:
    """Test a league where the account owns no roster."""
    data = _league_data((_roster(1, "somebody"),), my_roster_id=None)

    assert data.my_roster is None
    assert data.my_starters == ()
    assert data.matchup_starters == ()
    assert data.starters_out == ()
    assert data.rank is None
    assert data.my_matchup is None
    assert data.opponent_matchup is None
    assert data.opponent_roster is None
    assert data.opponent_name is None
    assert data.my_user is None
    assert data.my_team_name is None
    assert data.opponent_user is None


def test_bye_week() -> None:
    """Test a roster without a matchup this week."""
    matchups = (
        SleeperMatchup.from_json({"roster_id": 1, "matchup_id": None, "points": 0}),
        SleeperMatchup.from_json({"roster_id": 2, "matchup_id": 1, "points": 10}),
    )
    data = _league_data((_roster(1, "a"), _roster(2, "b")), matchups)

    assert data.my_matchup is None
    assert data.opponent_matchup is None


def test_opponent_without_owner() -> None:
    """Test opponent lookups when data is inconsistent or the roster is empty."""
    matchups = (
        SleeperMatchup.from_json({"roster_id": 1, "matchup_id": 1, "points": 0}),
        SleeperMatchup.from_json({"roster_id": 2, "matchup_id": 1, "points": 0}),
    )
    users = (SleeperLeagueUser.from_json({"user_id": "a", "display_name": "A"}),)

    # Opponent roster is not owned by anyone.
    data = _league_data((_roster(1, "a"), _roster(2, None)), matchups, users)
    assert data.opponent_roster is not None
    assert data.opponent_name is None
    assert data.opponent_user is None
    assert data.my_user is not None

    # Opponent roster is owned by a user missing from the member list.
    data = _league_data((_roster(1, "a"), _roster(2, "ghost")), matchups, users)
    assert data.opponent_name is None

    # Opponent has a matchup entry but no roster (should not happen).
    data = _league_data((_roster(1, "a"),), matchups, users)
    assert data.opponent_matchup is not None
    assert data.opponent_roster is None
    assert data.opponent_name is None

    # Opponent without a matchup entry at all.
    data = _league_data((_roster(1, "a"), _roster(2, "b")), matchups[:1], users)
    assert data.opponent_matchup is None


@pytest.mark.usefixtures("no_running_draft")
async def test_adaptive_interval(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the interval follows the observed scoring activity."""
    coordinator: SleeperCoordinator = init_integration.runtime_data
    assert coordinator.update_interval == IDLE_SEASON_UPDATE_INTERVAL

    # Nothing changed: stay idle.
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    assert coordinator.update_interval == IDLE_SEASON_UPDATE_INTERVAL

    # Points changed: switch to live.
    mock_client.get_matchups.side_effect = lambda league_id, week: (
        _matchups_with_points(80.0) if league_id == LEAGUE_ID else ()
    )
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    assert coordinator.update_interval == LIVE_UPDATE_INTERVAL
    my_matchup = coordinator.data.leagues[LEAGUE_ID].my_matchup
    assert my_matchup is not None
    assert my_matchup.points == 80.0

    # Unchanged but within the grace period: stay live.
    await async_poll(hass, freezer, LIVE_UPDATE_INTERVAL)
    assert coordinator.update_interval == LIVE_UPDATE_INTERVAL

    # Another change resets the grace period.
    mock_client.get_matchups.side_effect = lambda league_id, week: (
        _matchups_with_points(85.0) if league_id == LEAGUE_ID else ()
    )
    await async_poll(hass, freezer, LIVE_UPDATE_INTERVAL)
    assert coordinator.update_interval == LIVE_UPDATE_INTERVAL

    # Grace period over without changes: back to idle.
    await async_poll(hass, freezer, LIVE_GRACE_PERIOD)
    assert coordinator.update_interval == IDLE_SEASON_UPDATE_INTERVAL


@pytest.mark.usefixtures("no_running_draft")
async def test_offseason_interval(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_sport_state: SleeperSportState,
) -> None:
    """Test the long idle interval and no matchup requests in the off-season."""
    mock_client.get_sport_state.return_value = replace(
        mock_sport_state, season_type="off"
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: SleeperCoordinator = mock_config_entry.runtime_data
    assert coordinator.update_interval == IDLE_OFFSEASON_UPDATE_INTERVAL
    mock_client.get_matchups.assert_not_awaited()
    assert coordinator.data.leagues[LEAGUE_ID].matchups == ()


async def test_league_refresh(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    mock_leagues: tuple[SleeperLeague, ...],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the league list is reloaded hourly and on a new league season."""
    coordinator: SleeperCoordinator = init_integration.runtime_data
    sport_state = coordinator.data.state
    assert mock_client.get_user_leagues.await_count == 1
    assert mock_client.get_league_users.await_count == 2

    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    assert mock_client.get_user_leagues.await_count == 1

    # A league disappears; the hourly refresh picks it up.
    mock_client.get_user_leagues.return_value = mock_leagues[1:]
    await async_poll(hass, freezer, LEAGUE_REFRESH_INTERVAL)
    assert mock_client.get_user_leagues.await_count == 2
    assert set(coordinator.data.leagues) == {LEAGUE_ID}

    # Sleeper moves to the next league season: refresh immediately.
    mock_client.get_sport_state.return_value = replace(
        sport_state, league_season="2027"
    )
    mock_client.get_user_leagues.return_value = mock_leagues
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    assert mock_client.get_user_leagues.await_count == 3
    mock_client.get_user_leagues.assert_awaited_with(TEST_USER_ID, "2027")
    assert set(coordinator.data.leagues) == {LEAGUE_ID, PREDRAFT_LEAGUE_ID}


async def test_update_failed_and_recovery(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test an API error marks the entry unavailable until the next success."""
    coordinator: SleeperCoordinator = init_integration.runtime_data

    mock_client.get_rosters.side_effect = SleeperConnectionError("down")
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    assert coordinator.last_update_success is False
    week = hass.states.get("sensor.test_user_nfl_week")
    assert week is not None
    assert week.state == "unavailable"

    mock_client.get_rosters.side_effect = None
    mock_client.get_rosters.return_value = ()
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    assert coordinator.last_update_success is True
    week = hass.states.get("sensor.test_user_nfl_week")
    assert week is not None
    assert week.state == "1"
    assert coordinator.data.leagues[LEAGUE_ID].my_roster is None


def test_starters_without_player_list() -> None:
    """Test starters fall back to IDs when no player list is available."""
    roster = SleeperRoster.from_json(
        {"roster_id": 1, "league_id": "1", "owner_id": "a", "starters": ["7", "0"]}
    )
    data = _league_data((roster,))

    assert [starter.name for starter in data.my_starters] == ["7", "0"]
    # Slots beyond the league's roster positions are unknown.
    assert [starter.slot for starter in data.my_starters] == ["?", "?"]
    assert [starter.status for starter in data.starters_out] == ["Empty"]


@pytest.mark.parametrize(
    ("player", "status"),
    [
        (None, None),
        ({"status": "Active"}, None),
        ({"status": "Active", "injury_status": "Questionable"}, None),
        ({"status": "Active", "injury_status": "Doubtful"}, "Doubtful"),
        ({"status": "Inactive"}, "Inactive"),
    ],
)
def test_starter_status(player: dict[str, str] | None, status: str | None) -> None:
    """Test which player states mark a starter as out."""
    starter = SleeperStarter(
        slot="QB",
        player_id="7",
        player=None
        if player is None
        else SleeperPlayer.from_json({"player_id": "7", **player}),
    )

    assert starter.status == status


def _draft(
    status: str = "drafting",
    draft_type: str = "snake",
    *,
    rounds: int = 3,
    slot_map: bool = True,
    order: bool = True,
    **extra: object,
) -> SleeperDraft:
    """Build a 4-team draft; the account (roster 1) has slot 2."""
    slots = {"1": 2, "2": 1, "3": 3, "4": 4}
    return SleeperDraft.from_json(
        {
            "draft_id": "d",
            "league_id": "1",
            "status": status,
            "type": draft_type,
            "season": "2026",
            "settings": {"rounds": rounds, "teams": 4, "pick_timer": 3600},
            "draft_order": {str(roster): slot for roster, slot in slots.items()}
            if order
            else None,
            "slot_to_roster_id": {
                str(slot): int(roster) for roster, slot in slots.items()
            }
            if slot_map
            else None,
            **extra,
        }
    )


def _pick(
    pick_no: int, roster_id: int | None, picked_by: str | None = None
) -> SleeperDraftPick:
    """Build a pick in the 4-team snake draft."""
    return SleeperDraftPick.from_json(
        {
            "draft_id": "d",
            "pick_no": pick_no,
            "round": (pick_no - 1) // 4 + 1,
            "draft_slot": 0,
            "roster_id": roster_id,
            "picked_by": picked_by,
            "player_id": str(1000 + pick_no),
        }
    )


def _draft_data(
    draft: SleeperDraft | None,
    picks_made: int = 5,
    traded: tuple[SleeperTradedPick, ...] = (),
    my_roster_id: int | None = 1,
) -> SleeperLeagueData:
    """Build league data of a 4-team league with rosters 1-4 owned by users 1-4."""
    return _league_data(
        tuple(_roster(roster_id, str(roster_id)) for roster_id in range(1, 5)),
        users=tuple(
            SleeperLeagueUser.from_json(
                {"user_id": str(user_id), "display_name": f"user_{user_id}"}
            )
            for user_id in range(1, 5)
        ),
        my_roster_id=my_roster_id,
        draft=draft,
        draft_picks=tuple(_pick(pick_no, None) for pick_no in range(1, picks_made + 1)),
        traded_picks=traded,
    )


def test_pick_order_with_trades() -> None:
    """Test whose pick is up and which is mine, honouring traded picks."""
    # The account's third round pick (slot 2, pick 10) went to roster 3.
    trade = SleeperTradedPick.from_json(
        {"season": "2026", "round": 3, "roster_id": 1, "owner_id": 3}
    )
    data = _draft_data(_draft(), picks_made=5, traded=(trade,))
    assert data.next_pick_no == 6
    assert data.on_the_clock is not None
    assert data.on_the_clock.roster_id == 3
    assert data.on_the_clock.team_name == "user_3"
    assert data.on_the_clock.label == "2.02"
    assert data.on_the_clock.is_mine is False
    assert data.my_next_pick is not None
    assert data.my_next_pick.pick_no == 7
    assert data.picks_until_mine == 1
    assert data.pick_owner(10) == 3

    # After pick 8 the account has no pick left: its last one was traded.
    data = _draft_data(_draft(), picks_made=8, traded=(trade,))
    assert data.on_the_clock is not None
    assert data.on_the_clock.roster_id == 2
    assert data.on_the_clock.label == "3.01"
    assert data.my_next_pick is None
    assert data.picks_until_mine is None

    # All picks made: nothing is up, even before Sleeper marks it complete.
    data = _draft_data(_draft(), picks_made=12)
    assert data.next_pick_no is None
    assert data.on_the_clock is None

    # A paused draft keeps the pick on the clock but has no deadline.
    data = _draft_data(_draft("paused"))
    assert data.draft_in_progress is True
    assert data.on_the_clock is not None
    assert data.pick_deadline is None

    # Complete and not started: nothing is up.
    assert _draft_data(_draft("complete")).on_the_clock is None
    assert _draft_data(_draft("pre_draft")).on_the_clock is None
    assert _draft_data(_draft("pre_draft")).draft_in_progress is False
    # Auction drafts have no order, but the last pick is known.
    data = _draft_data(_draft(draft_type="auction"))
    assert data.next_pick_no is None
    assert data.last_draft_pick is not None
    assert data.last_draft_pick.pick_no == 5
    # A linear draft repeats the order every round.
    data = _draft_data(_draft(draft_type="linear"))
    assert data.on_the_clock is not None
    assert data.on_the_clock.roster_id == 1
    assert data.on_the_clock.is_mine is True
    assert data.picks_until_mine == 0


def test_pick_order_without_slot_map() -> None:
    """Test the draft order and roster owners stand in for a missing slot map."""
    data = _draft_data(_draft(slot_map=False))
    assert data.pick_owner(6) == 3
    assert data.on_the_clock is not None
    assert data.on_the_clock.is_mine is False
    assert data.picks_until_mine == 1

    # Without any order the pick is up, but nobody knows whose it is.
    data = _draft_data(_draft(slot_map=False, order=False))
    assert data.on_the_clock is not None
    assert data.on_the_clock.roster_id is None
    assert data.on_the_clock.team_name is None
    assert data.on_the_clock.is_mine is False
    assert data.my_next_pick is None

    # Without an own roster nothing is mine.
    data = _draft_data(_draft(), my_roster_id=None)
    assert data.on_the_clock is not None
    assert data.my_next_pick is None
    assert data.pick_is_mine(_pick(1, 1)) is False

    # Without a draft there is nothing at all.
    data = _draft_data(None, picks_made=0)
    assert data.on_the_clock is None
    assert data.pick_owner(1) is None
    assert data.pick_deadline is None
    assert data.last_draft_pick is None


def test_pick_deadline() -> None:
    """Test the deadline follows the last pick, the start, and the pauses."""
    start = datetime(2026, 9, 5, 18, 0, tzinfo=UTC)
    start_ms = int(start.timestamp() * 1000)
    last = start + timedelta(hours=5)
    last_ms = int(last.timestamp() * 1000)
    assert _draft_data(
        _draft(start_time=start_ms, last_picked=last_ms)
    ).pick_deadline == last + timedelta(hours=1)
    # Before the first pick the timer runs from the start.
    assert _draft_data(
        _draft(start_time=start_ms), picks_made=0
    ).pick_deadline == start + timedelta(hours=1)
    assert _draft_data(_draft(), picks_made=0).pick_deadline is None
    # Overnight autopause and drafts without a timer have no deadline.
    assert (
        _draft_data(
            _draft(last_picked=last_ms, metadata={"is_autopaused": "true"})
        ).pick_deadline
        is None
    )
    no_timer = replace(_draft(last_picked=last_ms), pick_timer=timedelta(0))
    assert _draft_data(no_timer).pick_deadline is None


def test_pick_is_mine_by_picker() -> None:
    """Test a pick without roster falls back to who made it."""
    data = _draft_data(_draft())
    assert data.pick_is_mine(_pick(1, None, "1")) is True
    assert data.pick_is_mine(_pick(1, None, "2")) is False
    assert data.pick_is_mine(_pick(1, 2, "1")) is False


async def test_draft_requests(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test which draft data is requested when.

    Both drafts and their trades load with the league list, the finished
    draft's picks once. The running draft is reloaded every poll with its
    picks and trades.
    """
    assert mock_client.get_draft.await_args_list == [
        call(DRAFTING_DRAFT_ID),
        call(DRAFT_ID),
        call(DRAFTING_DRAFT_ID),
    ]
    assert mock_client.get_draft_picks.await_args_list == [
        call(DRAFT_ID),
        call(DRAFTING_DRAFT_ID),
    ]
    assert mock_client.get_draft_traded_picks.await_args_list == [
        call(DRAFTING_DRAFT_ID),
        call(DRAFT_ID),
        call(DRAFTING_DRAFT_ID),
    ]

    mock_client.reset_mock()
    await async_poll(hass, freezer, DRAFT_UPDATE_INTERVAL)
    assert mock_client.get_draft.await_args_list == [call(DRAFTING_DRAFT_ID)]
    assert mock_client.get_draft_picks.await_args_list == [call(DRAFTING_DRAFT_ID)]
    assert mock_client.get_draft_traded_picks.await_args_list == [
        call(DRAFTING_DRAFT_ID)
    ]

    # The hourly refresh reloads the running draft and its trades; the
    # finished draft is not requested again.
    mock_client.reset_mock()
    await async_poll(hass, freezer, LEAGUE_REFRESH_INTERVAL)
    assert mock_client.get_draft.await_args_list == [
        call(DRAFTING_DRAFT_ID),
        call(DRAFTING_DRAFT_ID),
    ]
    assert mock_client.get_draft_picks.await_args_list == [call(DRAFTING_DRAFT_ID)]
    assert mock_client.get_draft_traded_picks.await_args_list == [
        call(DRAFTING_DRAFT_ID),
        call(DRAFTING_DRAFT_ID),
    ]


async def test_league_without_draft(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_leagues: tuple[SleeperLeague, ...],
) -> None:
    """Test leagues without a draft ID or with a draft Sleeper does not know."""
    mock_client.get_user_leagues.return_value = (
        replace(mock_leagues[0], draft_id=None),
        mock_leagues[1],
    )
    mock_client.get_draft.side_effect = SleeperNotFoundError("gone")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: SleeperCoordinator = mock_config_entry.runtime_data
    for data in coordinator.data.leagues.values():
        assert data.draft is None
        assert data.draft_picks == ()
        assert data.on_the_clock is None
    mock_client.get_draft.assert_awaited_once_with(DRAFT_ID)
    mock_client.get_draft_picks.assert_not_awaited()
    assert coordinator.update_interval == IDLE_SEASON_UPDATE_INTERVAL


async def test_draft_interval(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a running draft polls at the draft interval, live after a pick."""
    coordinator: SleeperCoordinator = init_integration.runtime_data
    assert coordinator.update_interval == DRAFT_UPDATE_INTERVAL

    # Nothing happened: stay at the draft interval.
    await async_poll(hass, freezer, DRAFT_UPDATE_INTERVAL)
    assert coordinator.update_interval == DRAFT_UPDATE_INTERVAL

    # A pick was made: live.
    draft = draft_for(DRAFTING_DRAFT_ID)
    assert draft.last_picked is not None
    picked = replace(draft, last_picked=draft.last_picked + timedelta(hours=1))
    mock_client.get_draft.side_effect = lambda draft_id: (
        picked if draft_id == DRAFTING_DRAFT_ID else draft_for(draft_id)
    )
    mock_client.get_draft_picks.side_effect = lambda draft_id: (
        drafting_picks(6)
        if draft_id == DRAFTING_DRAFT_ID
        else draft_picks_for(draft_id)
    )
    await async_poll(hass, freezer, DRAFT_UPDATE_INTERVAL)
    assert coordinator.update_interval == LIVE_UPDATE_INTERVAL
    assert coordinator.data.leagues[PREDRAFT_LEAGUE_ID].next_pick_no == 7

    # Quiet for the grace period: back to the draft interval.
    await async_poll(hass, freezer, LIVE_GRACE_PERIOD)
    assert coordinator.update_interval == DRAFT_UPDATE_INTERVAL

    # The draft finished: the final picks are fetched once more, then the
    # draft is left alone and the season interval applies.
    mock_client.get_draft.side_effect = lambda draft_id: replace(
        picked if draft_id == DRAFTING_DRAFT_ID else draft_for(draft_id),
        status="complete",
    )
    mock_client.reset_mock()
    await async_poll(hass, freezer, DRAFT_UPDATE_INTERVAL)
    assert coordinator.update_interval == IDLE_SEASON_UPDATE_INTERVAL
    assert mock_client.get_draft_picks.await_args_list == [call(DRAFTING_DRAFT_ID)]
    assert coordinator.data.leagues[PREDRAFT_LEAGUE_ID].draft_in_progress is False
    # This poll coincides with the hourly league refresh, which leaves the
    # finished drafts alone.
    mock_client.reset_mock()
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    mock_client.get_draft.assert_not_awaited()
    mock_client.get_draft_picks.assert_not_awaited()
    mock_client.get_draft_traded_picks.assert_not_awaited()


async def test_scheduled_draft_interval(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a scheduled draft pulls the next poll forward to its start."""
    draft = replace(draft_for(DRAFTING_DRAFT_ID), status="pre_draft", last_picked=None)
    assert draft.start_time is not None
    mock_client.get_draft.side_effect = lambda draft_id: (
        draft if draft_id == DRAFTING_DRAFT_ID else draft_for(draft_id)
    )
    freezer.move_to(draft.start_time - timedelta(minutes=10))
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: SleeperCoordinator = mock_config_entry.runtime_data
    assert coordinator.update_interval == timedelta(minutes=10)
    # No picks are requested before the draft starts.
    assert mock_client.get_draft_picks.await_args_list == [call(DRAFT_ID)]

    # The start time is here but the commissioner has not started it yet.
    await async_poll(hass, freezer, timedelta(minutes=10))
    assert coordinator.update_interval == DRAFT_UPDATE_INTERVAL

    # Rescheduled to shortly after the next poll: the live interval is the
    # floor.
    rescheduled = replace(
        draft,
        start_time=dt_util.utcnow() + DRAFT_UPDATE_INTERVAL + timedelta(seconds=30),
    )
    mock_client.get_draft.side_effect = lambda draft_id: (
        rescheduled if draft_id == DRAFTING_DRAFT_ID else draft_for(draft_id)
    )
    await async_poll(hass, freezer, DRAFT_UPDATE_INTERVAL)
    assert coordinator.update_interval == LIVE_UPDATE_INTERVAL
    await async_poll(hass, freezer, LIVE_UPDATE_INTERVAL)
    assert coordinator.update_interval == DRAFT_UPDATE_INTERVAL

    # Long after the scheduled start: give up, idle interval.
    await async_poll(hass, freezer, DRAFT_START_GRACE_PERIOD)
    assert coordinator.update_interval == IDLE_SEASON_UPDATE_INTERVAL
