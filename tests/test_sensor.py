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

from custom_components.sleeper.const import (
    IDLE_SEASON_UPDATE_INTERVAL,
    LEAGUE_REFRESH_INTERVAL,
)

from .conftest import LEAGUE_ID, TEST_USER_ID, async_poll, rosters_for


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
        ("sensor.wombats_league_record", "0-0"),
        ("sensor.wombats_league_rank", "1"),
        ("sensor.wombats_league_points_for", "0.0"),
        ("sensor.wombats_league_points_against", "0.0"),
        ("sensor.wombats_league_matchup_points", "71.66"),
        ("sensor.wombats_league_starters_out", "2"),
        ("sensor.wombats_league_opponent_points", "35.9"),
        ("sensor.wombats_league_opponent", "user_12"),
        ("sensor.wombats_league_waiver_position", "6"),
        ("sensor.wombats_league_waiver_budget_remaining", "100"),
        ("sensor.test_league_league_status", "pre_draft"),
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

    # Pre-draft league: no matchup details, no FAAB entity.
    matchup = hass.states.get("sensor.test_league_matchup_points")
    assert matchup is not None
    assert "week" not in matchup.attributes
    assert hass.states.get("sensor.test_league_waiver_budget_remaining") is None
    record = entity_registry.async_get("sensor.wombats_league_record")
    assert record is not None
    assert record.unique_id == f"{TEST_USER_ID}_{LEAGUE_ID}_record"


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
