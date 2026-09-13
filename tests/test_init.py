"""Tests for setting up and unloading the Sleeper integration."""

from __future__ import annotations

from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_USERNAME, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sleeper.api import SleeperConnectionError, SleeperLeague
from custom_components.sleeper.const import DOMAIN, LEAGUE_REFRESH_INTERVAL
from custom_components.sleeper.coordinator import league_device_identifier

from .conftest import LEAGUE_ID, PREDRAFT_LEAGUE_ID, TEST_USER_ID, async_poll


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test a config entry loads and unloads cleanly."""
    assert init_integration.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_on_connection_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Test setup goes into retry when Sleeper is unreachable."""
    mock_client.get_user.side_effect = SleeperConnectionError("down")
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_devices(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test the account device and the league devices linked to it."""
    account = device_registry.async_get_device_by_identifier(
        (DOMAIN, TEST_USER_ID), config_entry_id=init_integration.entry_id
    )
    assert account is not None
    assert account.name == "Test User"
    assert account.model == "Account"
    assert account.entry_type is dr.DeviceEntryType.SERVICE

    league = device_registry.async_get_device_by_identifier(
        league_device_identifier(TEST_USER_ID, LEAGUE_ID),
        config_entry_id=init_integration.entry_id,
    )
    assert league is not None
    assert league.name == "Wombats League"
    assert league.model == "12-team NFL league"
    assert league.manufacturer == "Sleeper"
    assert league.via_device_id == account.id
    assert league.configuration_url == f"https://sleeper.com/leagues/{LEAGUE_ID}"
    assert league.identifiers == {(DOMAIN, f"{TEST_USER_ID}_{LEAGUE_ID}")}

    predraft = device_registry.async_get_device_by_identifier(
        league_device_identifier(TEST_USER_ID, PREDRAFT_LEAGUE_ID),
        config_entry_id=init_integration.entry_id,
    )
    assert predraft is not None
    assert predraft.model == "4-team NFL league"


async def test_league_removed_and_rejoined(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    mock_leagues: tuple[SleeperLeague, ...],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a league that vanishes loses its device and entities, and returns."""
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    entity_id = "sensor.test_league_league_status"
    assert hass.states.get(entity_id) is not None

    # Sleeper answers with an empty list: treated as a hiccup, nothing removed.
    mock_client.get_user_leagues.return_value = ()
    await async_poll(hass, freezer, LEAGUE_REFRESH_INTERVAL)
    assert (
        device_registry.async_get_device_by_identifier(
            league_device_identifier(TEST_USER_ID, PREDRAFT_LEAGUE_ID),
            config_entry_id=init_integration.entry_id,
        )
        is not None
    )
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE

    # The account left the pre-draft league.
    mock_client.get_user_leagues.return_value = mock_leagues[1:]
    await async_poll(hass, freezer, LEAGUE_REFRESH_INTERVAL)
    assert (
        device_registry.async_get_device_by_identifier(
            league_device_identifier(TEST_USER_ID, PREDRAFT_LEAGUE_ID),
            config_entry_id=init_integration.entry_id,
        )
        is None
    )
    assert hass.states.get(entity_id) is None
    assert entity_registry.async_get(entity_id) is None
    assert hass.states.get("sensor.wombats_league_league_status") is not None

    # It rejoined: device and entities come back without a reload.
    mock_client.get_user_leagues.return_value = mock_leagues
    await async_poll(hass, freezer, LEAGUE_REFRESH_INTERVAL)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "pre_draft"
    assert entity_registry.async_get(entity_id) is not None
    assert (
        device_registry.async_get_device_by_identifier(
            league_device_identifier(TEST_USER_ID, PREDRAFT_LEAGUE_ID),
            config_entry_id=init_integration.entry_id,
        )
        is not None
    )


async def test_two_accounts_in_one_league(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test a second account in the same league gets its own league device."""
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

    first_device = device_registry.async_get_device_by_identifier(
        league_device_identifier(TEST_USER_ID, LEAGUE_ID),
        config_entry_id=init_integration.entry_id,
    )
    second_device = device_registry.async_get_device_by_identifier(
        league_device_identifier("987654321", LEAGUE_ID),
        config_entry_id=second.entry_id,
    )
    assert first_device is not None
    assert second_device is not None
    assert first_device.id != second_device.id
    assert first_device.name == second_device.name == "Wombats League"
    second_account = device_registry.async_get_device_by_identifier(
        (DOMAIN, "987654321"), config_entry_id=second.entry_id
    )
    assert second_account is not None
    assert second_device.via_device_id == second_account.id
    assert first_device.config_entries == {init_integration.entry_id}
    assert second_device.config_entries == {second.entry_id}
    # Entities of the second account are separate as well.
    assert hass.states.get("sensor.wombats_league_rank_2") is not None
