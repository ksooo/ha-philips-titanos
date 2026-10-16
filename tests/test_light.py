"""Tests for the Philips Titan OS TV Ambilight."""

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_HS_COLOR,
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.const import (
    ATTR_ASSUMED_STATE,
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util.color import color_hsv_to_RGB

from .conftest import system

ENTITY_ID = "light.43pus8400_12_ambilight"
STANDARD = {"styleName": "FOLLOW_VIDEO", "isExpert": False, "menuSetting": "STANDARD"}


class FakeAmbilight:
    """Answer like the TV: reads return what was last set."""

    def __init__(self, client: AsyncMock, config: dict[str, Any] | None) -> None:
        self.client = client
        self.config = config
        client.get_ambilight_configuration.side_effect = lambda: self.config
        client.set_ambilight_style.side_effect = self._style
        client.set_ambilight_color.side_effect = self._color
        client.activate_menu_node.side_effect = self._menu

    async def _style(self, style: str, setting: str) -> None:
        self.config = {"styleName": style, "isExpert": False, "menuSetting": setting}

    async def _color(self, hue: int, saturation: int, brightness: int) -> None:
        # The TV reports no color for it, only the LEDs show it.
        self.config = {
            "styleName": "FOLLOW_COLOR",
            "isExpert": False,
            "menuSetting": "PTA_LOUNGE",
        }
        r, g, b = color_hsv_to_RGB(
            hue / 255 * 360, saturation / 255 * 100, brightness / 255 * 100
        )
        self.client.get_ambilight_processed.return_value = {
            "layer1": {"left": {"0": {"r": r, "g": g, "b": b}}}
        }

    async def _menu(self, node_id: int, item: int | None = None) -> None:
        if node_id == 310:
            self.config = None
        elif (node_id, item) == (320, 325):
            await self._style("FOLLOW_VIDEO", "GAME")


@pytest.fixture
def tv(mock_client: AsyncMock) -> FakeAmbilight:
    """Return a TV with Ambilight following the video."""
    return FakeAmbilight(mock_client, dict(STANDARD))


async def _call(hass: HomeAssistant, service: str, **data: object) -> None:
    await hass.services.async_call(
        LIGHT_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_ID, **data}, blocking=True
    )


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _wake(
    hass: HomeAssistant, mock_client: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    """Switch the TV on through Home Assistant and let Cast come up."""
    mock_client.is_on.return_value = False
    await _tick(hass, freezer)
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "media_player.43pus8400_12"},
        blocking=True,
    )
    mock_client.is_on.return_value = True
    await _tick(hass, freezer)


@pytest.mark.usefixtures("tv", "init_integration")
async def test_initial_state(hass: HomeAssistant) -> None:
    """Report the TV's style and offer its styles."""
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_EFFECT] == "video_standard"
    assert ATTR_ASSUMED_STATE not in state.attributes
    assert state.attributes[ATTR_EFFECT_LIST] == [
        "video_standard",
        "video_sports",
        "video_natural",
        "video_game",
        "audio_vu_meter",
        "audio_party",
    ]


@pytest.mark.usefixtures("init_integration")
async def test_follow_remote(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    tv: FakeAmbilight,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Pick up changes made with the TV's remote."""
    tv.config = {
        "styleName": "FOLLOW_AUDIO",
        "isExpert": False,
        "menuSetting": "ENERGY_ADAPTIVE_BRIGHTNESS",
    }
    await _tick(hass, freezer)
    assert (
        hass.states.get(ENTITY_ID).attributes[ATTR_EFFECT]
        == "audio_energy_adaptive_brightness"
    )

    tv.config = {"styleName": "FOLLOW_COLOR", "isExpert": False}
    mock_client.get_ambilight_processed.return_value = {
        "layer1": {
            "left": {"0": {"r": 79, "g": 82, "b": 50}},
            "top": {"0": {"r": 79, "g": 82, "b": 50}},
        }
    }
    await _tick(hass, freezer)
    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_EFFECT] is None
    assert state.attributes[ATTR_HS_COLOR] == pytest.approx((65.6, 39.0), abs=0.1)
    assert state.attributes[ATTR_BRIGHTNESS] == 82

    tv.config = None
    await _tick(hass, freezer)
    assert hass.states.get(ENTITY_ID).state == STATE_OFF


@pytest.mark.usefixtures("init_integration")
async def test_turn_on_effect(
    hass: HomeAssistant, mock_client: AsyncMock, tv: FakeAmbilight
) -> None:
    """Set a style and come back to it after switching off."""
    await _call(hass, SERVICE_TURN_ON, **{ATTR_EFFECT: "audio_vu_meter"})
    mock_client.set_ambilight_style.assert_awaited_once_with("FOLLOW_AUDIO", "VU_METER")
    assert hass.states.get(ENTITY_ID).attributes[ATTR_EFFECT] == "audio_vu_meter"

    await _call(hass, SERVICE_TURN_OFF)
    mock_client.activate_menu_node.assert_awaited_once_with(310)
    assert hass.states.get(ENTITY_ID).state == STATE_OFF

    mock_client.set_ambilight_style.reset_mock()
    await _call(hass, SERVICE_TURN_ON)
    mock_client.set_ambilight_style.assert_awaited_once_with("FOLLOW_AUDIO", "VU_METER")


@pytest.mark.usefixtures("tv", "init_integration")
async def test_turn_on_menu_style(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Set a style the menu offers through the menu, so it survives standby."""
    await _call(hass, SERVICE_TURN_ON, **{ATTR_EFFECT: "video_game"})
    mock_client.activate_menu_node.assert_awaited_once_with(320, 325)
    mock_client.set_ambilight_style.assert_awaited_once_with("FOLLOW_VIDEO", "GAME")
    assert hass.states.get(ENTITY_ID).attributes[ATTR_EFFECT] == "video_game"


@pytest.mark.usefixtures("init_integration")
async def test_turn_on_default(
    hass: HomeAssistant, mock_client: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    """Follow the video when nothing else was seen before."""
    FakeAmbilight(mock_client, None)
    await _tick(hass, freezer)
    await _call(hass, SERVICE_TURN_ON)
    mock_client.set_ambilight_style.assert_awaited_once_with("FOLLOW_VIDEO", "STANDARD")
    assert hass.states.get(ENTITY_ID).state == STATE_ON


@pytest.mark.usefixtures("tv", "init_integration")
async def test_turn_on_unknown_effect(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Reject a style the TV does not offer."""
    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_TURN_ON, **{ATTR_EFFECT: "video_vivid"})
    mock_client.set_ambilight_style.assert_not_awaited()


@pytest.mark.usefixtures("tv", "init_integration")
async def test_turn_on_color(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Scale color and brightness to the TV's 0-255 and keep them."""
    await _call(
        hass, SERVICE_TURN_ON, **{ATTR_HS_COLOR: (240, 100), ATTR_BRIGHTNESS: 64}
    )
    mock_client.set_ambilight_color.assert_awaited_once_with(170, 255, 64)
    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_EFFECT] is None
    assert state.attributes[ATTR_HS_COLOR] == pytest.approx((240, 100), abs=1)
    assert state.attributes[ATTR_BRIGHTNESS] == pytest.approx(64, abs=1)

    mock_client.set_ambilight_color.reset_mock()
    await _call(hass, SERVICE_TURN_ON, **{ATTR_BRIGHTNESS: 255})
    mock_client.set_ambilight_color.assert_awaited_once_with(170, 255, 255)

    mock_client.set_ambilight_color.reset_mock()
    await _call(hass, SERVICE_TURN_OFF)
    await _call(hass, SERVICE_TURN_ON)
    mock_client.set_ambilight_color.assert_awaited_once_with(170, 255, 255)
    mock_client.set_ambilight_style.assert_not_awaited()

    await _call(hass, SERVICE_TURN_ON, **{ATTR_EFFECT: "video_standard"})
    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_HS_COLOR] is None
    assert state.attributes[ATTR_BRIGHTNESS] is None


@pytest.mark.parametrize(
    ("menu", "restored"),
    [
        ({300: {"activenode_id": 320}, 320: {"selected_item": 325}}, ("GAME",)),
        ({300: {"activenode_id": 320}, 320: {"selected_item": 321}}, ()),
        ({300: {"activenode_id": 310}}, None),
    ],
    ids=["menu_style", "menu_only_item", "menu_off"],
)
@pytest.mark.usefixtures("mock_wake_on_lan", "init_integration")
async def test_restore_after_wake(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
    menu: dict[int, dict[str, int]],
    restored: tuple[str, ...] | None,
) -> None:
    """Apply the menu's style when the TV wakes with Ambilight off."""
    FakeAmbilight(mock_client, None)
    mock_client.get_menu_values.side_effect = lambda nodes: {
        node: menu[node] for node in nodes if node in menu
    }
    await _wake(hass, mock_client, freezer)

    if restored is None:
        mock_client.set_ambilight_style.assert_not_awaited()
        mock_client.activate_menu_node.assert_not_awaited()
    elif restored:
        mock_client.set_ambilight_style.assert_awaited_once_with(
            "FOLLOW_VIDEO", *restored
        )
        assert hass.states.get(ENTITY_ID).attributes[ATTR_EFFECT] == "video_game"
    else:
        mock_client.activate_menu_node.assert_awaited_once_with(320, 321)

    mock_client.set_ambilight_style.reset_mock()
    mock_client.activate_menu_node.reset_mock()
    await _tick(hass, freezer)
    mock_client.set_ambilight_style.assert_not_awaited()
    mock_client.activate_menu_node.assert_not_awaited()


@pytest.mark.usefixtures("tv", "mock_wake_on_lan", "init_integration")
async def test_no_restore_when_lit(
    hass: HomeAssistant, mock_client: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    """Leave Ambilight alone when the TV started it itself."""
    await _wake(hass, mock_client, freezer)
    mock_client.get_menu_values.assert_not_awaited()


@pytest.mark.usefixtures("init_integration")
async def test_no_restore_without_wake(
    hass: HomeAssistant, mock_client: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    """Leave Ambilight off when the TV comes up without Wake-on-LAN."""
    FakeAmbilight(mock_client, None)
    mock_client.is_on.return_value = False
    await _tick(hass, freezer)
    mock_client.is_on.return_value = True
    await _tick(hass, freezer)
    mock_client.get_menu_values.assert_not_awaited()


@pytest.mark.usefixtures("tv", "init_integration")
async def test_unavailable_in_standby(
    hass: HomeAssistant, mock_client: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    """Be unavailable while the TV is off."""
    mock_client.is_on.return_value = False
    await _tick(hass, freezer)
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE

    mock_client.is_on.return_value = True
    await _tick(hass, freezer)
    assert hass.states.get(ENTITY_ID).state == STATE_ON


async def test_no_ambilight(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Create no light and read nothing for a TV without Ambilight."""
    data = system()
    del data["featuring"]["jsonfeatures"]["ambilight"]
    mock_client.get_system.return_value = data
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID) is None
    mock_client.get_ambilight_configuration.assert_not_awaited()
