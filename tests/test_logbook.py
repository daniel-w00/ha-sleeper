"""Tests for the scoring and draft events and their logbook descriptions."""

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
    DRAFT_UPDATE_INTERVAL,
    EVENT_DRAFT_ON_THE_CLOCK,
    EVENT_DRAFT_PICK,
    EVENT_PLAYER_SCORED,
    IDLE_SEASON_UPDATE_INTERVAL,
)
from custom_components.sleeper.coordinator import league_device_identifier
from custom_components.sleeper.logbook import async_describe_events

from .conftest import (
    DRAFTING_DRAFT_ID,
    LEAGUE_ID,
    PREDRAFT_LEAGUE_ID,
    TEST_USER_ID,
    async_poll,
    draft_for,
    draft_picks_for,
    drafting_picks,
    matchups_for,
    matchups_with_points,
)


def make_picks(hass: HomeAssistant, mock_client: MagicMock, count: int) -> None:
    """Let the running draft have ``count`` picks from the next poll on."""
    mock_client.get_draft_picks.side_effect = lambda draft_id: (
        drafting_picks(count)
        if draft_id == DRAFTING_DRAFT_ID
        else draft_picks_for(draft_id)
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


async def test_draft_events(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test every new pick fires an event, and the clock moving on another."""
    picks = async_capture_events(hass, EVENT_DRAFT_PICK)
    clock = async_capture_events(hass, EVENT_DRAFT_ON_THE_CLOCK)
    device = device_registry.async_get_device_by_identifier(
        league_device_identifier(TEST_USER_ID, PREDRAFT_LEAGUE_ID),
        config_entry_id=init_integration.entry_id,
    )
    assert device is not None

    # The first poll saw five picks: nothing is replayed, nothing happened.
    await async_poll(hass, freezer, DRAFT_UPDATE_INTERVAL)
    assert not picks
    assert not clock

    # Roster 3 picks: one pick event and the account comes on the clock.
    make_picks(hass, mock_client, 6)
    await async_poll(hass, freezer, DRAFT_UPDATE_INTERVAL)
    assert len(picks) == 1
    assert picks[0].data == {
        "device_id": device.id,
        "user_id": TEST_USER_ID,
        "league_id": PREDRAFT_LEAGUE_ID,
        "league": "Test League",
        "draft_id": DRAFTING_DRAFT_ID,
        "pick_no": 6,
        "round": 2,
        "pick_in_round": 2,
        "pick": "2.02",
        "roster_id": 3,
        "picked_by": "user_3",
        "picked_by_user_id": "000000000000000003",
        "player_id": picks[0].data["player_id"],
        "player": "Harold Fannin",
        "position": "TE",
        "team": "CLE",
        "picture": (
            "https://sleepercdn.com/content/nfl/players/thumb/"
            f"{picks[0].data['player_id']}.jpg"
        ),
        "is_mine": False,
        "is_keeper": False,
    }
    assert len(clock) == 1
    assert clock[0].data == {
        "device_id": device.id,
        "user_id": TEST_USER_ID,
        "league_id": PREDRAFT_LEAGUE_ID,
        "league": "Test League",
        "draft_id": DRAFTING_DRAFT_ID,
        "pick_no": 7,
        "round": 2,
        "pick_in_round": 3,
        "pick": "2.03",
        "roster_id": 1,
        "team": "Test Team",
        "picture": "https://sleepercdn.com/uploads/testteam.jpg",
        "is_mine": True,
        "deadline": "2026-09-06T18:00:00+00:00",
        "picks_until_mine": 0,
    }

    # Two picks between polls, the account's and roster 2's, both reported.
    # Roster 2 is up again (round 3 starts at slot 1), and the account's
    # third round pick was traded away, so its next one is in round 4.
    make_picks(hass, mock_client, 8)
    await async_poll(hass, freezer, DRAFT_UPDATE_INTERVAL)
    assert [(e.data["pick"], e.data["is_mine"]) for e in picks[1:]] == [
        ("2.03", True),
        ("2.04", False),
    ]
    assert picks[1].data["picked_by"] == "Test Team"
    assert picks[2].data["picked_by"] == "Team 2"
    assert clock[-1].data["pick"] == "3.01"
    assert clock[-1].data["team"] == "Team 2"
    assert clock[-1].data["is_mine"] is False
    assert clock[-1].data["picks_until_mine"] == 6
    assert (
        init_integration.runtime_data.data.leagues[PREDRAFT_LEAGUE_ID].new_draft_picks
        == drafting_picks(8)[6:]
    )

    # Nothing new: quiet.
    await async_poll(hass, freezer, DRAFT_UPDATE_INTERVAL)
    assert len(picks) == 3
    assert len(clock) == 2
    assert not init_integration.runtime_data.data.leagues[
        PREDRAFT_LEAGUE_ID
    ].new_draft_picks

    # The final picks arrive together with the draft turning complete: the
    # picks are reported, nobody is on the clock any more.
    make_picks(hass, mock_client, 60)
    mock_client.get_draft.side_effect = lambda draft_id: replace(
        draft_for(draft_id), status="complete"
    )
    await async_poll(hass, freezer, DRAFT_UPDATE_INTERVAL)
    assert len(picks) == 3 + 52
    assert picks[-1].data["pick"] == "15.04"
    assert len(clock) == 2

    # The finished draft of the other league never fired anything.
    assert {e.data["league_id"] for e in picks} == {PREDRAFT_LEAGUE_ID}


async def test_draft_events_without_device(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test draft events without a league device, roster and picker."""
    picks = async_capture_events(hass, EVENT_DRAFT_PICK)
    clock = async_capture_events(hass, EVENT_DRAFT_ON_THE_CLOCK)
    device = device_registry.async_get_device_by_identifier(
        league_device_identifier(TEST_USER_ID, PREDRAFT_LEAGUE_ID),
        config_entry_id=init_integration.entry_id,
    )
    assert device is not None
    device_registry.async_remove_device(device.id)
    await hass.async_block_till_done()

    # A pick by nobody in particular, and no draft order any more.
    mock_client.get_draft_picks.side_effect = lambda draft_id: (
        (
            *drafting_picks(5),
            replace(drafting_picks(6)[5], roster_id=None, picked_by=None),
        )
        if draft_id == DRAFTING_DRAFT_ID
        else draft_picks_for(draft_id)
    )
    mock_client.get_draft.side_effect = lambda draft_id: (
        replace(
            draft_for(draft_id),
            draft_order=MappingProxyType({}),
            slot_to_roster_id=MappingProxyType({}),
        )
        if draft_id == DRAFTING_DRAFT_ID
        else draft_for(draft_id)
    )
    await async_poll(hass, freezer, DRAFT_UPDATE_INTERVAL)
    assert len(picks) == 1
    assert picks[0].data["device_id"] is None
    assert picks[0].data["roster_id"] is None
    assert picks[0].data["picked_by"] is None
    assert picks[0].data["is_mine"] is False
    assert len(clock) == 1
    assert clock[0].data["roster_id"] is None
    assert clock[0].data["team"] is None
    assert clock[0].data["picture"] is None
    assert clock[0].data["picks_until_mine"] is None


def test_draft_pick_logbook_description(hass: HomeAssistant) -> None:
    """Test the logbook renders draft picks as readable lines."""
    described: dict[tuple[str, str], Any] = {}

    def register(domain: str, event_type: str, describe: Any) -> None:
        described[(domain, event_type)] = describe

    async_describe_events(hass, register)
    describe = described[(DOMAIN, EVENT_DRAFT_PICK)]
    assert (DOMAIN, EVENT_DRAFT_ON_THE_CLOCK) not in described

    data = {
        "picked_by": "Test Team",
        "roster_id": 1,
        "player": "Jahmyr Gibbs",
        "pick": "1.07",
        "league": "Wombats League",
        "is_keeper": False,
    }
    entry = describe(Event(EVENT_DRAFT_PICK, data))
    assert entry["name"] == "Test Team"
    assert entry["message"] == "picked Jahmyr Gibbs (1.07, Wombats League)"
    assert entry["icon"] == "mdi:clipboard-list-outline"

    entry = describe(
        Event(EVENT_DRAFT_PICK, {**data, "picked_by": None, "is_keeper": True})
    )
    assert entry["name"] == "Roster 1"
    assert entry["message"] == (
        "picked Jahmyr Gibbs as a keeper (1.07, Wombats League)"
    )

    entry = describe(
        Event(EVENT_DRAFT_PICK, {**data, "picked_by": None, "roster_id": None})
    )
    assert entry["name"] == "Someone"
