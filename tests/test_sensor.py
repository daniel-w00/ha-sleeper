"""Tests for the Sleeper sensors."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_sensors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test the sensors are created with the expected values."""
    week = hass.states.get("sensor.test_user_current_week")
    assert week is not None
    assert week.state == "1"

    season = hass.states.get("sensor.test_user_season")
    assert season is not None
    assert season.state == "2026"

    entry = entity_registry.async_get("sensor.test_user_current_week")
    assert entry is not None
    assert entry.unique_id == "123456789_current_week"
