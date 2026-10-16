"""Tests for the Philips Titan OS TV media player."""

from datetime import timedelta
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.philips_titanos.api import ApiError
from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_VOLUME_MUTED,
    DOMAIN as MP_DOMAIN,
    SERVICE_PLAY_MEDIA,
    SERVICE_SELECT_SOURCE,
    MediaPlayerEntityFeature,
    MediaType,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_MEDIA_NEXT_TRACK,
    SERVICE_MEDIA_PREVIOUS_TRACK,
    SERVICE_MEDIA_STOP,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_DOWN,
    SERVICE_VOLUME_MUTE,
    SERVICE_VOLUME_UP,
    STATE_OFF,
    STATE_ON,
    STATE_PLAYING,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from .conftest import MAC, SERIAL

ENTITY_ID = "media_player.43pus8400_12"


async def _call(hass: HomeAssistant, service: str, **data: object) -> None:
    await hass.services.async_call(
        MP_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_ID, **data}, blocking=True
    )


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_state_and_device(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """Expose power, source and the device."""
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_INPUT_SOURCE] == "Home"
    assert state.attributes["source_list"] == ["Home", "Live TV"]

    device = device_registry.async_get_device_by_identifier(
        ("philips_titanos", SERIAL), init_integration.entry_id
    )
    assert device.manufacturer == "Philips"
    assert device.model == "43PUS8400/12"
    assert device.model_id == "TPN256E"
    assert (dr.CONNECTION_NETWORK_MAC, MAC) in device.connections


@pytest.mark.parametrize(
    ("activity", "source"),
    [("watchtv", "Live TV"), ("extension", "HDMI"), ("something", None)],
)
@pytest.mark.usefixtures("init_integration")
async def test_source_from_activity(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
    activity: str,
    source: str | None,
) -> None:
    """Map the activity to a source."""
    mock_client.get_current_activity.return_value = activity
    await _tick(hass, freezer, 10)
    assert hass.states.get(ENTITY_ID).attributes.get(ATTR_INPUT_SOURCE) == source


@pytest.mark.usefixtures("init_integration")
async def test_standby_and_unavailable(
    hass: HomeAssistant, mock_client: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    """Show standby as off and API errors as unavailable."""
    mock_client.is_on.return_value = False
    await _tick(hass, freezer, 10)
    assert hass.states.get(ENTITY_ID).state == STATE_OFF

    mock_client.is_on.return_value = True
    mock_client.get_current_activity.side_effect = ApiError("GET", "/x", 503)
    await _tick(hass, freezer, 10)
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("init_integration")
async def test_turn_on(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_wake_on_lan: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Wake the TV and report on until Cast comes up."""
    mock_client.is_on.return_value = False
    await _tick(hass, freezer, 10)

    await _call(hass, SERVICE_TURN_ON)
    mock_wake_on_lan.assert_awaited_once_with(MAC)
    assert hass.states.get(ENTITY_ID).state == STATE_ON

    await _tick(hass, freezer, 10)
    assert hass.states.get(ENTITY_ID).state == STATE_ON

    await _tick(hass, freezer, 40)
    assert hass.states.get(ENTITY_ID).state == STATE_OFF


@pytest.mark.usefixtures("init_integration")
async def test_turn_off(
    hass: HomeAssistant, mock_client: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    """Send Standby once and report off while Cast is still up."""
    await _call(hass, SERVICE_TURN_OFF)
    mock_client.send_key.assert_awaited_once_with("Standby")
    assert hass.states.get(ENTITY_ID).state == STATE_OFF

    await _tick(hass, freezer, 10)
    assert hass.states.get(ENTITY_ID).state == STATE_OFF

    await _call(hass, SERVICE_TURN_OFF)
    mock_client.send_key.assert_awaited_once()


async def test_no_turn_on_without_mac(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Offer no power on without a MAC for Wake-on-LAN."""
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, "mac": None}
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    features = hass.states.get(ENTITY_ID).attributes["supported_features"]
    assert not features & 128


@pytest.mark.parametrize(
    ("service", "key"),
    [(SERVICE_VOLUME_UP, "VolumeUp"), (SERVICE_VOLUME_DOWN, "VolumeDown")],
)
@pytest.mark.usefixtures("init_integration")
async def test_volume(
    hass: HomeAssistant, mock_client: AsyncMock, service: str, key: str
) -> None:
    """Step the volume with keys."""
    await _call(hass, service)
    mock_client.send_key.assert_awaited_once_with(key)


@pytest.mark.usefixtures("init_integration")
async def test_mute(
    hass: HomeAssistant, mock_client: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    """Toggle mute only when it differs, and follow the remote."""
    assert hass.states.get(ENTITY_ID).attributes[ATTR_MEDIA_VOLUME_MUTED] is False

    await _call(hass, SERVICE_VOLUME_MUTE, **{ATTR_MEDIA_VOLUME_MUTED: True})
    mock_client.send_key.assert_awaited_once_with("Mute")
    assert hass.states.get(ENTITY_ID).attributes[ATTR_MEDIA_VOLUME_MUTED] is True

    mock_client.send_key.reset_mock()
    await _call(hass, SERVICE_VOLUME_MUTE, **{ATTR_MEDIA_VOLUME_MUTED: True})
    mock_client.send_key.assert_not_awaited()

    await _tick(hass, freezer, 10)
    assert hass.states.get(ENTITY_ID).attributes[ATTR_MEDIA_VOLUME_MUTED] is False


@pytest.mark.usefixtures("init_integration")
async def test_command_failed(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Report a refused command."""
    mock_client.send_key.side_effect = ApiError("POST", "/input/key", 403)
    with pytest.raises(HomeAssistantError, match="did not accept"):
        await _call(hass, SERVICE_VOLUME_UP)


@pytest.mark.usefixtures("init_integration")
async def test_select_source(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Go home with the key and to live TV with the last channel."""
    await _call(hass, SERVICE_SELECT_SOURCE, source="Home")
    mock_client.send_key.assert_awaited_once_with("Home")

    await _call(hass, SERVICE_SELECT_SOURCE, source="Live TV")
    mock_client.set_channel.assert_awaited_once_with(40005004, "allst")

    await _call(
        hass,
        SERVICE_PLAY_MEDIA,
        **{
            ATTR_MEDIA_CONTENT_TYPE: MediaType.CHANNEL,
            ATTR_MEDIA_CONTENT_ID: "40005006",
        },
    )
    mock_client.set_channel.reset_mock()
    await _call(hass, SERVICE_SELECT_SOURCE, source="Live TV")
    mock_client.set_channel.assert_awaited_once_with(40005006, "allst")

    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_SELECT_SOURCE, source="HDMI")


@pytest.mark.parametrize(
    "media_id", ["40005006", "euronews deutsch", " Euronews Deutsch "]
)
@pytest.mark.usefixtures("init_integration")
async def test_play_channel(
    hass: HomeAssistant, mock_client: AsyncMock, media_id: str
) -> None:
    """Tune by ccid or name."""
    await _call(
        hass,
        SERVICE_PLAY_MEDIA,
        **{ATTR_MEDIA_CONTENT_TYPE: MediaType.CHANNEL, ATTR_MEDIA_CONTENT_ID: media_id},
    )
    mock_client.set_channel.assert_awaited_once_with(40005006, "allst")


@pytest.mark.parametrize(
    ("media_type", "media_id"),
    [(MediaType.CHANNEL, "Unknown TV"), (MediaType.URL, "http://example.com")],
)
@pytest.mark.usefixtures("init_integration")
async def test_play_invalid(
    hass: HomeAssistant, mock_client: AsyncMock, media_type: str, media_id: str
) -> None:
    """Reject what the TV cannot play."""
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            SERVICE_PLAY_MEDIA,
            **{ATTR_MEDIA_CONTENT_TYPE: media_type, ATTR_MEDIA_CONTENT_ID: media_id},
        )
    mock_client.set_channel.assert_not_awaited()


@pytest.mark.usefixtures("init_integration")
async def test_browse_channels(hass: HomeAssistant, hass_ws_client) -> None:
    """List the channels in the media browser."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "media_player/browse_media", "entity_id": ENTITY_ID}
    )
    response = await client.receive_json()
    assert response["success"]
    children = response["result"]["children"]
    assert [c["title"] for c in children] == ["CNN International", "Euronews Deutsch"]
    assert children[1]["media_content_id"] == "40005006"
    assert children[1]["media_content_type"] == "channel"
    assert children[1]["can_play"]


@pytest.mark.usefixtures("init_integration")
async def test_live_tv_controls(
    hass: HomeAssistant, mock_client: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    """Offer stop and channel stepping only while live TV runs."""
    live_tv = (
        MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
    )
    assert not hass.states.get(ENTITY_ID).attributes[ATTR_SUPPORTED_FEATURES] & live_tv

    mock_client.get_current_activity.return_value = "watchtv"
    await _tick(hass, freezer, 10)
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_PLAYING
    assert state.attributes[ATTR_SUPPORTED_FEATURES] & live_tv == live_tv

    await _call(hass, SERVICE_MEDIA_NEXT_TRACK)
    await _call(hass, SERVICE_MEDIA_PREVIOUS_TRACK)
    await _call(hass, SERVICE_MEDIA_STOP)
    assert [c.args[0] for c in mock_client.send_key.await_args_list] == [
        "ChannelStepUp",
        "ChannelStepDown",
        "Home",
    ]
