"""Tests for the shared player cache."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sleeper.api import SleeperConnectionError
from custom_components.sleeper.const import (
    DOMAIN,
    IDLE_SEASON_UPDATE_INTERVAL,
    PLAYERS_MAX_AGE,
    PLAYERS_RETRY_INTERVAL,
)
from custom_components.sleeper.players import PLAYERS_KEY

from .conftest import async_poll, load_json_fixture

STORAGE_KEY = f"{DOMAIN}.players_nfl"


def _stored(age: timedelta) -> dict[str, Any]:
    """Return storage contents of a player list fetched ``age`` ago."""
    return {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "fetched": (dt_util.utcnow() - age).isoformat(),
            "players": load_json_fixture("players.json"),
        },
    }


async def test_download_on_first_setup(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    hass_storage: dict[str, Any],
) -> None:
    """Test the player list is downloaded once and stored."""
    mock_client.get_players.assert_awaited_once()
    players = hass.data[PLAYERS_KEY]
    assert players.name("11560") == "Caleb Williams"
    assert players.name("nobody") == "nobody"
    assert players.get("nobody") is None
    assert not players.stale
    stored = hass_storage[STORAGE_KEY]["data"]
    assert stored["players"]["PIT"]["last_name"] == "Steelers"
    assert dt_util.parse_datetime(stored["fetched"]) is not None

    # A second account shares the list instead of downloading it again.
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Other",
        unique_id="987654321",
        data={CONF_USERNAME: "other"},
    )
    second.add_to_hass(hass)
    await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    assert second.state is ConfigEntryState.LOADED
    mock_client.get_players.assert_awaited_once()
    assert second.runtime_data.players is players


async def test_loaded_from_storage(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    hass_storage: dict[str, Any],
) -> None:
    """Test a fresh stored copy avoids the download."""
    hass_storage[STORAGE_KEY] = _stored(timedelta(hours=1))
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client.get_players.assert_not_awaited()
    assert hass.data[PLAYERS_KEY].name("1466") == "Travis Kelce"


async def test_stale_storage_is_refreshed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    hass_storage: dict[str, Any],
) -> None:
    """Test a stored copy older than a day is downloaded again."""
    hass_storage[STORAGE_KEY] = _stored(PLAYERS_MAX_AGE + timedelta(minutes=1))
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client.get_players.assert_awaited_once()


async def test_daily_refresh(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the list is downloaded again once it is a day old."""
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    mock_client.get_players.assert_awaited_once()

    await async_poll(hass, freezer, PLAYERS_MAX_AGE)
    assert mock_client.get_players.await_count == 2


async def test_download_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a failed download does not break setup and is retried hourly."""
    mock_client.get_players.side_effect = SleeperConnectionError("down")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert "Could not download the Sleeper player list" in caplog.text
    mock_client.get_players.assert_awaited_once()
    # Names fall back to IDs, starters are still listed.
    state = hass.states.get("sensor.wombats_league_matchup_points")
    assert state is not None
    assert state.attributes["starters"][0]["player"] == "11560"

    # No retry within the retry interval, even though the list is stale.
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    mock_client.get_players.assert_awaited_once()

    mock_client.get_players.side_effect = None
    await async_poll(hass, freezer, PLAYERS_RETRY_INTERVAL)
    assert mock_client.get_players.await_count == 2
    state = hass.states.get("sensor.wombats_league_matchup_points")
    assert state is not None
    assert state.attributes["starters"][0]["player"] == "Caleb Williams"
