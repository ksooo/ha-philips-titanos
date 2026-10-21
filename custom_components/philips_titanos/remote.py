"""Remote for the Philips Titan OS TV integration."""

import asyncio
from collections.abc import Iterable
from typing import Any, override

from homeassistant.components.remote import (
    ATTR_DELAY_SECS,
    ATTR_HOLD_SECS,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    DEFAULT_NUM_REPEATS,
    RemoteEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import TitanOSConfigEntry, TitanOSCoordinator
from .entity import TitanOSEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TitanOSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the remote."""
    async_add_entities([TitanOSRemote(entry.runtime_data)])


class TitanOSRemote(TitanOSEntity, RemoteEntity):
    """The TV's remote control keys."""

    _attr_name = None

    def __init__(self, coordinator: TitanOSCoordinator) -> None:
        """Initialize the remote."""
        super().__init__(coordinator, "remote")

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the TV is on."""
        return self.coordinator.data.is_on

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Wake the TV."""
        await self.coordinator.async_turn_on()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Put the TV into standby."""
        await self.coordinator.async_turn_off()

    @override
    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send JointSpace key names such as CursorUp or Confirm."""
        if kwargs.get(ATTR_HOLD_SECS):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="hold_not_supported"
            )
        num_repeats = kwargs.get(ATTR_NUM_REPEATS, DEFAULT_NUM_REPEATS)
        delay_secs = kwargs.get(ATTR_DELAY_SECS, DEFAULT_DELAY_SECS)
        keys = list(command) * num_repeats
        for index, key in enumerate(keys):
            if index:
                await asyncio.sleep(delay_secs)
            await self.coordinator.async_command(self.coordinator.client.send_key(key))
