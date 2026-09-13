"""Sensor platform for the Sleeper integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import SleeperConfigEntry, SleeperCoordinator, SleeperData
from .entity import SleeperEntity


@dataclass(kw_only=True, frozen=True)
class SleeperSensorEntityDescription(SensorEntityDescription):
    """Describe a Sleeper sensor."""

    value_fn: Callable[[SleeperData], StateType]


SENSORS: tuple[SleeperSensorEntityDescription, ...] = (
    SleeperSensorEntityDescription(
        key="current_week",
        translation_key="current_week",
        value_fn=lambda data: data.state.display_week,
    ),
    SleeperSensorEntityDescription(
        key="season",
        translation_key="season",
        value_fn=lambda data: data.state.season,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SleeperConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Sleeper sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        SleeperSensor(coordinator, description) for description in SENSORS
    )


class SleeperSensor(SleeperEntity, SensorEntity):
    """A Sleeper sensor."""

    entity_description: SleeperSensorEntityDescription

    def __init__(
        self,
        coordinator: SleeperCoordinator,
        description: SleeperSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{description.key}"

    @property
    @override
    def native_value(self) -> StateType:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)
