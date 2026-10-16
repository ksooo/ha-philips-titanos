"""Media player for the Philips Titan OS TV integration."""

from collections.abc import Awaitable
from dataclasses import replace
from typing import Any, override

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import TitanOSError, wake_on_lan
from .const import DOMAIN
from .coordinator import Channel, TitanOSConfigEntry, TitanOSCoordinator
from .entity import TitanOSEntity

PARALLEL_UPDATES = 1

SOURCE_HOME = "Home"
SOURCE_LIVE_TV = "Live TV"
SOURCE_HDMI = "HDMI"

_LIVE_TV_FEATURES = (
    MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
)

_ACTIVITY_SOURCES = {
    "browser": SOURCE_HOME,
    "watchtv": SOURCE_LIVE_TV,
    "extension": SOURCE_HDMI,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TitanOSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the media player."""
    async_add_entities([TitanOSMediaPlayer(entry.runtime_data)])


class TitanOSMediaPlayer(TitanOSEntity, MediaPlayerEntity):
    """The TV as a media player."""

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV

    def __init__(self, coordinator: TitanOSCoordinator) -> None:
        """Initialize the media player."""
        super().__init__(coordinator, None)
        self._attr_source_list = [SOURCE_HOME, SOURCE_LIVE_TV]
        self._mac: str | None = coordinator.config_entry.data.get(CONF_MAC)
        self._attr_supported_features = (
            MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.BROWSE_MEDIA
            | MediaPlayerEntityFeature.PLAY_MEDIA
        )
        if self._mac:
            self._attr_supported_features |= MediaPlayerEntityFeature.TURN_ON

    @property
    @override
    def state(self) -> MediaPlayerState:
        """Return off, playing in live TV, otherwise on."""
        if not self.coordinator.data.is_on:
            return MediaPlayerState.OFF
        if self.coordinator.data.activity == "watchtv":
            return MediaPlayerState.PLAYING
        return MediaPlayerState.ON

    @property
    @override
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Offer stop and channel stepping only in live TV."""
        if self.coordinator.data.activity == "watchtv":
            return self._attr_supported_features | _LIVE_TV_FEATURES
        return self._attr_supported_features

    @property
    @override
    def is_volume_muted(self) -> bool | None:
        """Return whether the TV is muted."""
        return self.coordinator.data.muted

    @property
    @override
    def source(self) -> str | None:
        """Return what the TV shows."""
        return _ACTIVITY_SOURCES.get(self.coordinator.data.activity or "")

    async def _call(self, command: Awaitable[Any]) -> None:
        try:
            await command
        except (TitanOSError, OSError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    @override
    async def async_turn_on(self) -> None:
        """Wake the TV."""
        assert self._mac
        await self._call(wake_on_lan(self._mac))
        self.coordinator.expect_power(True)

    @override
    async def async_turn_off(self) -> None:
        """Put the TV into standby."""
        # Standby is the remote's power key and may wake a TV in standby.
        if not self.coordinator.data.is_on:
            return
        await self._call(self.coordinator.client.send_key("Standby"))
        self.coordinator.expect_power(False)

    @override
    async def async_volume_up(self) -> None:
        """Raise the volume by one step."""
        await self._call(self.coordinator.client.send_key("VolumeUp"))

    @override
    async def async_volume_down(self) -> None:
        """Lower the volume by one step."""
        await self._call(self.coordinator.client.send_key("VolumeDown"))

    @override
    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute with the remote's mute key, which toggles."""
        if self.coordinator.data.muted == mute:
            return
        await self._call(self.coordinator.client.send_key("Mute"))
        self.coordinator.async_set_updated_data(
            replace(self.coordinator.data, muted=mute)
        )

    @override
    async def async_media_stop(self) -> None:
        """Leave live TV for the home screen."""
        await self._call(self.coordinator.client.send_key("Home"))
        await self.coordinator.async_request_refresh()

    @override
    async def async_media_next_track(self) -> None:
        """Tune to the next channel."""
        await self._call(self.coordinator.client.send_key("ChannelStepUp"))

    @override
    async def async_media_previous_track(self) -> None:
        """Tune to the previous channel."""
        await self._call(self.coordinator.client.send_key("ChannelStepDown"))

    @override
    async def async_select_source(self, source: str) -> None:
        """Switch to the home screen or live TV."""
        if source == SOURCE_HOME:
            await self._call(self.coordinator.client.send_key("Home"))
        elif source == SOURCE_LIVE_TV:
            channel = self.coordinator.last_channel or next(
                iter(self.coordinator.channels), None
            )
            if channel is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="no_channels"
                )
            await self._tune(channel)
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_source",
                translation_placeholders={"source": source},
            )
        await self.coordinator.async_request_refresh()

    async def _tune(self, channel: Channel) -> None:
        await self._call(
            self.coordinator.client.set_channel(channel.ccid, channel.list_id)
        )
        self.coordinator.last_channel = channel

    @override
    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Tune to a channel given by name or ccid."""
        if media_type != MediaType.CHANNEL:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_media_type",
                translation_placeholders={"media_type": media_type},
            )
        wanted = media_id.strip().casefold()
        for channel in self.coordinator.channels:
            if wanted in (str(channel.ccid), channel.name.casefold()):
                await self._tune(channel)
                await self.coordinator.async_request_refresh()
                return
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_channel",
            translation_placeholders={"channel": media_id},
        )

    @override
    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """List the channels."""
        return BrowseMedia(
            title="Channels",
            media_class=MediaClass.DIRECTORY,
            media_content_id="channels",
            media_content_type=MediaType.CHANNELS,
            children_media_class=MediaClass.CHANNEL,
            can_play=False,
            can_expand=True,
            children=[
                BrowseMedia(
                    title=channel.name,
                    media_class=MediaClass.CHANNEL,
                    media_content_id=str(channel.ccid),
                    media_content_type=MediaType.CHANNEL,
                    can_play=True,
                    can_expand=False,
                )
                for channel in self.coordinator.channels
            ],
        )
