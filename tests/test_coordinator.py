"""Tests for the Sleeper coordinator."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sleeper.api import (
    SleeperConnectionError,
    SleeperLeague,
    SleeperLeagueUser,
    SleeperMatchup,
    SleeperPlayer,
    SleeperRoster,
    SleeperSportState,
)
from custom_components.sleeper.const import (
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
    LEAGUE_ID,
    PREDRAFT_LEAGUE_ID,
    TEST_USER_ID,
    async_poll,
    matchups_for,
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
    assert coordinator.update_interval == IDLE_SEASON_UPDATE_INTERVAL


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
    assert data.starters_out == ()
    assert data.rank is None
    assert data.my_matchup is None
    assert data.opponent_matchup is None
    assert data.opponent_roster is None
    assert data.opponent_name is None


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
