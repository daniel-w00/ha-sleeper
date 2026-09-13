"""Shared fixtures for the Sleeper integration tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sleeper.api import SleeperSportState, SleeperUser
from custom_components.sleeper.const import DOMAIN

TEST_USERNAME = "testuser"
TEST_USER_ID = "123456789"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading of custom integrations in every test."""


@pytest.fixture
def mock_user() -> SleeperUser:
    """Return a Sleeper user as the API would deliver it."""
    return SleeperUser(
        user_id=TEST_USER_ID,
        username=TEST_USERNAME,
        display_name="Test User",
        avatar=None,
    )


@pytest.fixture
def mock_sport_state() -> SleeperSportState:
    """Return a sport state as the API would deliver it."""
    return SleeperSportState(
        week=3,
        season="2026",
        season_type="regular",
        display_week=3,
        league_season="2026",
    )


@pytest.fixture
def mock_client(
    mock_user: SleeperUser, mock_sport_state: SleeperSportState
) -> Generator[MagicMock]:
    """Patch the Sleeper client in the config flow and the coordinator."""
    with (
        patch(
            "custom_components.sleeper.config_flow.SleeperClient", autospec=True
        ) as flow_client,
        patch(
            "custom_components.sleeper.coordinator.SleeperClient",
            new=flow_client,
        ),
    ):
        client = flow_client.return_value
        client.get_user = AsyncMock(return_value=mock_user)
        client.get_sport_state = AsyncMock(return_value=mock_sport_state)
        yield client


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for the test user."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test User",
        unique_id=TEST_USER_ID,
        data={CONF_USERNAME: TEST_USERNAME},
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> MockConfigEntry:
    """Set up the integration with the mocked client."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
