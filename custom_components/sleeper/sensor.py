"""Sensor platform for the Sleeper integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .api import WAIVER_TYPE_FAAB, SleeperMatchup, SleeperRoster
from .const import LEAGUE_STATUSES, SEASON_TYPES, UNIT_POINTS
from .coordinator import (
    SleeperConfigEntry,
    SleeperCoordinator,
    SleeperData,
    SleeperLeagueData,
)
from .entity import SleeperEntity, SleeperLeagueEntity


@dataclass(kw_only=True, frozen=True)
class SleeperSensorEntityDescription(SensorEntityDescription):
    """Describe an account-level Sleeper sensor."""

    value_fn: Callable[[SleeperData], StateType]


@dataclass(kw_only=True, frozen=True)
class SleeperLeagueSensorEntityDescription(SensorEntityDescription):
    """Describe a Sleeper sensor that belongs to one league."""

    value_fn: Callable[[SleeperLeagueData], StateType]
    attributes_fn: Callable[[SleeperLeagueData], dict[str, Any]] | None = None
    exists_fn: Callable[[SleeperLeagueData], bool] = lambda _: True


def _roster_value(
    value_fn: Callable[[SleeperRoster], StateType],
) -> Callable[[SleeperLeagueData], StateType]:
    """Wrap a roster accessor so it yields ``None`` without an own roster."""
    return lambda data: None if data.my_roster is None else value_fn(data.my_roster)


def _matchup_value(
    value_fn: Callable[[SleeperMatchup], StateType],
) -> Callable[[SleeperLeagueData], StateType]:
    """Wrap a matchup accessor so it yields ``None`` without a matchup."""
    return lambda data: None if data.my_matchup is None else value_fn(data.my_matchup)


def _matchup_attributes(data: SleeperLeagueData) -> dict[str, Any]:
    """Return the details of the account's matchup of the week."""
    if (matchup := data.my_matchup) is None:
        return {}
    return {
        "week": data.week,
        "matchup_id": matchup.matchup_id,
        "starters": list(matchup.starters),
        "starters_points": list(matchup.starters_points),
    }


def _opponent_attributes(data: SleeperLeagueData) -> dict[str, Any]:
    """Return the details of the week's opponent."""
    if (roster := data.opponent_roster) is None:
        return {}
    return {"opponent_roster_id": roster.roster_id, "opponent_record": roster.record}


def _waiver_budget_attributes(data: SleeperLeagueData) -> dict[str, Any]:
    """Return the FAAB budget and how much of it is spent."""
    if (roster := data.my_roster) is None:
        return {}
    return {
        "budget": data.league.waiver_budget,
        "used": roster.waiver_budget_used,
    }


SENSORS: tuple[SleeperSensorEntityDescription, ...] = (
    SleeperSensorEntityDescription(
        key="week",
        translation_key="week",
        value_fn=lambda data: data.state.display_week,
    ),
    SleeperSensorEntityDescription(
        key="season",
        translation_key="season",
        value_fn=lambda data: data.state.season,
    ),
    SleeperSensorEntityDescription(
        key="season_type",
        translation_key="season_type",
        device_class=SensorDeviceClass.ENUM,
        options=list(SEASON_TYPES),
        value_fn=lambda data: data.state.season_type,
    ),
)

LEAGUE_SENSORS: tuple[SleeperLeagueSensorEntityDescription, ...] = (
    SleeperLeagueSensorEntityDescription(
        key="league_status",
        translation_key="league_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(LEAGUE_STATUSES),
        value_fn=lambda data: data.league.status,
        attributes_fn=lambda data: {
            "season": data.league.season,
            "num_teams": data.league.num_teams,
            "playoff_week_start": data.league.playoff_week_start,
            "playoff_teams": data.league.playoff_teams,
            "scoring_type": data.league.scoring_type,
        },
    ),
    SleeperLeagueSensorEntityDescription(
        key="record",
        translation_key="record",
        value_fn=_roster_value(lambda roster: roster.record),
        attributes_fn=lambda data: (
            {}
            if data.my_roster is None
            else {
                "wins": data.my_roster.wins,
                "losses": data.my_roster.losses,
                "ties": data.my_roster.ties,
            }
        ),
    ),
    SleeperLeagueSensorEntityDescription(
        key="wins",
        translation_key="wins",
        value_fn=_roster_value(lambda roster: roster.wins),
    ),
    SleeperLeagueSensorEntityDescription(
        key="losses",
        translation_key="losses",
        value_fn=_roster_value(lambda roster: roster.losses),
    ),
    SleeperLeagueSensorEntityDescription(
        key="ties",
        translation_key="ties",
        entity_registry_enabled_default=False,
        value_fn=_roster_value(lambda roster: roster.ties),
    ),
    SleeperLeagueSensorEntityDescription(
        key="rank",
        translation_key="rank",
        value_fn=lambda data: data.rank,
    ),
    SleeperLeagueSensorEntityDescription(
        key="points_for",
        translation_key="points_for",
        native_unit_of_measurement=UNIT_POINTS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_roster_value(lambda roster: roster.points_for),
    ),
    SleeperLeagueSensorEntityDescription(
        key="points_against",
        translation_key="points_against",
        native_unit_of_measurement=UNIT_POINTS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_roster_value(lambda roster: roster.points_against),
    ),
    SleeperLeagueSensorEntityDescription(
        key="matchup_points",
        translation_key="matchup_points",
        native_unit_of_measurement=UNIT_POINTS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_matchup_value(lambda matchup: matchup.points),
        attributes_fn=_matchup_attributes,
    ),
    SleeperLeagueSensorEntityDescription(
        key="opponent_points",
        translation_key="opponent_points",
        native_unit_of_measurement=UNIT_POINTS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: (
            None if data.opponent_matchup is None else data.opponent_matchup.points
        ),
    ),
    SleeperLeagueSensorEntityDescription(
        key="opponent",
        translation_key="opponent",
        value_fn=lambda data: data.opponent_name,
        attributes_fn=_opponent_attributes,
    ),
    SleeperLeagueSensorEntityDescription(
        key="waiver_position",
        translation_key="waiver_position",
        value_fn=_roster_value(lambda roster: roster.waiver_position),
    ),
    SleeperLeagueSensorEntityDescription(
        key="waiver_budget_remaining",
        translation_key="waiver_budget_remaining",
        exists_fn=lambda data: data.league.waiver_type == WAIVER_TYPE_FAAB,
        value_fn=lambda data: (
            None
            if data.my_roster is None
            else data.league.waiver_budget - data.my_roster.waiver_budget_used
        ),
        attributes_fn=_waiver_budget_attributes,
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

    known_league_ids: set[str] = set()

    @callback
    def _async_add_league_sensors() -> None:
        """Add sensors for leagues that appeared since the last check."""
        # Entities of a vanished league are removed with its device; forget
        # the league so it gets entities again should the account rejoin.
        known_league_ids.intersection_update(coordinator.data.leagues)
        new_league_ids = set(coordinator.data.leagues) - known_league_ids
        known_league_ids.update(new_league_ids)
        async_add_entities(
            SleeperLeagueSensor(coordinator, description, league_id)
            for league_id in new_league_ids
            for description in LEAGUE_SENSORS
            if description.exists_fn(coordinator.data.leagues[league_id])
        )

    _async_add_league_sensors()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_league_sensors))


class SleeperSensor(SleeperEntity, SensorEntity):
    """An account-level Sleeper sensor.

    These report the state of the sport itself (week, season), so their names
    carry the sport rather than anything account specific.
    """

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
        self._attr_translation_placeholders = {"sport": coordinator.sport.upper()}

    @property
    @override
    def native_value(self) -> StateType:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)


class SleeperLeagueSensor(SleeperLeagueEntity, SensorEntity):
    """A Sleeper sensor of one league."""

    entity_description: SleeperLeagueSensorEntityDescription

    def __init__(
        self,
        coordinator: SleeperCoordinator,
        description: SleeperLeagueSensorEntityDescription,
        league_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, league_id)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.config_entry.unique_id}_{league_id}_{description.key}"
        )

    @property
    @override
    def native_value(self) -> StateType:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.league_data)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional details for the sensor."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.league_data)
