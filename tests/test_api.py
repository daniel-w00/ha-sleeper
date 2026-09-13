"""Tests for the Sleeper API client."""

from __future__ import annotations

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.sleeper.api import (
    BASE_URL,
    SleeperClient,
    SleeperConnectionError,
    SleeperNotFoundError,
)

JSON_HEADERS = {"Content-Type": "application/json"}

USER_JSON = {
    "user_id": "123456789",
    "username": "testuser",
    "display_name": "Test User",
    "avatar": "abc",
}

STATE_JSON = {
    "week": 3,
    "season": "2026",
    "season_type": "regular",
    "display_week": 3,
    "league_season": "2026",
}


@pytest.fixture
async def client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> SleeperClient:
    """Return a client bound to Home Assistant's mocked session.

    ``aioclient_mock`` must be requested first so the session it patches in
    is the one the client receives.
    """
    return SleeperClient(async_get_clientsession(hass), sport="nfl")


async def test_get_user(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching a user."""
    aioclient_mock.get(f"{BASE_URL}/user/testuser", json=USER_JSON)

    user = await client.get_user("testuser")

    assert user.user_id == "123456789"
    assert user.username == "testuser"
    assert user.display_name == "Test User"
    assert user.avatar == "abc"


async def test_get_user_without_display_name(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test the username is used when no display name is set."""
    aioclient_mock.get(
        f"{BASE_URL}/user/testuser",
        json={"user_id": 1, "username": "testuser", "display_name": None},
    )

    user = await client.get_user("testuser")

    assert user.user_id == "1"
    assert user.display_name == "testuser"
    assert user.avatar is None


async def test_get_user_null_body(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test Sleeper's 200 + null answer for unknown users."""
    aioclient_mock.get(f"{BASE_URL}/user/nobody", text="null", headers=JSON_HEADERS)

    with pytest.raises(SleeperNotFoundError):
        await client.get_user("nobody")


async def test_http_404(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a 404 maps to not found."""
    aioclient_mock.get(f"{BASE_URL}/user/nobody", status=404)

    with pytest.raises(SleeperNotFoundError):
        await client.get_user("nobody")


async def test_http_error(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a server error maps to a connection error."""
    aioclient_mock.get(f"{BASE_URL}/user/testuser", status=500)

    with pytest.raises(SleeperConnectionError):
        await client.get_user("testuser")


async def test_client_error(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a transport error maps to a connection error."""
    aioclient_mock.get(f"{BASE_URL}/user/testuser", exc=ClientError("boom"))

    with pytest.raises(SleeperConnectionError):
        await client.get_user("testuser")


async def test_timeout(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a timeout maps to a connection error."""
    aioclient_mock.get(f"{BASE_URL}/user/testuser", exc=TimeoutError())

    with pytest.raises(SleeperConnectionError):
        await client.get_user("testuser")


async def test_get_sport_state(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching the sport state."""
    aioclient_mock.get(f"{BASE_URL}/state/nfl", json=STATE_JSON)

    state = await client.get_sport_state()

    assert client.sport == "nfl"
    assert state.week == 3
    assert state.display_week == 3
    assert state.season == "2026"
    assert state.season_type == "regular"
    assert state.league_season == "2026"


async def test_get_sport_state_defaults(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test optional state fields fall back to the required ones."""
    aioclient_mock.get(
        f"{BASE_URL}/state/nfl",
        json={"week": 1, "season": "2026", "season_type": "pre"},
    )

    state = await client.get_sport_state()

    assert state.display_week == 1
    assert state.league_season == "2026"


async def test_get_sport_state_null(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a null state answer maps to not found."""
    aioclient_mock.get(f"{BASE_URL}/state/nfl", text="null", headers=JSON_HEADERS)

    with pytest.raises(SleeperNotFoundError):
        await client.get_sport_state()
