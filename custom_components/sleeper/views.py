"""HTTP view that serves player headshots as square pictures."""

from __future__ import annotations

import base64
from collections import OrderedDict
from http import HTTPStatus
import logging

from aiohttp import ClientSession, hdrs, web
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.http import KEY_HASS, HomeAssistantView

from .api import SleeperClient, SleeperConnectionError, SleeperNotFoundError
from .const import DOMAIN
from .coordinator import SleeperConfigEntry

_LOGGER = logging.getLogger(__name__)

PLAYER_PICTURE_PATH = "/api/sleeper/player_picture/{sport}/{player_id}"
# Pictures kept in memory (about 30 kB each). Only the players currently shown
# as entity pictures are requested, so this is plenty.
CACHE_SIZE = 200
# Headshots change between seasons at most.
CACHE_CONTROL = "public, max-age=86400"
# The SVG only embeds the picture; it must not load or run anything else when
# a browser opens it directly.
CONTENT_SECURITY_POLICY = "default-src 'none'; img-src data:; style-src 'none'"


def player_picture_path(sport: str, player_id: str) -> str:
    """Return the path of a player's square picture on this instance."""
    return PLAYER_PICTURE_PATH.format(sport=sport, player_id=player_id)


@callback
def async_is_pictured(hass: HomeAssistant, sport: str, player_id: str) -> bool:
    """Return whether an entity of any account shows a player's picture."""
    entries: list[SleeperConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    return any(
        entry.runtime_data.sport == sport and player_id in data.pictured_players
        for entry in entries
        for data in entry.runtime_data.data.leagues.values()
    )


def square_svg(picture: bytes, media_type: str) -> bytes:
    """Wrap a picture in a square SVG that shows its centred square.

    ``xMidYMid slice`` scales the picture to cover the square and cuts off
    equal parts on both sides, whatever the picture's aspect ratio.
    """
    data = base64.b64encode(picture).decode("ascii")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"'
        ' viewBox="0 0 1 1">'
        '<image width="1" height="1" preserveAspectRatio="xMidYMid slice"'
        f' href="data:{media_type};base64,{data}"/>'
        "</svg>"
    ).encode()


class SleeperPlayerPictureView(HomeAssistantView):
    """Serve a player's headshot cropped to a centred square.

    Sleeper's headshots are landscape. The frontend shows entity pictures as
    round badges whose background covers the badge but is anchored at the
    top left, which moves the player's head off centre. Square pictures look
    the same however they are anchored.

    Browsers load entity pictures without credentials, so the view does not
    require authentication. It only passes on what Sleeper's CDN serves
    publicly, and only requests the pictures of players an entity shows
    right now; anything else is not found. With the answers remembered,
    missing pictures included, it cannot be used to hammer the CDN.
    """

    url = "/api/sleeper/player_picture/{sport:[a-z]+}/{player_id:[0-9]{1,10}}"
    name = "api:sleeper:player_picture"
    requires_auth = False

    def __init__(self, session: ClientSession) -> None:
        """Initialize the view. Performs no I/O."""
        self._session = session
        self._cache: OrderedDict[tuple[str, str], bytes | None] = OrderedDict()

    async def get(
        self, request: web.Request, sport: str, player_id: str
    ) -> web.Response:
        """Return the square picture of a player."""
        key = (sport, player_id)
        if key in self._cache:
            self._cache.move_to_end(key)
            svg = self._cache[key]
        elif not async_is_pictured(request.app[KEY_HASS], sport, player_id):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        else:
            client = SleeperClient(self._session, sport=sport)
            try:
                picture, media_type = await client.get_player_picture(player_id)
            except SleeperNotFoundError:
                svg = None
            except SleeperConnectionError as err:
                _LOGGER.debug("Error fetching the picture of %s: %s", player_id, err)
                return web.Response(status=HTTPStatus.BAD_GATEWAY)
            else:
                svg = square_svg(picture, media_type)
            self._cache[key] = svg
            if len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        if svg is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.Response(
            body=svg,
            content_type="image/svg+xml",
            headers={
                hdrs.CACHE_CONTROL: CACHE_CONTROL,
                "Content-Security-Policy": CONTENT_SECURITY_POLICY,
            },
        )
