"""Config flow for DirecTV."""
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from directv import DIRECTV, DIRECTVError
import voluptuous as vol

from homeassistant.components.ssdp import ATTR_SSDP_LOCATION, ATTR_UPNP_SERIAL
from homeassistant.config_entries import CONN_CLASS_LOCAL_POLL, ConfigFlow
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import (
    ConfigType,
    DiscoveryInfoType,
    HomeAssistantType,
)

from .const import CONF_RECEIVER_ID
from .const import DOMAIN  # pylint: disable=unused-import

_LOGGER = logging.getLogger(__name__)

ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_UNKNOWN = "unknown"


async def validate_input(hass: HomeAssistantType, data: dict) -> Dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    session = async_get_clientsession(hass)
    directv = DIRECTV(data[CONF_HOST], session=session)
    device = await directv.update()

    return {CONF_RECEIVER_ID: device.info.receiver_id}


class DirecTVConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for DirecTV."""

    VERSION = 1
    CONNECTION_CLASS = CONN_CLASS_LOCAL_POLL

    def __init__(self):
        """Set up the instance."""
        self.discovery_info = {}

    async def async_step_user(
        self, user_input: Optional[ConfigType] = None
    ) -> Dict[str, Any]:
        """Handle a flow initiated by the user."""
        if user_input is None:
            return self._show_setup_form()

        try:
            info = await validate_input(self.hass, user_input)
        except DIRECTVError:
            return self._show_setup_form({"base": ERROR_CANNOT_CONNECT})
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            return self.async_abort(reason=ERROR_UNKNOWN)

        user_input[CONF_RECEIVER_ID] = info[CONF_RECEIVER_ID]

        await self.async_set_unique_id(user_input[CONF_RECEIVER_ID])
        self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})

        return self.async_create_entry(title=user_input[CONF_HOST], data=user_input)

    async def async_step_ssdp(
        self, discovery_info: DiscoveryInfoType
    ) -> Dict[str, Any]:
        """Handle SSDP discovery."""
        host = urlparse(discovery_info[ATTR_SSDP_LOCATION]).hostname
        receiver_id = None

        if discovery_info.get(ATTR_UPNP_SERIAL):
            receiver_id = discovery_info[ATTR_UPNP_SERIAL][4:]  # strips off RID-

        self.context.update({"title_placeholders": {"name": host}})

        self.discovery_info.update(
            {CONF_HOST: host, CONF_NAME: host, CONF_RECEIVER_ID: receiver_id}
        )

        try:
            info = await validate_input(self.hass, self.discovery_info)
        except DIRECTVError:
            return self.async_abort(reason=ERROR_CANNOT_CONNECT)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            return self.async_abort(reason=ERROR_UNKNOWN)

        self.discovery_info[CONF_RECEIVER_ID] = info[CONF_RECEIVER_ID]

        await self.async_set_unique_id(self.discovery_info[CONF_RECEIVER_ID])
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: self.discovery_info[CONF_HOST]}
        )

        return await self.async_step_ssdp_confirm()

    async def async_step_ssdp_confirm(
        self, user_input: ConfigType = None
    ) -> Dict[str, Any]:
        """Handle a confirmation flow initiated by SSDP."""
        if user_input is None:
            return self.async_show_form(
                step_id="ssdp_confirm",
                description_placeholders={"name": self.discovery_info[CONF_NAME]},
                errors={},
            )

        return self.async_create_entry(
            title=self.discovery_info[CONF_NAME],
            data=self.discovery_info,
        )

    def _show_setup_form(self, errors: Optional[Dict] = None) -> Dict[str, Any]:
        """Show the setup form to the user."""
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors or {},
        )
