"""Coordinator for the Philips Titan OS TV integration."""

from collections.abc import Awaitable
from dataclasses import dataclass, replace
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

from .api import (
    InvalidAuth,
    SystemInfo,
    TitanOSClient,
    TitanOSError,
    find_menu_node,
    wake_on_lan,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=10)
# While waiting for the TV to come on, so that Ambilight is restored soon.
WAKE_INTERVAL = timedelta(seconds=2)
# Cast comes up 15 to 30 seconds after Wake-on-LAN and goes down about 11
# seconds after standby.
POWER_ON_GRACE = 45
POWER_OFF_GRACE = 20

# The TV lists no settings for FOLLOW_VIDEO; these are the ones its menu sets.
# Its "AI" style also reads as NATURAL.
VIDEO_SETTINGS = ["STANDARD", "SPORTS", "NATURAL", "GAME"]

# Menu list items by string ID, and the style each sets.
MENU_STYLES = {
    "MLM_PHM_KEY_MAIN_AMBILIGHT_STYLE_VIDEO_1_2K20": ("FOLLOW_VIDEO", "STANDARD"),
    "MLM_PHM_KEY_MAIN_AMBILIGHT_STYLE_VIDEO_3_2K20": ("FOLLOW_VIDEO", "SPORTS"),
    "MLM_PHM_KEY_MAIN_AMBILIGHT_CINEMA": ("FOLLOW_VIDEO", "NATURAL"),
    "MLM_PHM_KEY_MAIN_AMBILIGHT_GAME_2K23": ("FOLLOW_VIDEO", "GAME"),
    "MLM_PHM_KEY_MAIN_AMBILIGHT_STYLE_AUDIO_1_2K20": (
        "FOLLOW_AUDIO",
        "ENERGY_ADAPTIVE_BRIGHTNESS",
    ),
    "MLM_PHM_KEY_MAIN_FOLLOW_AUDIO_STYLE_3_2K23": ("FOLLOW_AUDIO", "PARTY"),
    "MLM_PHM_KEY_MAIN_AMBILIGHT_STYLE_AUDIO_3_2K20": (
        "FOLLOW_AUDIO",
        "RANDOM_PIXEL_FLASH",
    ),
}


@dataclass(frozen=True, slots=True)
class TitanOSData:
    """State of the TV."""

    is_on: bool
    activity: str | None
    muted: bool | None = None
    ambilight: dict[str, Any] | None = None
    ambilight_rgb: tuple[int, int, int] | None = None


@dataclass(frozen=True, slots=True)
class AmbilightStyle:
    """An Ambilight style with one of its settings."""

    style: str
    setting: str

    @property
    def effect(self) -> str:
        """Return the effect name used in Home Assistant."""
        prefix = "video" if self.style == "FOLLOW_VIDEO" else "audio"
        return f"{prefix}_{self.setting.lower()}"


@dataclass(frozen=True, slots=True)
class Channel:
    """A channel the TV can tune to."""

    ccid: int
    name: str
    list_id: str


type TitanOSConfigEntry = ConfigEntry[TitanOSCoordinator]


def _average_rgb(pixels: dict[str, Any]) -> tuple[int, int, int] | None:
    """Return the mean color over all LEDs."""
    colors = [
        (pixel["r"], pixel["g"], pixel["b"])
        for layer in pixels.values()
        for side in layer.values()
        for pixel in side.values()
    ]
    if not colors:
        return None
    return tuple(round(sum(c[i] for c in colors) / len(colors)) for i in range(3))


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
        self.ambilight_styles: list[AmbilightStyle] = []
        self.ambilight_off_node: int | None = None
        # Menu node and list item for each style that has one.
        self.ambilight_menu: dict[AmbilightStyle, tuple[int, int]] = {}
        self._ambilight_style_node: int | None = None
        self.has_ambilight = bool(
            system.featuring.get("jsonfeatures", {}).get("ambilight")
        )
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
        if is_on:
            self.update_interval = WAKE_INTERVAL
        if self.data:
            self.async_set_updated_data(replace(self.data, is_on=is_on))

    def _is_on(self, measured: bool) -> bool:
        if self._expected_power is None:
            return measured
        expected, deadline = self._expected_power
        if measured == expected or time.monotonic() > deadline:
            self._expected_power = None
            self.update_interval = UPDATE_INTERVAL
            return measured
        return expected

    async def _async_update_data(self) -> TitanOSData:
        cast_up = await self.client.is_on()
        # The TV also brings up Cast when it wakes itself with the screen dark.
        woke = cast_up and self._expected_power is not None and self._expected_power[0]
        is_on = self._is_on(cast_up)
        if not is_on:
            return TitanOSData(is_on=False, activity=None)
        try:
            activity = await self.client.get_current_activity()
            muted = await self.client.is_muted()
            if not self.channels:
                self.channels = await self._async_fetch_channels()
            ambilight = rgb = None
            if self.has_ambilight:
                if not self.ambilight_styles:
                    await self._async_fetch_ambilight()
                ambilight = await self.client.get_ambilight_configuration()
                if woke and ambilight is None and await self._async_restore_ambilight():
                    ambilight = await self.client.get_ambilight_configuration()
                if ambilight and ambilight.get("styleName") == "FOLLOW_COLOR":
                    rgb = _average_rgb(await self.client.get_ambilight_processed())
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed from err
        except TitanOSError as err:
            raise UpdateFailed(str(err)) from err
        return TitanOSData(
            is_on=True,
            activity=activity,
            muted=muted,
            ambilight=ambilight,
            ambilight_rgb=rgb,
        )

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

    async def _async_fetch_ambilight(self) -> None:
        styles = await self.client.get_ambilight_styles()
        settings = {s["styleName"]: s.get("algorithms", []) for s in styles}
        result = []
        if "FOLLOW_VIDEO" in settings:
            result += [AmbilightStyle("FOLLOW_VIDEO", s) for s in VIDEO_SETTINGS]
        result += [
            AmbilightStyle("FOLLOW_AUDIO", s) for s in settings.get("FOLLOW_AUDIO", [])
        ]
        self.ambilight_styles = result
        menu = await self.client.get_menu_structure()
        if off := find_menu_node(menu, "ambilight_off"):
            self.ambilight_off_node = off["node_id"]
        if style_node := find_menu_node(menu, "ambilight_style"):
            self._ambilight_style_node = style_node["node_id"]
        for context in ("ambilight_follow_video", "ambilight_follow_audio"):
            if not (node := find_menu_node(menu, context)):
                continue
            for item in node.get("data", {}).get("enums", []):
                if style := MENU_STYLES.get(item.get("string_id")):
                    self.ambilight_menu[AmbilightStyle(*style)] = (
                        node["node_id"],
                        item["enum_id"],
                    )

    async def _async_restore_ambilight(self) -> bool:
        """Apply the Ambilight style the menu holds.

        The TV skips this itself when woken through Wake-on-LAN. Return
        whether the menu holds a style, that is, Ambilight is not set to off.
        """
        if (style_node := self._ambilight_style_node) is None:
            return False
        values = await self.client.get_menu_values([style_node])
        active = values.get(style_node, {}).get("activenode_id")
        if active is None or active == self.ambilight_off_node:
            return False
        item = (
            (await self.client.get_menu_values([active]))
            .get(active, {})
            .get("selected_item")
        )
        if item is None:
            return False
        _LOGGER.debug("Restoring Ambilight from menu node %s item %s", active, item)
        styles = {menu: style for style, menu in self.ambilight_menu.items()}
        if style := styles.get((active, item)):
            await self.client.set_ambilight_style(style.style, style.setting)
        else:
            await self.client.activate_menu_node(active, item)
        return True
