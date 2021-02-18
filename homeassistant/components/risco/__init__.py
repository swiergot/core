"""The Risco integration."""
import asyncio
from datetime import timedelta
import logging
from time import monotonic

from pyrisco import CannotConnectError, OperationError, RiscoAPI, UnauthorizedError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_PIN,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CACHE_EXPIRY_TIME,
    DATA_COORDINATOR,
    DEFAULT_CACHE_EXPIRY_TIME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENTS_COORDINATOR,
)

PLATFORMS = ["alarm_control_panel", "binary_sensor", "sensor"]
UNDO_UPDATE_LISTENER = "undo_update_listener"
LAST_EVENT_STORAGE_VERSION = 1
LAST_EVENT_TIMESTAMP_KEY = "last_event_timestamp"
_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Risco component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Risco from a config entry."""
    data = entry.data
    risco = RiscoAPI(data[CONF_USERNAME], data[CONF_PASSWORD], data[CONF_PIN])
    try:
        await risco.login(async_get_clientsession(hass))
    except CannotConnectError as error:
        raise ConfigEntryNotReady() from error
    except UnauthorizedError:
        _LOGGER.exception("Failed to login to Risco cloud")
        return False

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    cache_expiry_time = entry.options.get(
        CONF_CACHE_EXPIRY_TIME, DEFAULT_CACHE_EXPIRY_TIME
    )
    coordinator = RiscoDataUpdateCoordinator(
        hass, risco, scan_interval, cache_expiry_time
    )
    await coordinator.async_refresh()
    events_coordinator = RiscoEventsDataUpdateCoordinator(
        hass, risco, entry.entry_id, 60
    )

    undo_listener = entry.add_update_listener(_update_listener)

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
        UNDO_UPDATE_LISTENER: undo_listener,
        EVENTS_COORDINATOR: events_coordinator,
    }

    async def start_platforms():
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_setup(entry, component)
                for component in PLATFORMS
            ]
        )
        await events_coordinator.async_refresh()

    hass.async_create_task(start_platforms())

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )

    if unload_ok:
        hass.data[DOMAIN][entry.entry_id][UNDO_UPDATE_LISTENER]()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


class RiscoDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching risco data."""

    def __init__(self, hass, risco, scan_interval, cache_expiry_time):
        """Initialize global risco data updater."""
        self.risco = risco
        self.cache = None
        self.cache_time = None
        self.cache_expiry_time = cache_expiry_time
        self.last_error_msg = None
        interval = timedelta(seconds=scan_interval)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=interval,
        )

    async def _async_update_data(self):
        """Fetch data from risco."""
        try:
            self.cache = await self.risco.get_state()
            self.cache_time = monotonic()
            if self.last_error_msg:
                _LOGGER.debug("No need to use cache anymore")
                self.last_error_msg = None
            return self.cache
        except (CannotConnectError, UnauthorizedError, OperationError) as error:
            if self.cache_expiry_time > 0 and self.cache:
                cache_age = monotonic() - self.cache_time
                if cache_age > self.cache_expiry_time:
                    _LOGGER.debug("Cache has expired")
                else:
                    error_msg = f"Returning cached state due to {error.__class__.__name__}: {error}"
                    if error_msg != self.last_error_msg:
                        _LOGGER.debug(error_msg)
                        self.last_error_msg = error_msg
                    return self.cache
            self.last_error_msg = None
            raise UpdateFailed(error) from error


class RiscoEventsDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching risco data."""

    def __init__(self, hass, risco, eid, scan_interval):
        """Initialize global risco data updater."""
        self.risco = risco
        self._store = Store(
            hass, LAST_EVENT_STORAGE_VERSION, f"risco_{eid}_last_event_timestamp"
        )
        interval = timedelta(seconds=scan_interval)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_events",
            update_interval=interval,
        )

    async def _async_update_data(self):
        """Fetch data from risco."""
        last_store = await self._store.async_load() or {}
        last_timestamp = last_store.get(
            LAST_EVENT_TIMESTAMP_KEY, "2020-01-01T00:00:00Z"
        )
        try:
            events = await self.risco.get_events(last_timestamp, 10)
        except (CannotConnectError, UnauthorizedError, OperationError) as error:
            raise UpdateFailed(error) from error

        if len(events) > 0:
            await self._store.async_save({LAST_EVENT_TIMESTAMP_KEY: events[0].time})

        return events
