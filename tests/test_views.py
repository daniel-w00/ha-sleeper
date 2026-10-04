"""Tests for the view that serves square player pictures."""

from __future__ import annotations

import base64
from http import HTTPStatus
from unittest.mock import patch

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.sleeper.api import PNG_SIGNATURE
from custom_components.sleeper.const import DOMAIN

CDN_URL = "https://sleepercdn.com/content/nfl/players/thumb/{}.jpg"
PICTURE = PNG_SIGNATURE + b"headshot"


@pytest.fixture
async def setup_view(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Set up the integration, which registers the view.

    ``aioclient_mock`` must be requested first so the view gets the session
    it patches in.
    """
    assert await async_setup_component(hass, DOMAIN, {})


@pytest.mark.usefixtures("setup_view")
async def test_player_picture(
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test a headshot is served as a square SVG, without credentials."""
    aioclient_mock.get(CDN_URL.format("11560"), content=PICTURE)
    client = await hass_client_no_auth()

    response = await client.get("/api/sleeper/player_picture/nfl/11560")

    assert response.status == HTTPStatus.OK
    assert response.content_type == "image/svg+xml"
    assert response.headers["Cache-Control"] == "public, max-age=86400"
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    svg = await response.text()
    assert 'viewBox="0 0 1 1"' in svg
    assert 'preserveAspectRatio="xMidYMid slice"' in svg
    assert f"data:image/png;base64,{base64.b64encode(PICTURE).decode()}" in svg

    # The second request is answered from the cache.
    response = await client.get("/api/sleeper/player_picture/nfl/11560")
    assert response.status == HTTPStatus.OK
    assert await response.text() == svg
    assert aioclient_mock.call_count == 1


@pytest.mark.usefixtures("setup_view")
async def test_player_picture_missing(
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test a player without a headshot is not found, and remembered."""
    aioclient_mock.get(CDN_URL.format("99999999"), status=403)
    client = await hass_client_no_auth()

    for _ in range(2):
        response = await client.get("/api/sleeper/player_picture/nfl/99999999")
        assert response.status == HTTPStatus.NOT_FOUND
    assert aioclient_mock.call_count == 1


@pytest.mark.usefixtures("setup_view")
async def test_player_picture_connection_error(
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test a CDN failure is reported and not remembered."""
    aioclient_mock.get(CDN_URL.format("11560"), exc=ClientError("boom"))
    client = await hass_client_no_auth()

    for _ in range(2):
        response = await client.get("/api/sleeper/player_picture/nfl/11560")
        assert response.status == HTTPStatus.BAD_GATEWAY
    assert aioclient_mock.call_count == 2


@pytest.mark.usefixtures("setup_view")
@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/api/sleeper/player_picture/nfl/PIT", HTTPStatus.NOT_FOUND),
        ("/api/sleeper/player_picture/NFL/11560", HTTPStatus.NOT_FOUND),
        ("/api/sleeper/player_picture/nfl/12345678901", HTTPStatus.NOT_FOUND),
        # Home Assistant's security filter rejects this one before routing.
        (
            "/api/sleeper/player_picture/nfl/..%2F..%2Favatars%2Fx",
            HTTPStatus.BAD_REQUEST,
        ),
    ],
)
async def test_player_picture_invalid_path(
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    path: str,
    status: HTTPStatus,
) -> None:
    """Test only numeric player IDs of a lowercase sport reach the CDN."""
    client = await hass_client_no_auth()

    response = await client.get(path)

    assert response.status == status
    assert aioclient_mock.call_count == 0


@pytest.mark.usefixtures("setup_view")
async def test_player_picture_cache_size(
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test the least recently used picture leaves a full cache."""
    for player_id in ("1", "2"):
        aioclient_mock.get(CDN_URL.format(player_id), content=PICTURE)
    client = await hass_client_no_auth()

    with patch("custom_components.sleeper.views.CACHE_SIZE", 1):
        for player_id in ("1", "2", "1"):
            response = await client.get(f"/api/sleeper/player_picture/nfl/{player_id}")
            assert response.status == HTTPStatus.OK
    assert aioclient_mock.call_count == 3
