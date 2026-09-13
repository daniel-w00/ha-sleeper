"""Tests for the Sleeper binary sensors."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.sleeper.const import IDLE_SEASON_UPDATE_INTERVAL

from .conftest import async_poll, matchups_for


@pytest.fixture(autouse=True)
def platforms() -> Generator[None]:
    """Load only the binary sensor platform."""
    with patch("custom_components.sleeper.PLATFORMS", [Platform.BINARY_SENSOR]):
        yield


async def test_binary_sensors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test all binary sensors against the snapshot."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


async def test_matchup_leading(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the leading flag follows the points of both sides."""
    state = hass.states.get("binary_sensor.wombats_league_leading_matchup")
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["margin"] == 35.76

    state = hass.states.get("binary_sensor.test_league_leading_matchup")
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert "margin" not in state.attributes

    mock_client.get_matchups.side_effect = lambda league_id, week: tuple(
        replace(matchup, points=20.0) if matchup.roster_id == 1 else matchup
        for matchup in matchups_for(league_id, week)
    )
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)

    state = hass.states.get("binary_sensor.wombats_league_leading_matchup")
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["margin"] == -15.9
