"""Shared fixtures for the Sleeper integration tests."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.syrupy import (
    HomeAssistantSnapshotExtension,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.sleeper.api import (
    SleeperDraft,
    SleeperDraftPick,
    SleeperLeague,
    SleeperLeagueUser,
    SleeperMatchup,
    SleeperPlayer,
    SleeperRoster,
    SleeperSportState,
    SleeperTradedPick,
    SleeperUser,
)
from custom_components.sleeper.const import DOMAIN

TEST_USERNAME = "testuser"
TEST_USER_ID = "123456789"
# The two leagues in the fixtures: a 12-team FAAB league in season with a
# complete draft, and a 4-team league whose league list entry still says
# pre-draft while its slow draft is running (5 of 60 picks made).
LEAGUE_ID = "1392910061486997504"
PREDRAFT_LEAGUE_ID = "1397743357073084416"
DRAFT_ID = "1392910064494333952"
DRAFTING_DRAFT_ID = "1397743359191212032"

FIXTURES = Path(__file__).parent / "fixtures"


def load_json_fixture(name: str) -> Any:
    """Load a JSON fixture captured from the Sleeper API."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def async_poll(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    """Advance time and let the coordinator poll."""
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def players_fixture() -> dict[str, SleeperPlayer]:
    """Return the fixture players, as the client would deliver them."""
    return {
        player_id: SleeperPlayer.from_json(item)
        for player_id, item in load_json_fixture("players.json").items()
    }


def rosters_for(league_id: str) -> tuple[SleeperRoster, ...]:
    """Return the fixture rosters of a league, as the client would."""
    name = "rosters.json" if league_id == LEAGUE_ID else "rosters_predraft.json"
    return tuple(SleeperRoster.from_json(item) for item in load_json_fixture(name))


def matchups_for(league_id: str, week: int) -> tuple[SleeperMatchup, ...]:
    """Return the fixture matchups of a league, as the client would."""
    if league_id != LEAGUE_ID:
        return ()
    return tuple(
        SleeperMatchup.from_json(item) for item in load_json_fixture("matchups_1.json")
    )


def _draft_fixture(draft_id: str) -> str:
    """Return the fixture name suffix of a draft."""
    return "complete" if draft_id == DRAFT_ID else "drafting"


def draft_for(draft_id: str) -> SleeperDraft:
    """Return a fixture draft, as the client would."""
    return SleeperDraft.from_json(
        load_json_fixture(f"draft_{_draft_fixture(draft_id)}.json")
    )


def draft_picks_for(draft_id: str) -> tuple[SleeperDraftPick, ...]:
    """Return the fixture picks of a draft, as the client would."""
    return tuple(
        SleeperDraftPick.from_json(item)
        for item in load_json_fixture(f"draft_picks_{_draft_fixture(draft_id)}.json")
    )


def traded_picks_for(draft_id: str) -> tuple[SleeperTradedPick, ...]:
    """Return the fixture traded picks of a draft, as the client would."""
    return tuple(
        SleeperTradedPick.from_json(item)
        for item in load_json_fixture(f"traded_picks_{_draft_fixture(draft_id)}.json")
    )


def drafting_picks(count: int) -> tuple[SleeperDraftPick, ...]:
    """Return the first ``count`` picks of the running test league draft.

    The five fixture picks come first. Further picks are generated along the
    snake order, the slot map and the traded picks of the draft, with players
    from the player list fixture, so the account (slot 2, roster 1) is the
    picker whenever the order says so.
    """
    draft = draft_for(DRAFTING_DRAFT_ID)
    picks = list(draft_picks_for(DRAFTING_DRAFT_ID))[:count]
    owners = {
        roster.roster_id: roster.owner_id for roster in rosters_for(PREDRAFT_LEAGUE_ID)
    }
    players = list(load_json_fixture("players.json").values())
    for pick_no in range(len(picks) + 1, count + 1):
        slot = draft.slot_of(pick_no)
        assert slot is not None
        roster_id = draft.slot_to_roster_id[slot]
        round_no = draft.round_of(pick_no)
        for trade in traded_picks_for(DRAFTING_DRAFT_ID):
            if trade.round == round_no and trade.roster_id == roster_id:
                roster_id = trade.owner_id
        player = players[pick_no % len(players)]
        picks.append(
            SleeperDraftPick.from_json(
                {
                    "draft_id": DRAFTING_DRAFT_ID,
                    "draft_slot": slot,
                    "pick_no": pick_no,
                    "round": round_no,
                    "roster_id": roster_id,
                    "picked_by": owners[roster_id],
                    "player_id": player["player_id"],
                    "metadata": player,
                }
            )
        )
    return tuple(picks)


def matchups_with_points(
    changes: dict[tuple[int, str], float],
) -> tuple[SleeperMatchup, ...]:
    """Return the fixture matchups with some player points replaced."""
    result = []
    for matchup in matchups_for(LEAGUE_ID, 1):
        points = dict(matchup.players_points)
        for (roster_id, player_id), value in changes.items():
            if roster_id == matchup.roster_id:
                points[player_id] = value
        result.append(replace(matchup, players_points=MappingProxyType(points)))
    return tuple(result)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading of custom integrations in every test."""


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Return the snapshot assertion with the Home Assistant extension.

    The plugin ships this override too, but syrupy's own fixture can win the
    plugin ordering; defining it here makes the extension apply reliably.
    """
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Enable entities that are disabled by default (same as in core's tests)."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        PropertyMock(return_value=True),
    ):
        yield


@pytest.fixture
def mock_user() -> SleeperUser:
    """Return a Sleeper user as the API would deliver it."""
    return SleeperUser(
        user_id=TEST_USER_ID,
        username=TEST_USERNAME,
        display_name="Test User",
        avatar=None,
    )


@pytest.fixture
def mock_sport_state() -> SleeperSportState:
    """Return a sport state as the API would deliver it."""
    return SleeperSportState.from_json(load_json_fixture("state.json"))


@pytest.fixture
def mock_leagues() -> tuple[SleeperLeague, ...]:
    """Return the leagues of the test user as the API would deliver them."""
    return tuple(
        SleeperLeague.from_json(item) for item in load_json_fixture("leagues.json")
    )


@pytest.fixture
def mock_client(
    mock_user: SleeperUser,
    mock_sport_state: SleeperSportState,
    mock_leagues: tuple[SleeperLeague, ...],
) -> Generator[MagicMock]:
    """Patch the Sleeper client in the config flow and the coordinator."""
    league_users = tuple(
        SleeperLeagueUser.from_json(item) for item in load_json_fixture("users.json")
    )
    with (
        patch(
            "custom_components.sleeper.config_flow.SleeperClient", autospec=True
        ) as flow_client,
        patch(
            "custom_components.sleeper.coordinator.SleeperClient",
            new=flow_client,
        ),
        patch(
            "custom_components.sleeper.players.SleeperClient",
            new=flow_client,
        ),
    ):
        client = flow_client.return_value
        client.get_user = AsyncMock(return_value=mock_user)
        client.get_sport_state = AsyncMock(return_value=mock_sport_state)
        client.get_user_leagues = AsyncMock(return_value=mock_leagues)
        client.get_league_users = AsyncMock(return_value=league_users)
        client.get_rosters = AsyncMock(side_effect=rosters_for)
        client.get_matchups = AsyncMock(side_effect=matchups_for)
        client.get_players = AsyncMock(return_value=players_fixture())
        client.get_draft = AsyncMock(side_effect=draft_for)
        client.get_draft_picks = AsyncMock(side_effect=draft_picks_for)
        client.get_draft_traded_picks = AsyncMock(side_effect=traded_picks_for)
        yield client


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for the test user."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test User",
        unique_id=TEST_USER_ID,
        data={CONF_USERNAME: TEST_USERNAME},
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> MockConfigEntry:
    """Set up the integration with the mocked client."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
