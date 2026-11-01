"""Tests for the Sleeper diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion

from custom_components.sleeper.api import SleeperConnectionError


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test the diagnostics of an account, without IDs and names."""
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, init_integration
    )

    assert diagnostics == snapshot


async def test_diagnostics_after_a_failed_update(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Test the diagnostics name the error of the last update."""
    mock_client.get_sport_state.side_effect = SleeperConnectionError("down")
    await init_integration.runtime_data.async_refresh()

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, init_integration
    )

    assert diagnostics["last_update_success"] is False
    exception = diagnostics["last_exception"]
    assert isinstance(exception, str)
    assert "Error communicating with Sleeper: down" in exception
