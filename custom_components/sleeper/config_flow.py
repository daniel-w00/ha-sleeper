"""Config flow for the Sleeper integration."""

from __future__ import annotations

import logging
from typing import Any, override

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import SleeperClient, SleeperConnectionError, SleeperNotFoundError
from .const import DEFAULT_SPORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str})


class SleeperConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sleeper."""

    VERSION = 1

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step: ask for the Sleeper username."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = SleeperClient(
                async_get_clientsession(self.hass), sport=DEFAULT_SPORT
            )
            try:
                user = await client.get_user(user_input[CONF_USERNAME].strip())
            except SleeperNotFoundError:
                errors["base"] = "user_not_found"
            except SleeperConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user.user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user.display_name,
                    data={CONF_USERNAME: user.username},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
