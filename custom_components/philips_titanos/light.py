"""Ambilight for the Philips Titan OS TV integration."""

from typing import Any, override

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.color import color_RGB_to_hsv

from .const import DOMAIN
from .coordinator import AmbilightStyle, TitanOSConfigEntry, TitanOSCoordinator
from .entity import TitanOSEntity

PARALLEL_UPDATES = 1

DEFAULT_STYLE = AmbilightStyle("FOLLOW_VIDEO", "STANDARD")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TitanOSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ambilight."""
    if entry.runtime_data.has_ambilight:
        async_add_entities([TitanOSAmbilight(entry.runtime_data)])


class TitanOSAmbilight(TitanOSEntity, LightEntity):
    """Ambilight as the TV reports it.

    Styles that the TV's settings menu offers are set through the menu, so the
    TV keeps them across standby. Other styles and colors bypass the menu, and
    the TV restores the menu value after standby.
    """

    _attr_translation_key = "ambilight"
    _attr_color_mode = ColorMode.HS
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(self, coordinator: TitanOSCoordinator) -> None:
        """Initialize Ambilight."""
        super().__init__(coordinator, "ambilight")
        self._attr_supported_color_modes = {ColorMode.HS}
        # What turning on without arguments restores.
        self._last_effect: str | None = None
        self._last_hs: tuple[float, float] | None = None
        self._last_brightness: int | None = None
        self._read_state()

    @property
    @override
    def available(self) -> bool:
        """Return whether the TV is on."""
        return super().available and self.coordinator.data.is_on

    @property
    @override
    def effect_list(self) -> list[str]:
        """Return the styles the TV offers."""
        return [style.effect for style in self.coordinator.ambilight_styles]

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Take over the state the TV reports."""
        self._read_state()
        super()._handle_coordinator_update()

    def _read_state(self) -> None:
        data = self.coordinator.data
        config = data.ambilight
        self._attr_is_on = config is not None
        self._attr_effect = None
        self._attr_hs_color = None
        self._attr_brightness = None
        if config is None:
            return
        style = config.get("styleName")
        if style == "FOLLOW_COLOR":
            # The configuration carries no color, not even one the API set.
            if data.ambilight_rgb is not None:
                hue, saturation, value = color_RGB_to_hsv(*data.ambilight_rgb)
                self._attr_hs_color = (hue, saturation)
                self._attr_brightness = round(value / 100 * 255)
            self._last_effect = None
            self._last_hs = self._attr_hs_color
            self._last_brightness = self._attr_brightness
        elif (setting := config.get("menuSetting")) and style in (
            "FOLLOW_VIDEO",
            "FOLLOW_AUDIO",
        ):
            self._attr_effect = AmbilightStyle(style, setting).effect
            self._last_effect = self._attr_effect

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch Ambilight on with a style or a color."""
        if effect := kwargs.get(ATTR_EFFECT):
            await self._set_style(effect)
        elif ATTR_HS_COLOR in kwargs or ATTR_BRIGHTNESS in kwargs:
            await self._set_color(
                kwargs.get(ATTR_HS_COLOR, self._last_hs or (0.0, 0.0)),
                kwargs.get(ATTR_BRIGHTNESS, self._last_brightness or 255),
            )
        elif self._last_effect is None and self._last_hs is not None:
            await self._set_color(self._last_hs, self._last_brightness or 255)
        else:
            await self._set_style(self._last_effect or DEFAULT_STYLE.effect)
        await self.coordinator.async_refresh()

    async def _set_style(self, effect: str) -> None:
        style = next(
            (s for s in self.coordinator.ambilight_styles if s.effect == effect),
            None,
        )
        if style is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_effect",
                translation_placeholders={"effect": effect},
            )
        client = self.coordinator.client
        if menu := self.coordinator.ambilight_menu.get(style):
            await self.coordinator.async_command(client.activate_menu_node(*menu))
        # Selecting the item the menu already shows changes nothing.
        await self.coordinator.async_command(
            client.set_ambilight_style(style.style, style.setting)
        )

    async def _set_color(self, hs: tuple[float, float], brightness: int) -> None:
        await self.coordinator.async_command(
            self.coordinator.client.set_ambilight_color(
                round(hs[0] / 360 * 255), round(hs[1] / 100 * 255), brightness
            )
        )

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch Ambilight off through the TV's menu."""
        if (node := self.coordinator.ambilight_off_node) is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="no_ambilight_off"
            )
        await self.coordinator.async_command(
            self.coordinator.client.activate_menu_node(node)
        )
        await self.coordinator.async_refresh()
