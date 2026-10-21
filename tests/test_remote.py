"""Tests for the Philips Titan OS TV remote."""

from datetime import timedelta
from unittest.mock import AsyncMock, call, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.components.remote import (
    ATTR_COMMAND,
    ATTR_DELAY_SECS,
    ATTR_HOLD_SECS,
    ATTR_NUM_REPEATS,
    DOMAIN as REMOTE_DOMAIN,
    SERVICE_SEND_COMMAND,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .conftest import MAC

ENTITY_ID = "remote.43pus8400_12"


async def _call(hass: HomeAssistant, service: str, **data: object) -> None:
    await hass.services.async_call(
        REMOTE_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_ID, **data}, blocking=True
    )


@pytest.mark.usefixtures("init_integration")
async def test_state(
    hass: HomeAssistant, mock_client: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    """Follow the power state of the TV."""
    assert hass.states.get(ENTITY_ID).state == STATE_ON
    mock_client.is_on.return_value = False
    freezer.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_OFF


@pytest.mark.usefixtures("init_integration")
async def test_power(
    hass: HomeAssistant, mock_client: AsyncMock, mock_wake_on_lan: AsyncMock
) -> None:
    """Switch the TV like the media player does."""
    await _call(hass, SERVICE_TURN_OFF)
    mock_client.send_key.assert_awaited_once_with("Standby")
    assert hass.states.get(ENTITY_ID).state == STATE_OFF

    await _call(hass, SERVICE_TURN_ON)
    mock_wake_on_lan.assert_awaited_once_with(MAC)
    assert hass.states.get(ENTITY_ID).state == STATE_ON


@pytest.mark.usefixtures("init_integration")
async def test_send_command(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Send keys in order, repeated, with a pause in between."""
    with patch("custom_components.philips_titanos.remote.asyncio.sleep") as sleep:
        await _call(
            hass,
            SERVICE_SEND_COMMAND,
            **{
                ATTR_COMMAND: ["CursorDown", "Confirm"],
                ATTR_NUM_REPEATS: 2,
                ATTR_DELAY_SECS: 0.2,
            },
        )
    assert mock_client.send_key.await_args_list == [
        call("CursorDown"),
        call("Confirm"),
        call("CursorDown"),
        call("Confirm"),
    ]
    assert sleep.await_args_list == [call(0.2)] * 3


@pytest.mark.usefixtures("init_integration")
async def test_send_command_hold(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Refuse holding keys."""
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            SERVICE_SEND_COMMAND,
            **{ATTR_COMMAND: ["VolumeUp"], ATTR_HOLD_SECS: 1},
        )
    mock_client.send_key.assert_not_awaited()


async def test_turn_on_without_mac(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Explain why a TV without a known MAC cannot be switched on."""
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, "mac": None}
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError, match="Wake-on-LAN"):
        await _call(hass, SERVICE_TURN_ON)
