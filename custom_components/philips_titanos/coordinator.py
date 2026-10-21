"""Coordinator for the Philips Titan OS TV integration."""

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import InvalidAuth, SystemInfo, TitanOSClient, TitanOSError, wake_on_lan
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=10)
# Cast comes up 15 to 30 seconds after Wake-on-LAN and goes down about 11
# seconds after standby.
POWER_ON_GRACE = 45
POWER_OFF_GRACE = 20


@dataclass(frozen=True, slots=True)
class TitanOSData:
    """State of the TV."""

    is_on: bool
    activity: str | None
    muted: bool | None = None


@dataclass(frozen=True, slots=True)
class Channel:
    """A channel the TV can tune to."""

    ccid: int
    name: str
    list_id: str


type TitanOSConfigEntry = ConfigEntry[TitanOSCoordinator]


class TitanOSCoordinator(DataUpdateCoordinator[TitanOSData]):
    """Poll power and activity of the TV."""

    config_entry: TitanOSConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TitanOSConfigEntry,
        client: TitanOSClient,
        system: SystemInfo,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.system = system
        self.channels: list[Channel] = []
        self.last_channel: Channel | None = None
        self._expected_power: tuple[bool, float] | None = None

    async def async_command(self, command: Awaitable[Any]) -> None:
        """Run a command and report its failure to the user."""
        try:
            await command
        except (TitanOSError, OSError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    async def async_turn_on(self) -> None:
        """Wake the TV."""
        if not (mac := self.config_entry.data.get(CONF_MAC)):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_mac"
            )
        await self.async_command(wake_on_lan(mac))
        self.expect_power(True)

    async def async_turn_off(self) -> None:
        """Put the TV into standby."""
        # Standby is the remote's power key and may wake a TV in standby.
        if not self.data.is_on:
            return
        await self.async_command(self.client.send_key("Standby"))
        self.expect_power(False)

    def expect_power(self, is_on: bool) -> None:
        """Report this power state until the TV confirms it or a grace ends."""
        grace = POWER_ON_GRACE if is_on else POWER_OFF_GRACE
        self._expected_power = (is_on, time.monotonic() + grace)
        if self.data:
            self.async_set_updated_data(
                TitanOSData(is_on=is_on, activity=self.data.activity)
            )

    def _is_on(self, measured: bool) -> bool:
        if self._expected_power is None:
            return measured
        expected, deadline = self._expected_power
        if measured == expected or time.monotonic() > deadline:
            self._expected_power = None
            return measured
        return expected

    async def _async_update_data(self) -> TitanOSData:
        is_on = self._is_on(await self.client.is_on())
        if not is_on:
            return TitanOSData(is_on=False, activity=None)
        try:
            activity = await self.client.get_current_activity()
            muted = await self.client.is_muted()
            if not self.channels:
                self.channels = await self._async_fetch_channels()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed from err
        except TitanOSError as err:
            raise UpdateFailed(str(err)) from err
        return TitanOSData(is_on=True, activity=activity, muted=muted)

    async def _async_fetch_channels(self) -> list[Channel]:
        db: dict[str, Any] = await self.client.get_channel_db() or {}
        channels: list[Channel] = []
        for channel_list in db.get("channelLists", []):
            list_id = channel_list["id"]
            channels.extend(
                Channel(ccid=int(c["ccid"]), name=c["name"], list_id=list_id)
                for c in await self.client.get_channels(list_id)
            )
        return channels
