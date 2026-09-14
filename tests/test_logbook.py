"""Tests for the player scoring events and their logbook description."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.sleeper.api import SleeperMatchup
from custom_components.sleeper.const import (
    DOMAIN,
    EVENT_PLAYER_SCORED,
    IDLE_SEASON_UPDATE_INTERVAL,
)
from custom_components.sleeper.coordinator import league_device_identifier
from custom_components.sleeper.logbook import async_describe_events

from .conftest import (
    LEAGUE_ID,
    TEST_USER_ID,
    async_poll,
    matchups_for,
    matchups_with_points,
)


async def test_scoring_events(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test notable starter point changes of both matchup sides fire events."""
    events = async_capture_events(hass, EVENT_PLAYER_SCORED)

    # Unchanged points: no event.
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    assert not events

    # My QB scores a touchdown, my RB gains a little, my bench RB a lot, the
    # opponent's kicker (roster 5) loses points and someone in another
    # matchup scores too.
    mock_client.get_matchups.side_effect = lambda league_id, week: matchups_with_points(
        {
            (1, "11560"): 24.0,
            (1, "6813"): 11.0,
            (1, "11581"): 20.0,
            (5, "11792"): -3.0,
            (9, "11560"): 99.0,
        }
    )
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)

    assert [(e.data["player_id"], e.data["delta"]) for e in events] == [
        ("11560", 6.34),
        ("11792", -3.0),
    ]
    device = device_registry.async_get_device_by_identifier(
        league_device_identifier(TEST_USER_ID, LEAGUE_ID),
        config_entry_id=init_integration.entry_id,
    )
    assert device is not None
    assert events[0].data == {
        "device_id": device.id,
        "user_id": TEST_USER_ID,
        "league_id": LEAGUE_ID,
        "league": "Wombats League",
        "player_id": "11560",
        "player": "Caleb Williams",
        "position": "QB",
        "team": "CHI",
        "picture": "https://sleepercdn.com/content/nfl/players/thumb/11560.jpg",
        "roster_id": 1,
        "is_mine": True,
        "previous_points": 17.66,
        "points": 24.0,
        "delta": 6.34,
        "week": 1,
        "matchup_id": 6,
    }
    assert events[1].data["is_mine"] is False
    assert events[1].data["player"] == "Will Reichard"
    assert events[1].data["roster_id"] == 5
    assert init_integration.runtime_data.data.leagues[LEAGUE_ID].points_changes

    # Same points again: nothing new.
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    assert len(events) == 2
    assert not init_integration.runtime_data.data.leagues[LEAGUE_ID].points_changes


async def test_new_week_starts_fresh(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a week change does not report every player dropping to zero."""
    events = async_capture_events(hass, EVENT_PLAYER_SCORED)
    sport_state = init_integration.runtime_data.data.state
    mock_client.get_sport_state.return_value = replace(sport_state, week=2)
    mock_client.get_matchups.side_effect = lambda league_id, week: tuple(
        replace(
            matchup,
            points=0.0,
            players_points=MappingProxyType(dict.fromkeys(matchup.players, 0.0)),
        )
        for matchup in matchups_for(league_id, 1)
    )
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)

    assert not events


async def test_unknown_player_and_missing_device(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test events for a player not in the list and without a league device."""
    events = async_capture_events(hass, EVENT_PLAYER_SCORED)
    device = device_registry.async_get_device_by_identifier(
        league_device_identifier(TEST_USER_ID, LEAGUE_ID),
        config_entry_id=init_integration.entry_id,
    )
    assert device is not None
    device_registry.async_remove_device(device.id)
    await hass.async_block_till_done()

    # A starter unknown to the player list, with points appearing for the
    # first time, then jumping; plus an empty slot, which is ignored.
    def matchups(league_id: str, week: int) -> tuple[SleeperMatchup, ...]:
        return tuple(
            replace(
                matchup,
                starters=("ghost", "0", *matchup.starters[2:]),
                players_points=MappingProxyType(
                    {**matchup.players_points, "ghost": 9.0}
                ),
            )
            if matchup.roster_id == 1
            else matchup
            for matchup in matchups_with_points({(1, "11560"): 0.0})
        )

    mock_client.get_matchups.side_effect = matchups
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    assert not events  # first sighting of "ghost" is the baseline

    mock_client.get_matchups.side_effect = lambda league_id, week: tuple(
        replace(
            matchup,
            players_points=MappingProxyType({**matchup.players_points, "ghost": 15.5}),
        )
        if matchup.roster_id == 1
        else matchup
        for matchup in matchups(league_id, week)
    )
    await async_poll(hass, freezer, IDLE_SEASON_UPDATE_INTERVAL)
    assert len(events) == 1
    assert events[0].data["device_id"] is None
    assert events[0].data["player"] == "ghost"
    assert events[0].data["position"] is None
    assert events[0].data["picture"] is None
    assert events[0].data["delta"] == 6.5


def test_logbook_description(hass: HomeAssistant) -> None:
    """Test the logbook renders scoring events as readable lines."""
    described: dict[tuple[str, str], Any] = {}

    def register(domain: str, event_type: str, describe: Any) -> None:
        described[(domain, event_type)] = describe

    async_describe_events(hass, register)
    describe = described[(DOMAIN, EVENT_PLAYER_SCORED)]

    entry = describe(
        Event(
            EVENT_PLAYER_SCORED,
            {
                "player": "Caleb Williams",
                "delta": 6.34,
                "points": 24.0,
                "is_mine": True,
                "league": "Wombats League",
            },
        )
    )
    assert entry["name"] == "Caleb Williams"
    assert entry["message"] == "scored 6.34 points (24 total, Wombats League)"
    assert entry["icon"] == "mdi:football"

    entry = describe(
        Event(
            EVENT_PLAYER_SCORED,
            {
                "player": "Will Reichard",
                "delta": -3.0,
                "points": 6.0,
                "is_mine": False,
                "league": "Wombats League",
            },
        )
    )
    assert (
        entry["message"] == "lost 3 points for the opponent (6 total, Wombats League)"
    )
