"""The Philips Titan OS TV integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TitanOSClient
from .const import CONF_AUTH_KEY, CONF_DEVICE_ID

PLATFORMS: list[Platform] = []

type TitanOSConfigEntry = ConfigEntry[TitanOSClient]


async def async_setup_entry(hass: HomeAssistant, entry: TitanOSConfigEntry) -> bool:
    """Set up a TV from a config entry."""
    entry.runtime_data = TitanOSClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_DEVICE_ID],
        entry.data[CONF_AUTH_KEY],
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TitanOSConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
