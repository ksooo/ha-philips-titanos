"""The Philips Titan OS TV integration."""

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SystemInfo, TitanOSClient, TitanOSError
from .const import CONF_AUTH_KEY, CONF_DEVICE_ID
from .coordinator import TitanOSConfigEntry, TitanOSCoordinator

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.MEDIA_PLAYER,
    Platform.REMOTE,
]


async def async_setup_entry(hass: HomeAssistant, entry: TitanOSConfigEntry) -> bool:
    """Set up a TV from a config entry."""
    client = TitanOSClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_DEVICE_ID],
        entry.data[CONF_AUTH_KEY],
    )
    try:
        system = SystemInfo.from_json(await client.get_system())
    except TitanOSError as err:
        raise ConfigEntryNotReady(str(err)) from err
    coordinator = TitanOSCoordinator(hass, entry, client, system)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TitanOSConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
