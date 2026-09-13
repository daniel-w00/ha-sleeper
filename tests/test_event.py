"""Tests for the Sleeper event entities."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
from types import MappingProxyType
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

from custom_components.sleeper.api import SleeperMatchup
from custom_components.sleeper.const import IDLE_SEASON_UPDATE_INTERVAL

from .conftest import LEAGUE_ID, async_poll, matchups_for

ENTITY_ID = "event.wombats_league_player_scoring"


@pytest.fixture(autouse=True)
def platforms() -> Generator[None]:
    """Load only the event platform."""
    with patch("custom_components.sleeper.PLATFORMS", [Platform.EVENT]):
        yield


def _with_points(
    changes: dict[tuple[int, str], float],
) -> tuple[SleeperMatchup, ...]:
    """Return the fixture matchups with some player points replaced."""
    result = []
    for matchup in matchups_for(LEAGUE_ID, 1):
        points = dict(matchup.players_points)
        for (roster_id, player_id), value in changes.items():
            if roster_id == matchup.roster_id:
                points[player_id] = value
        result.append(replace(matchup, players_points=MappingProxyType(points)))
    return tuple(result)


async def test_event_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test all event entities against the snapshot."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


async def test_points_changes(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test point changes of both matchup sides fire events."""
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN

    # Unchanged points: still no event.
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN

    # My QB scores a touchdown, my bench RB gains a bit, the opponent's
    # kicker (roster 5) scores too and someone in another matchup as well.
    mock_client.get_matchups.side_effect = lambda league_id, week: _with_points(
        {
            (1, "11560"): 24.0,
            (1, "11581"): 1.5,
            (5, "11792"): 6.0,
            (9, "11560"): 99.0,
        }
    )
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)

    # The entity ends on the account's biggest gain.
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state != STATE_UNKNOWN
    assert state.attributes["event_type"] == "points_changed"
    assert state.attributes["player"] == "Caleb Williams"
    assert state.attributes["player_id"] == "11560"
    assert state.attributes["position"] == "QB"
    assert state.attributes["team"] == "CHI"
    assert state.attributes["roster_id"] == 1
    assert state.attributes["is_mine"] is True
    assert state.attributes["is_starter"] is True
    assert state.attributes["previous_points"] == 17.66
    assert state.attributes["points"] == 24.0
    assert state.attributes["delta"] == 6.34
    assert state.attributes["week"] == 1
    assert state.attributes["matchup_id"] == 6

    data = init_integration.runtime_data.data.leagues[LEAGUE_ID]
    changes = {
        (change.roster_id, change.player_id): change for change in data.points_changes
    }
    assert set(changes) == {(1, "11560"), (1, "11581"), (5, "11792")}
    bench = changes[(1, "11581")]
    assert bench.is_starter is False
    assert bench.is_mine is True
    assert bench.delta == 1.5
    opponent = changes[(5, "11792")]
    assert opponent.is_mine is False
    assert opponent.player is not None
    assert opponent.player.position == "K"

    # Same points again: no new event, the last one stays.
    last_changed = state.last_changed
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.last_changed == last_changed
    assert not init_integration.runtime_data.data.leagues[LEAGUE_ID].points_changes


async def test_new_week_starts_fresh(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a week change does not report every player dropping to zero."""
    sport_state = init_integration.runtime_data.data.state
    mock_client.get_sport_state.return_value = replace(sport_state, week=2)
    mock_client.get_matchups.side_effect = lambda league_id, week: tuple(
        replace(
            matchup,
            points=0.0,
            players_points=MappingProxyType(dict.fromkeys(matchup.players, 0.0)),
        )
        for matchup in matchups_for(league_id, 1)
    )
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert not init_integration.runtime_data.data.leagues[LEAGUE_ID].points_changes
