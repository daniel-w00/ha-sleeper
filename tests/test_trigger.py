"""Tests for the player scored trigger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from homeassistant.helpers.trigger import (
    NotTriggeredInfo,
    TriggerConfig,
    async_get_all_descriptions,
)
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)
import yaml

from custom_components.sleeper.const import (
    DOMAIN,
    EVENT_PLAYER_SCORED,
    IDLE_SEASON_UPDATE_INTERVAL,
)
from custom_components.sleeper.coordinator import league_device_identifier
from custom_components.sleeper.trigger import PlayerScoredTrigger

from .conftest import (
    LEAGUE_ID,
    PREDRAFT_LEAGUE_ID,
    TEST_USER_ID,
    async_poll,
    matchups_with_points,
)

TRIGGER_KEY = f"{DOMAIN}.player_scored"
INTEGRATION = Path("custom_components") / DOMAIN

# My QB gains 6.34 points, the opponent's kicker loses 3 points.
SCORING_CHANGES = {(1, "11560"): 24.0, (5, "11792"): -3.0}


async def setup_automation(
    hass: HomeAssistant, trigger: dict[str, Any]
) -> list[ServiceCall]:
    """Set up an automation with the trigger; return its recorded actions."""
    calls = async_mock_service(hass, "test", "automation")
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "id": "test",
                "alias": "Test",
                "triggers": [{"trigger": TRIGGER_KEY, **trigger}],
                "actions": [
                    {
                        "action": "test.automation",
                        "data": {
                            "player": "{{ trigger.player }}",
                            "delta": "{{ trigger.delta }}",
                            "description": "{{ trigger.description }}",
                        },
                    }
                ],
            }
        },
    )
    await hass.async_block_till_done()
    return calls


async def async_score(
    hass: HomeAssistant, mock_client: MagicMock, freezer: FrozenDateTimeFactory
) -> None:
    """Let the next poll report the scoring changes."""
    mock_client.get_matchups.side_effect = lambda league_id, week: matchups_with_points(
        SCORING_CHANGES
    )
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)


def league_device(
    hass: HomeAssistant, entry: MockConfigEntry, league_id: str
) -> dr.DeviceEntry:
    """Return the device of a league."""
    device = dr.async_get(hass).async_get_device_by_identifier(
        league_device_identifier(TEST_USER_ID, league_id),
        config_entry_id=entry.entry_id,
    )
    assert device is not None
    return device


async def test_player_scored(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the trigger fires for both matchup sides by default."""
    calls = await setup_automation(hass, {})
    assert hass.states.get("automation.test") is not None

    await async_score(hass, mock_client, freezer)

    assert [call.data for call in calls] == [
        {
            "player": "Caleb Williams",
            "delta": 6.34,
            "description": "Caleb Williams scored 6.34 points",
        },
        {
            "player": "Will Reichard",
            "delta": -3.0,
            "description": "Will Reichard lost 3 points",
        },
    ]


@pytest.mark.parametrize(
    ("options", "players"),
    [
        ({"side": "mine"}, ["Caleb Williams"]),
        ({"side": "opponent"}, ["Will Reichard"]),
        ({"min_delta": 6}, ["Caleb Williams"]),
        ({"side": "opponent", "min_delta": 6}, []),
    ],
)
async def test_options(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
    options: dict[str, Any],
    players: list[str],
) -> None:
    """Test the side and minimum change options filter the events."""
    calls = await setup_automation(hass, {"options": options})

    await async_score(hass, mock_client, freezer)

    assert [call.data["player"] for call in calls] == players


@pytest.mark.parametrize(
    ("target", "fires"),
    [
        ("league_device", True),
        ("other_league_device", False),
        ("account_device", True),
        ("league_entity", True),
        ("area", True),
        ("label", True),
        ("unknown_device", False),
        ("entity_without_device", False),
    ],
)
async def test_target(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    area_registry: ar.AreaRegistry,
    label_registry: lr.LabelRegistry,
    target: str,
    fires: bool,
) -> None:
    """Test the trigger can be limited to a league in every target form."""
    device = league_device(hass, init_integration, LEAGUE_ID)
    if target == "league_device":
        config = {"device_id": device.id}
    elif target == "other_league_device":
        config = {
            "device_id": league_device(hass, init_integration, PREDRAFT_LEAGUE_ID).id
        }
    elif target == "account_device":
        assert device.via_device_id is not None
        config = {"device_id": device.via_device_id}
    elif target == "league_entity":
        config = {"entity_id": "sensor.wombats_league_matchup_points"}
    elif target == "area":
        area = area_registry.async_create("Living room")
        device_registry.async_update_device(device.id, area_id=area.id)
        config = {"area_id": area.id}
    elif target == "label":
        label = label_registry.async_create("Football")
        device_registry.async_update_device(device.id, labels={label.label_id})
        config = {"label_id": label.label_id}
    elif target == "entity_without_device":
        entry = entity_registry.async_get_or_create("sensor", "test", "lonely")
        config = {"entity_id": entry.entity_id}
    else:
        config = {"device_id": "no_such_device"}
    calls = await setup_automation(hass, {"target": config})

    await async_score(hass, mock_client, freezer)

    assert bool(calls) is fires


async def test_target_without_device(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test a targeted trigger ignores events of a league without a device."""
    device = league_device(hass, init_integration, LEAGUE_ID)
    calls = await setup_automation(hass, {"target": {"device_id": device.id}})
    device_registry.async_remove_device(device.id)
    await hass.async_block_till_done()

    await async_score(hass, mock_client, freezer)

    assert not calls


async def test_invalid_options(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test an automation with invalid trigger options is not set up."""
    await setup_automation(hass, {"options": {"side": "referee"}})

    state = hass.states.get("automation.test")
    assert state is not None
    assert state.state == "unavailable"
    assert "value must be one of ['any', 'mine', 'opponent']" in caplog.text


async def test_not_triggered_reports(hass: HomeAssistant) -> None:
    """Test filtered events are reported as not triggered for traces."""
    trigger = PlayerScoredTrigger(
        hass,
        TriggerConfig(
            TRIGGER_KEY,
            target={"device_id": "league"},
            options={"side": "mine", "min_delta": 6.0},
        ),
    )
    run_action = MagicMock()
    did_not_trigger = MagicMock()
    remove = await trigger.async_attach_runner(run_action, did_not_trigger)

    for data, reason in (
        ({"device_id": "other"}, "league_not_targeted"),
        ({"device_id": "league", "is_mine": False, "player": "A"}, "opponent_player"),
        (
            {"device_id": "league", "is_mine": True, "player": "A", "delta": 4.0},
            "change_too_small",
        ),
    ):
        hass.bus.async_fire(EVENT_PLAYER_SCORED, data)
        await hass.async_block_till_done()
        info: NotTriggeredInfo = did_not_trigger.call_args.args[0]
        assert info.reason == reason
    assert did_not_trigger.call_count == 3
    run_action.assert_not_called()
    remove()

    trigger = PlayerScoredTrigger(
        hass, TriggerConfig(TRIGGER_KEY, options={"side": "opponent", "min_delta": 3.0})
    )
    remove = await trigger.async_attach_runner(run_action, did_not_trigger)
    hass.bus.async_fire(
        EVENT_PLAYER_SCORED, {"device_id": None, "is_mine": True, "player": "A"}
    )
    await hass.async_block_till_done()
    assert did_not_trigger.call_args.args[0].reason == "own_player"
    run_action.assert_not_called()
    remove()

    # Without a reporter (e.g. a script), filtered events are simply ignored.
    remove = await trigger.async_attach_runner(run_action)
    hass.bus.async_fire(
        EVENT_PLAYER_SCORED, {"device_id": None, "is_mine": True, "player": "A"}
    )
    await hass.async_block_till_done()
    remove()

    hass.bus.async_fire(EVENT_PLAYER_SCORED, {"device_id": "other"})
    await hass.async_block_till_done()
    assert did_not_trigger.call_count == 4
    run_action.assert_not_called()


async def test_descriptions_and_translations(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test triggers.yaml loads and every trigger and field is translated."""
    descriptions = await async_get_all_descriptions(hass)
    description = descriptions[TRIGGER_KEY]
    assert description is not None
    assert set(description["fields"]) == {"side", "min_delta"}

    triggers = yaml.safe_load((INTEGRATION / "triggers.yaml").read_text())
    strings = json.loads((INTEGRATION / "strings.json").read_text())["triggers"]
    icons = json.loads((INTEGRATION / "icons.json").read_text())["triggers"]
    assert set(triggers) == set(strings) == set(icons) == {"player_scored"}
    for key, trigger in triggers.items():
        assert set(trigger["fields"]) == set(strings[key]["fields"])
