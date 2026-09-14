"""Tests for the Sleeper sensors."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.sleeper.api import SleeperLeague, SleeperNotFoundError
from custom_components.sleeper.const import (
    DRAFT_UPDATE_INTERVAL,
    IDLE_SEASON_UPDATE_INTERVAL,
    LEAGUE_REFRESH_INTERVAL,
)

from .conftest import (
    DRAFTING_DRAFT_ID,
    LEAGUE_ID,
    TEST_USER_ID,
    async_poll,
    draft_for,
    draft_picks_for,
    drafting_picks,
    matchups_with_points,
    rosters_for,
)


@pytest.fixture(autouse=True)
def platforms() -> Generator[None]:
    """Load only the sensor platform."""
    with patch("custom_components.sleeper.PLATFORMS", [Platform.SENSOR]):
        yield


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_sensors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test all sensors against the snapshot."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


async def test_sensor_values(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test the key values of both leagues."""
    for entity_id, state in (
        ("sensor.test_user_nfl_week", "1"),
        ("sensor.test_user_nfl_season", "2026"),
        ("sensor.test_user_nfl_season_type", "regular"),
        ("sensor.wombats_league_league_status", "in_season"),
        ("sensor.wombats_league_team", "Test Team"),
        ("sensor.wombats_league_record", "0-0"),
        ("sensor.wombats_league_rank", "1"),
        ("sensor.wombats_league_points_for", "0.0"),
        ("sensor.wombats_league_points_against", "0.0"),
        ("sensor.wombats_league_matchup_points", "71.66"),
        ("sensor.wombats_league_starters_out", "2"),
        ("sensor.wombats_league_opponent_points", "35.9"),
        ("sensor.wombats_league_opponent", "user_12"),
        ("sensor.wombats_league_last_big_play", STATE_UNKNOWN),
        ("sensor.wombats_league_waiver_position", "6"),
        ("sensor.wombats_league_waiver_budget_remaining", "100"),
        ("sensor.wombats_league_draft_start", "2026-08-24T13:20:26+00:00"),
        ("sensor.wombats_league_on_the_clock", STATE_UNKNOWN),
        ("sensor.wombats_league_my_next_pick", STATE_UNKNOWN),
        ("sensor.wombats_league_last_pick", "Eddy Pineiro"),
        ("sensor.test_league_league_status", "pre_draft"),
        ("sensor.test_league_draft_start", "2026-09-05T18:00:00+00:00"),
        ("sensor.test_league_on_the_clock", "user_3"),
        ("sensor.test_league_my_next_pick", "1"),
        ("sensor.test_league_last_pick", "Saquon Barkley"),
        ("sensor.test_league_record", "0-0"),
        ("sensor.test_league_matchup_points", STATE_UNKNOWN),
        ("sensor.test_league_starters_out", "10"),
        ("sensor.test_league_opponent_points", STATE_UNKNOWN),
        ("sensor.test_league_opponent", STATE_UNKNOWN),
    ):
        entity_state = hass.states.get(entity_id)
        assert entity_state is not None, entity_id
        assert entity_state.state == state, entity_id

    matchup = hass.states.get("sensor.wombats_league_matchup_points")
    assert matchup is not None
    assert matchup.attributes["week"] == 1
    assert matchup.attributes["matchup_id"] == 6
    assert matchup.attributes["starters"][0] == {
        "slot": "QB",
        "player": "Caleb Williams",
        "player_id": "11560",
        "points": 17.66,
    }
    assert matchup.attributes["starters"][-1]["player"] == "Pittsburgh Steelers"
    assert matchup.attributes["starters"][-1]["slot"] == "DEF"

    # Two starters are out; the questionable RB is not counted.
    out = hass.states.get("sensor.wombats_league_starters_out")
    assert out is not None
    assert out.attributes["starters"] == [
        {"slot": "WR", "player": "Zay Flowers", "status": "Out"},
        {"slot": "TE", "player": "Travis Kelce", "status": "Out"},
    ]
    out = hass.states.get("sensor.test_league_starters_out")
    assert out is not None
    assert out.attributes["starters"][0] == {
        "slot": "QB",
        "player": "0",
        "status": "Empty",
    }

    opponent = hass.states.get("sensor.wombats_league_opponent")
    assert opponent is not None
    assert opponent.attributes["opponent_roster_id"] == 5
    assert opponent.attributes["opponent_record"] == "0-0"

    # Team pictures replace the icon only where the state names that team.
    opponent_avatar = "https://sleepercdn.com/avatars/thumbs/opponentavatar"
    for entity_id, picture in (
        ("sensor.wombats_league_team", "https://sleepercdn.com/uploads/testteam.jpg"),
        ("sensor.wombats_league_opponent_points", opponent_avatar),
        ("sensor.wombats_league_opponent", opponent_avatar),
        ("sensor.wombats_league_league_status", None),
        ("sensor.wombats_league_matchup_points", None),
        ("sensor.wombats_league_record", None),
        ("sensor.wombats_league_last_big_play", None),
        ("sensor.wombats_league_on_the_clock", None),
        (
            "sensor.wombats_league_last_pick",
            "https://sleepercdn.com/content/nfl/players/thumb/5189.jpg",
        ),
        ("sensor.test_league_opponent", None),
        # user_3 has no picture of any kind.
        ("sensor.test_league_on_the_clock", None),
        (
            "sensor.test_league_last_pick",
            "https://sleepercdn.com/content/nfl/players/thumb/4866.jpg",
        ),
    ):
        entity_state = hass.states.get(entity_id)
        assert entity_state is not None, entity_id
        assert entity_state.attributes.get("entity_picture") == picture, entity_id

    # Pre-draft league: no matchup details, no FAAB entity.
    matchup = hass.states.get("sensor.test_league_matchup_points")
    assert matchup is not None
    assert "week" not in matchup.attributes
    assert hass.states.get("sensor.test_league_waiver_budget_remaining") is None

    record = entity_registry.async_get("sensor.wombats_league_record")
    assert record is not None
    assert record.unique_id == f"{TEST_USER_ID}_{LEAGUE_ID}_record"


async def test_draft_sensor_values(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test the attributes of the draft sensors of both leagues."""
    # Its draft is running: 5 of 60 picks made, roster 3 is up, mine is next.
    draft_start = hass.states.get("sensor.test_league_draft_start")
    assert draft_start is not None
    assert draft_start.attributes["draft_id"] == DRAFTING_DRAFT_ID
    assert draft_start.attributes["status"] == "drafting"
    assert draft_start.attributes["type"] == "snake"
    assert draft_start.attributes["rounds"] == 15
    assert draft_start.attributes["teams"] == 4
    assert draft_start.attributes["pick_timer"] == 28800
    assert draft_start.attributes["my_slot"] == 2
    on_the_clock = hass.states.get("sensor.test_league_on_the_clock")
    assert on_the_clock is not None
    assert on_the_clock.attributes["pick_no"] == 6
    assert on_the_clock.attributes["round"] == 2
    assert on_the_clock.attributes["pick_in_round"] == 2
    assert on_the_clock.attributes["pick"] == "2.02"
    assert on_the_clock.attributes["roster_id"] == 3
    assert on_the_clock.attributes["is_mine"] is False
    assert on_the_clock.attributes["deadline"] == "2026-09-06T18:00:00+00:00"
    assert on_the_clock.attributes["paused"] is False
    assert on_the_clock.attributes["picks_made"] == 5
    assert on_the_clock.attributes["picks_total"] == 60
    my_next_pick = hass.states.get("sensor.test_league_my_next_pick")
    assert my_next_pick is not None
    assert my_next_pick.attributes["unit_of_measurement"] == "picks"
    assert my_next_pick.attributes["pick"] == "2.03"
    assert my_next_pick.attributes["pick_no"] == 7
    last_pick = hass.states.get("sensor.test_league_last_pick")
    assert last_pick is not None
    assert last_pick.attributes["pick"] == "2.01"
    assert last_pick.attributes["pick_no"] == 5
    assert last_pick.attributes["picked_by"] == "user_4"
    assert last_pick.attributes["roster_id"] == 4
    assert last_pick.attributes["player_id"] == "4866"
    assert last_pick.attributes["position"] == "RB"
    assert last_pick.attributes["is_mine"] is False
    assert last_pick.attributes["is_keeper"] is False
    # The finished draft keeps its settings and final pick, nothing is up.
    on_the_clock = hass.states.get("sensor.wombats_league_on_the_clock")
    assert on_the_clock is not None
    assert "pick_no" not in on_the_clock.attributes
    my_next_pick = hass.states.get("sensor.wombats_league_my_next_pick")
    assert my_next_pick is not None
    assert "pick_no" not in my_next_pick.attributes
    draft_start = hass.states.get("sensor.wombats_league_draft_start")
    assert draft_start is not None
    assert draft_start.attributes["status"] == "complete"
    assert draft_start.attributes["my_slot"] == 7


async def test_last_big_play(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the last big play sensor follows the biggest change of a poll."""
    entity_id = "sensor.wombats_league_last_big_play"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert "player_id" not in state.attributes

    # Baseline poll, then my QB scores a touchdown and the opponent's kicker
    # loses points: the touchdown is the bigger change.
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    mock_client.get_matchups.side_effect = lambda league_id, week: matchups_with_points(
        {(1, "11560"): 24.0, (5, "11792"): -3.0}
    )
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "Caleb Williams"
    assert state.attributes["entity_picture"] == (
        "https://sleepercdn.com/content/nfl/players/thumb/11560.jpg"
    )
    assert state.attributes["player_id"] == "11560"
    assert state.attributes["position"] == "QB"
    assert state.attributes["team"] == "CHI"
    assert state.attributes["is_mine"] is True
    assert state.attributes["previous_points"] == 17.66
    assert state.attributes["points"] == 24.0
    assert state.attributes["delta"] == 6.34
    assert state.attributes["week"] == 1

    # A quiet poll keeps the last play.
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "Caleb Williams"

    # My defense scores more than the opponent's kicker: the defense wins and
    # shows the team logo instead of a headshot.
    mock_client.get_matchups.side_effect = lambda league_id, week: matchups_with_points(
        {(1, "11560"): 24.0, (1, "PIT"): 20.0, (5, "11792"): 3.0}
    )
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "Pittsburgh Steelers"
    assert state.attributes["position"] == "DEF"
    assert state.attributes["entity_picture"] == (
        "https://sleepercdn.com/images/team_logos/nfl/pit.png"
    )


async def test_sensors_without_own_roster(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test roster-based sensors become unknown when the account has no team."""
    mock_client.get_rosters.side_effect = None
    mock_client.get_rosters.return_value = ()
    await async_poll(hass, freezer, LEAGUE_REFRESH_INTERVAL)

    for entity_id in (
        "sensor.wombats_league_record",
        "sensor.wombats_league_rank",
        "sensor.wombats_league_points_for",
        "sensor.wombats_league_starters_out",
        "sensor.wombats_league_waiver_budget_remaining",
    ):
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.state == STATE_UNKNOWN, entity_id
    record = hass.states.get("sensor.wombats_league_record")
    assert record is not None
    assert "wins" not in record.attributes
    budget = hass.states.get("sensor.wombats_league_waiver_budget_remaining")
    assert budget is not None
    assert "budget" not in budget.attributes
    out = hass.states.get("sensor.wombats_league_starters_out")
    assert out is not None
    assert "starters" not in out.attributes


async def test_roster_lineup_differs_from_matchup(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the matchup shows the locked line-up while starters out uses the roster.

    Sleeper lets you set next week's line-up while this week is still being
    played; the roster then carries the new line-up.
    """
    rosters = list(rosters_for(LEAGUE_ID))
    starters = list(rosters[0].starters)
    starters[0] = "11581"  # bench RB (inactive) moved into the QB slot
    rosters[0] = replace(rosters[0], starters=tuple(starters))
    mock_client.get_rosters.side_effect = lambda league_id: (
        tuple(rosters) if league_id == LEAGUE_ID else rosters_for(league_id)
    )
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)

    matchup = hass.states.get("sensor.wombats_league_matchup_points")
    assert matchup is not None
    assert matchup.attributes["starters"][0]["player"] == "Caleb Williams"

    out = hass.states.get("sensor.wombats_league_starters_out")
    assert out is not None
    assert out.state == "3"
    assert out.attributes["starters"][0] == {
        "slot": "QB",
        "player": "MarShawn Lloyd",
        "status": "Inactive",
    }


async def test_draft_sensors_follow_the_picks(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the draft sensors move on as picks are made and the draft ends."""
    mock_client.get_draft_picks.side_effect = lambda draft_id: (
        drafting_picks(6)
        if draft_id == DRAFTING_DRAFT_ID
        else draft_picks_for(draft_id)
    )
    await async_poll(hass, freezer, DRAFT_UPDATE_INTERVAL)

    # Roster 3 picked: the account is on the clock.
    state = hass.states.get("sensor.test_league_on_the_clock")
    assert state is not None
    assert state.state == "Test Team"
    assert state.attributes["is_mine"] is True
    assert state.attributes["pick"] == "2.03"
    assert state.attributes["entity_picture"] == (
        "https://sleepercdn.com/uploads/testteam.jpg"
    )
    state = hass.states.get("sensor.test_league_my_next_pick")
    assert state is not None
    assert state.state == "0"
    state = hass.states.get("sensor.test_league_last_pick")
    assert state is not None
    assert state.state == "Harold Fannin"
    assert state.attributes["picked_by"] == "user_3"

    # The draft is over: nothing is up, the last pick stays.
    mock_client.get_draft.side_effect = lambda draft_id: replace(
        draft_for(draft_id), status="complete"
    )
    mock_client.get_draft_picks.side_effect = lambda draft_id: (
        drafting_picks(60)
        if draft_id == DRAFTING_DRAFT_ID
        else draft_picks_for(draft_id)
    )
    await async_poll(hass, freezer, DRAFT_UPDATE_INTERVAL)
    state = hass.states.get("sensor.test_league_on_the_clock")
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert "entity_picture" not in state.attributes
    state = hass.states.get("sensor.test_league_my_next_pick")
    assert state is not None
    assert state.state == STATE_UNKNOWN
    state = hass.states.get("sensor.test_league_last_pick")
    assert state is not None
    assert state.attributes["pick"] == "15.04"
    state = hass.states.get("sensor.test_league_draft_start")
    assert state is not None
    assert state.attributes["status"] == "complete"


async def test_draft_sensors_without_draft(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_leagues: tuple[SleeperLeague, ...],
) -> None:
    """Test a league without a draft gets no draft sensors."""
    mock_client.get_user_leagues.return_value = (
        replace(mock_leagues[0], draft_id=None),
        mock_leagues[1],
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.test_league_draft_start") is None
    assert hass.states.get("sensor.test_league_on_the_clock") is None
    assert hass.states.get("sensor.test_league_league_status") is not None
    assert hass.states.get("sensor.wombats_league_draft_start") is not None


async def test_draft_sensors_when_draft_vanishes(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the draft sensors go unknown when Sleeper drops the draft."""
    mock_client.get_draft.side_effect = SleeperNotFoundError("gone")
    await async_poll(hass, freezer, LEAGUE_REFRESH_INTERVAL)

    for entity_id in (
        "sensor.test_league_draft_start",
        "sensor.test_league_on_the_clock",
        "sensor.test_league_my_next_pick",
        "sensor.test_league_last_pick",
    ):
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.state == STATE_UNKNOWN, entity_id
        assert "pick_no" not in state.attributes
        assert "draft_id" not in state.attributes
