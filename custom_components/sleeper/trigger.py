"""Triggers for the Sleeper integration.

Every trigger listens for one of the bus events the coordinator fires and
applies the automation's options and target to it, so the automation editor
can offer them by name without the user knowing the event types.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from typing import Any, cast, override

from homeassistant.const import ATTR_DEVICE_ID, CONF_OPTIONS, CONF_TARGET
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)
from homeassistant.helpers.trigger import (
    NotTriggeredInfo,
    Trigger,
    TriggerActionRunner,
    TriggerConfig,
    TriggerNotTriggeredReporter,
)
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    DOMAIN,
    EVENT_DRAFT_ON_THE_CLOCK,
    EVENT_DRAFT_PICK,
    EVENT_PLAYER_SCORED,
    NOTABLE_POINTS_DELTA,
)

CONF_SIDE = "side"
CONF_MIN_DELTA = "min_delta"
CONF_PICKER = "picker"

SIDE_ANY = "any"
SIDE_MINE = "mine"
SIDE_OPPONENT = "opponent"

PICKER_ANY = "any"
PICKER_MINE = "mine"
PICKER_OTHERS = "others"

_PLAYER_SCORED_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_TARGET): cv.TARGET_FIELDS,
        vol.Required(CONF_OPTIONS, default=dict): {
            vol.Optional(CONF_SIDE, default=SIDE_ANY): vol.In(
                (SIDE_ANY, SIDE_MINE, SIDE_OPPONENT)
            ),
            vol.Optional(CONF_MIN_DELTA, default=NOTABLE_POINTS_DELTA): vol.All(
                vol.Coerce(float), vol.Range(min=NOTABLE_POINTS_DELTA)
            ),
        },
    }
)


def _picker_schema(default: str) -> vol.Schema:
    """Return the schema of a draft trigger with a "whose pick" option."""
    return vol.Schema(
        {
            vol.Optional(CONF_TARGET): cv.TARGET_FIELDS,
            vol.Required(CONF_OPTIONS, default=dict): {
                vol.Optional(CONF_PICKER, default=default): vol.In(
                    (PICKER_ANY, PICKER_MINE, PICKER_OTHERS)
                ),
            },
        }
    )


_DRAFT_PICK_MADE_SCHEMA = _picker_schema(PICKER_ANY)
_ON_THE_CLOCK_SCHEMA = _picker_schema(PICKER_MINE)


@callback
def _targeted_league_devices(hass: HomeAssistant, target: ConfigType) -> set[str]:
    """Return the IDs of the league devices a trigger target refers to.

    Devices, areas, floors and labels resolve through the target helper.
    Entities count for the device they belong to, and the account device
    stands for all of its leagues (they point to it via ``via_device``).
    """
    selected = async_extract_referenced_entity_ids(hass, TargetSelection(target))
    device_ids = set(selected.referenced_devices)
    entity_registry = er.async_get(hass)
    for entity_id in selected.referenced:
        if (entry := entity_registry.async_get(entity_id)) and entry.device_id:
            device_ids.add(entry.device_id)
    device_registry = dr.async_get(hass)
    for entry in hass.config_entries.async_entries(DOMAIN):
        device_ids.update(
            device.id
            for device in dr.async_entries_for_config_entry(
                device_registry, entry.entry_id
            )
            if device.via_device_id in device_ids
        )
    return device_ids


# Reports why an event did not fire the trigger, for the automation trace.
type _Reporter = Callable[[str, dict[str, Any]], None]


class SleeperEventTrigger(Trigger):
    """Base of the triggers built on the integration's bus events.

    Subclasses name the event, validate the options and decide per event
    whether it passes their filters. The league target is checked here.
    """

    _event_type: str
    _schema: vol.Schema

    @override
    @classmethod
    async def async_validate_config(
        cls, hass: HomeAssistant, config: ConfigType
    ) -> ConfigType:
        """Validate config."""
        return cast(ConfigType, cls._schema(config))

    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        """Initialize the trigger."""
        super().__init__(hass, config)
        self._options: dict[str, Any] = config.options or {}
        self._target = config.target

    @abstractmethod
    def _accepts(self, data: dict[str, Any], report: _Reporter) -> bool:
        """Return whether an event passes the options; report why not."""

    @abstractmethod
    def _describe(self, data: dict[str, Any]) -> str:
        """Return the description of the event for the automation trace."""

    @override
    async def async_attach_runner(
        self,
        run_action: TriggerActionRunner,
        did_not_trigger: TriggerNotTriggeredReporter | None = None,
    ) -> CALLBACK_TYPE:
        """Attach the trigger to an action runner."""

        @callback
        def report(reason: str, data: dict[str, Any]) -> None:
            if did_not_trigger is not None:
                did_not_trigger(NotTriggeredInfo(reason, data))

        @callback
        def handle_event(event: Event[dict[str, Any]]) -> None:
            data = event.data
            if self._target is not None:
                devices = _targeted_league_devices(self._hass, self._target)
                if data[ATTR_DEVICE_ID] not in devices:
                    report("league_not_targeted", {"device_id": data[ATTR_DEVICE_ID]})
                    return
            if not self._accepts(data, report):
                return
            run_action(dict(data), self._describe(data), event.context)

        return self._hass.bus.async_listen(self._event_type, handle_event)


class PlayerScoredTrigger(SleeperEventTrigger):
    """Trigger that fires when a starter in a matchup scores a notable change."""

    _event_type = EVENT_PLAYER_SCORED
    _schema = _PLAYER_SCORED_SCHEMA

    @override
    def _accepts(self, data: dict[str, Any], report: _Reporter) -> bool:
        side: str = self._options[CONF_SIDE]
        min_delta: float = self._options[CONF_MIN_DELTA]
        if side == SIDE_MINE and not data["is_mine"]:
            report("opponent_player", {"player": data["player"]})
            return False
        if side == SIDE_OPPONENT and data["is_mine"]:
            report("own_player", {"player": data["player"]})
            return False
        if abs(data["delta"]) < min_delta:
            report(
                "change_too_small", {"player": data["player"], "delta": data["delta"]}
            )
            return False
        return True

    @override
    def _describe(self, data: dict[str, Any]) -> str:
        verb = "scored" if data["delta"] > 0 else "lost"
        return f"{data['player']} {verb} {abs(data['delta']):g} points"


class _DraftTrigger(SleeperEventTrigger):
    """Base of the draft triggers, filtering by whose pick it is."""

    def _picker_accepts(self, data: dict[str, Any], report: _Reporter) -> bool:
        """Apply the "whose pick" option."""
        picker: str = self._options[CONF_PICKER]
        if picker == PICKER_MINE and not data["is_mine"]:
            report("other_team", {"pick": data["pick"]})
            return False
        if picker == PICKER_OTHERS and data["is_mine"]:
            report("own_team", {"pick": data["pick"]})
            return False
        return True


class DraftPickMadeTrigger(_DraftTrigger):
    """Trigger that fires for every pick made in a draft."""

    _event_type = EVENT_DRAFT_PICK
    _schema = _DRAFT_PICK_MADE_SCHEMA

    @override
    def _accepts(self, data: dict[str, Any], report: _Reporter) -> bool:
        return self._picker_accepts(data, report)

    @override
    def _describe(self, data: dict[str, Any]) -> str:
        team = data["picked_by"] or "Someone"
        return f"{team} picked {data['player']} ({data['pick']})"


class OnTheClockTrigger(_DraftTrigger):
    """Trigger that fires when a team comes on the clock, by default yours."""

    _event_type = EVENT_DRAFT_ON_THE_CLOCK
    _schema = _ON_THE_CLOCK_SCHEMA

    @override
    def _accepts(self, data: dict[str, Any], report: _Reporter) -> bool:
        return self._picker_accepts(data, report)

    @override
    def _describe(self, data: dict[str, Any]) -> str:
        team = data["team"] or "A team"
        return f"{team} is on the clock ({data['pick']})"


TRIGGERS: dict[str, type[Trigger]] = {
    "player_scored": PlayerScoredTrigger,
    "draft_pick_made": DraftPickMadeTrigger,
    "on_the_clock": OnTheClockTrigger,
}


async def async_get_triggers(hass: HomeAssistant) -> dict[str, type[Trigger]]:
    """Return the triggers of the Sleeper integration."""
    return TRIGGERS
