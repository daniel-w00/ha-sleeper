"""Sensor platform for the Sleeper integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
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
from .const import LEAGUE_STATUSES, UNIT_PICKS, UNIT_POINTS
from .coordinator import (
    SleeperConfigEntry,
    SleeperCoordinator,
    SleeperLeagueData,
    SleeperUpcomingPick,
)
from .entity import SleeperLeagueEntity


@dataclass(kw_only=True, frozen=True)
class SleeperLeagueSensorEntityDescription(SensorEntityDescription):
    """Describe a Sleeper sensor that belongs to one league."""

    value_fn: Callable[[SleeperLeagueData], StateType | datetime]
    attributes_fn: Callable[[SleeperLeagueData], dict[str, Any]] | None = None
    entity_picture_fn: Callable[[SleeperLeagueData], str | None] | None = None
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
        "starters": [
            {
                "slot": starter.slot,
                "player": starter.name,
                "player_id": starter.player_id,
                "points": points,
            }
            for starter, points in zip(
                data.matchup_starters, matchup.starters_points, strict=False
            )
        ],
    }


def _starters_out_attributes(data: SleeperLeagueData) -> dict[str, Any]:
    """Return which starters are not expected to play and why."""
    if data.my_roster is None:
        return {}
    return {
        "starters": [
            {"slot": starter.slot, "player": starter.name, "status": starter.status}
            for starter in data.starters_out
        ],
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


def _last_big_play_attributes(data: SleeperLeagueData) -> dict[str, Any]:
    """Return the details of the last notable points change."""
    if (change := data.last_big_play) is None:
        return {}
    player = change.player
    return {
        "player_id": change.player_id,
        "position": None if player is None else player.position,
        "team": None if player is None else player.team,
        "is_mine": change.is_mine,
        "previous_points": change.previous,
        "points": change.points,
        "delta": change.delta,
        "week": data.week,
    }


def _last_big_play_picture(data: SleeperLeagueData) -> str | None:
    """Return the picture of the player of the last notable points change."""
    return None if (change := data.last_big_play) is None else change.picture


def _my_picture(data: SleeperLeagueData) -> str | None:
    """Return the picture of the account's team."""
    return None if (user := data.my_user) is None else user.avatar_url


def _draft_start_attributes(data: SleeperLeagueData) -> dict[str, Any]:
    """Return the draft's settings."""
    if (draft := data.draft) is None:
        return {}
    return {
        "draft_id": draft.draft_id,
        "status": draft.status,
        "type": draft.type,
        "rounds": draft.rounds,
        "teams": draft.teams,
        "pick_timer": int(draft.pick_timer.total_seconds()),
        "my_slot": (
            None
            if data.my_roster is None or data.my_roster.owner_id is None
            else draft.draft_order.get(data.my_roster.owner_id)
        ),
    }


def _upcoming_pick_attributes(pick: SleeperUpcomingPick) -> dict[str, Any]:
    """Return the position of a pick that is still to be made."""
    return {
        "pick_no": pick.pick_no,
        "round": pick.round,
        "pick_in_round": pick.pick_in_round,
        "pick": pick.label,
    }


def _on_the_clock_attributes(data: SleeperLeagueData) -> dict[str, Any]:
    """Return the details of the pick that is up and the draft's progress."""
    if (pick := data.on_the_clock) is None or (draft := data.draft) is None:
        return {}
    deadline = data.pick_deadline
    return {
        **_upcoming_pick_attributes(pick),
        "roster_id": pick.roster_id,
        "is_mine": pick.is_mine,
        "deadline": None if deadline is None else deadline.isoformat(),
        "paused": draft.status != "drafting" or draft.is_autopaused,
        "picks_made": len(data.draft_picks),
        "picks_total": draft.total_picks,
    }


def _on_the_clock_picture(data: SleeperLeagueData) -> str | None:
    """Return the picture of the team that is on the clock."""
    if (pick := data.on_the_clock) is None or pick.user is None:
        return None
    return pick.user.avatar_url


def _my_next_pick_attributes(data: SleeperLeagueData) -> dict[str, Any]:
    """Return the position of the account's next pick."""
    if (pick := data.my_next_pick) is None:
        return {}
    return _upcoming_pick_attributes(pick)


def _last_pick_attributes(data: SleeperLeagueData) -> dict[str, Any]:
    """Return the details of the most recent draft pick."""
    if (pick := data.last_draft_pick) is None or (draft := data.draft) is None:
        return {}
    user = data.user_for_roster_id(pick.roster_id)
    pick_in_round = draft.pick_in_round(pick.pick_no)
    return {
        "pick_no": pick.pick_no,
        "round": pick.round,
        "pick_in_round": pick_in_round,
        "pick": f"{pick.round}.{pick_in_round:02d}",
        "picked_by": None if user is None else user.name,
        "roster_id": pick.roster_id,
        "player_id": pick.player_id,
        "position": pick.player.position,
        "team": pick.player.team,
        "is_mine": data.pick_is_mine(pick),
        "is_keeper": pick.is_keeper,
    }


def _last_pick_picture(data: SleeperLeagueData) -> str | None:
    """Return the picture of the most recently picked player."""
    if (pick := data.last_draft_pick) is None:
        return None
    return pick.player.picture_url(data.league.sport)


def _opponent_picture(data: SleeperLeagueData) -> str | None:
    """Return the picture of the week's opponent."""
    return None if (user := data.opponent_user) is None else user.avatar_url


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
        key="team",
        translation_key="team",
        value_fn=lambda data: data.my_team_name,
        entity_picture_fn=_my_picture,
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
        key="starters_out",
        translation_key="starters_out",
        value_fn=lambda data: (
            None if data.my_roster is None else len(data.starters_out)
        ),
        attributes_fn=_starters_out_attributes,
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
        entity_picture_fn=_opponent_picture,
    ),
    SleeperLeagueSensorEntityDescription(
        key="opponent",
        translation_key="opponent",
        value_fn=lambda data: data.opponent_name,
        entity_picture_fn=_opponent_picture,
        attributes_fn=_opponent_attributes,
    ),
    SleeperLeagueSensorEntityDescription(
        key="last_big_play",
        translation_key="last_big_play",
        value_fn=lambda data: (
            None if data.last_big_play is None else data.last_big_play.player_name
        ),
        entity_picture_fn=_last_big_play_picture,
        attributes_fn=_last_big_play_attributes,
    ),
    SleeperLeagueSensorEntityDescription(
        key="waiver_position",
        translation_key="waiver_position",
        value_fn=_roster_value(lambda roster: roster.waiver_position),
    ),
    SleeperLeagueSensorEntityDescription(
        key="draft_start",
        translation_key="draft_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        exists_fn=lambda data: data.draft is not None,
        value_fn=lambda data: None if data.draft is None else data.draft.start_time,
        attributes_fn=_draft_start_attributes,
    ),
    SleeperLeagueSensorEntityDescription(
        key="on_the_clock",
        translation_key="on_the_clock",
        exists_fn=lambda data: data.draft is not None,
        value_fn=lambda data: (
            None if data.on_the_clock is None else data.on_the_clock.team_name
        ),
        entity_picture_fn=_on_the_clock_picture,
        attributes_fn=_on_the_clock_attributes,
    ),
    SleeperLeagueSensorEntityDescription(
        key="my_next_pick",
        translation_key="my_next_pick",
        native_unit_of_measurement=UNIT_PICKS,
        exists_fn=lambda data: data.draft is not None,
        value_fn=lambda data: data.picks_until_mine,
        attributes_fn=_my_next_pick_attributes,
    ),
    SleeperLeagueSensorEntityDescription(
        key="last_pick",
        translation_key="last_pick",
        exists_fn=lambda data: data.draft is not None,
        value_fn=lambda data: (
            None if data.last_draft_pick is None else data.last_draft_pick.player.name
        ),
        entity_picture_fn=_last_pick_picture,
        attributes_fn=_last_pick_attributes,
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
    def native_value(self) -> StateType | datetime:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.league_data)

    @property
    @override
    def entity_picture(self) -> str | None:
        """Return the league or team picture shown instead of the icon."""
        # Home Assistant also reads the picture of unavailable entities, whose
        # league may be gone from the coordinator data.
        if self.entity_description.entity_picture_fn is None or not self.available:
            return None
        return self.entity_description.entity_picture_fn(self.league_data)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional details for the sensor."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.league_data)
