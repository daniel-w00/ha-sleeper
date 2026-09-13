"""Tests for the Sleeper config flow."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant import config_entries
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sleeper.api import (
    SleeperConnectionError,
    SleeperNotFoundError,
)
from custom_components.sleeper.const import DOMAIN

from .conftest import TEST_USER_ID, TEST_USERNAME


async def test_user_flow(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Test the full happy path of the user flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: f"  {TEST_USERNAME} "}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test User"
    assert result["data"] == {CONF_USERNAME: TEST_USERNAME}
    assert result["result"].unique_id == TEST_USER_ID
    # First call is the flow's validation with the stripped input; the entry
    # setup that follows calls it again through the coordinator.
    assert mock_client.get_user.await_args_list[0].args == (TEST_USERNAME,)


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (SleeperNotFoundError("nope"), "user_not_found"),
        (SleeperConnectionError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_client: MagicMock,
    side_effect: Exception,
    error: str,
) -> None:
    """Test that API errors are shown in the form and the flow can recover."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    mock_client.get_user.side_effect = side_effect
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: TEST_USERNAME}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_client.get_user.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: TEST_USERNAME}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_already_configured(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the same account cannot be added twice."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: TEST_USERNAME}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
