"""Triggers for the Sleeper integration."""

from __future__ import annotations

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

from .const import DOMAIN, EVENT_PLAYER_SCORED, NOTABLE_POINTS_DELTA

CONF_SIDE = "side"
CONF_MIN_DELTA = "min_delta"

SIDE_ANY = "any"
SIDE_MINE = "mine"
SIDE_OPPONENT = "opponent"

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


class PlayerScoredTrigger(Trigger):
    """Trigger that fires when a starter in a matchup scores a notable change.

    It listens for the bus event the coordinator fires and applies the
    side, minimum change and league device filters of the automation.
    """

    @override
    @classmethod
    async def async_validate_config(
        cls, hass: HomeAssistant, config: ConfigType
    ) -> ConfigType:
        """Validate config."""
        return cast(ConfigType, _PLAYER_SCORED_SCHEMA(config))

    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        """Initialize the trigger."""
        super().__init__(hass, config)
        options = config.options or {}
        self._side: str = options[CONF_SIDE]
        self._min_delta: float = options[CONF_MIN_DELTA]
        self._target = config.target

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
            if self._side == SIDE_MINE and not data["is_mine"]:
                report("opponent_player", {"player": data["player"]})
                return
            if self._side == SIDE_OPPONENT and data["is_mine"]:
                report("own_player", {"player": data["player"]})
                return
            if abs(data["delta"]) < self._min_delta:
                report(
                    "change_too_small",
                    {"player": data["player"], "delta": data["delta"]},
                )
                return
            verb = "scored" if data["delta"] > 0 else "lost"
            run_action(
                dict(data),
                f"{data['player']} {verb} {abs(data['delta']):g} points",
                event.context,
            )

        return self._hass.bus.async_listen(EVENT_PLAYER_SCORED, handle_event)


TRIGGERS: dict[str, type[Trigger]] = {
    "player_scored": PlayerScoredTrigger,
}


async def async_get_triggers(hass: HomeAssistant) -> dict[str, type[Trigger]]:
    """Return the triggers of the Sleeper integration."""
    return TRIGGERS
