"""Config flow for the Philips Titan OS TV integration."""

from collections.abc import Mapping
import logging
from typing import Any, override

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PIN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    CannotConnect,
    PairingError,
    PairingSession,
    SystemInfo,
    TitanOSClient,
    TitanOSError,
)
from .const import CONF_AUTH_KEY, CONF_DEVICE_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)


class NotSupported(Exception):
    """The device is not a Philips TV with Titan OS or Linux firmware."""


def _is_supported(data: dict[str, Any]) -> bool:
    features = data.get("featuring", {}).get("systemfeatures", {})
    return (
        data.get("api_version", {}).get("Major") == 6
        and features.get("os_type") == "Linux"
        and features.get("pairing_type") == "digest_auth_pairing"
    )


def _wol_mac(devices: list[dict[str, Any]], host: str) -> str | None:
    """Pick the MAC of the interface the TV can be woken through."""
    candidates = [d for d in devices if d.get("wake-on-lan") == "Enabled"]
    for device in candidates:
        if device.get("ip") == host:
            return format_mac(device["mac"])
    return format_mac(candidates[0]["mac"]) if candidates else None


class TitanOSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Philips Titan OS TVs."""

    VERSION = 1

    host: str
    system: SystemInfo
    client: TitanOSClient
    pairing: PairingSession | None = None

    async def _async_fetch_system(self, host: str) -> None:
        """Read /system and set the unique ID.

        Raises CannotConnect or NotSupported.
        """
        client = TitanOSClient(async_get_clientsession(self.hass), host)
        data = await client.get_system()
        if not data or not _is_supported(data):
            raise NotSupported
        self.host = host
        self.client = client
        self.system = SystemInfo.from_json(data)
        if not self.system.serial_number:
            raise NotSupported
        await self.async_set_unique_id(self.system.serial_number)

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a manually added TV and reconfiguration."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._async_fetch_system(user_input[CONF_HOST])
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except NotSupported:
                return self.async_abort(reason="not_supported")
            else:
                if self.source == SOURCE_RECONFIGURE:
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(),
                        data_updates={CONF_HOST: self.host},
                    )
                self._abort_if_unique_id_configured(updates={CONF_HOST: self.host})
                return await self.async_step_pair()
        default_host = (user_input or {}).get(CONF_HOST, vol.UNDEFINED)
        if self.source == SOURCE_RECONFIGURE:
            default_host = self._get_reconfigure_entry().data[CONF_HOST]
        return self.async_show_form(
            step_id="reconfigure" if self.source == SOURCE_RECONFIGURE else "user",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST, default=default_host): str}
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a changed IP address."""
        return await self.async_step_user(user_input)

    @override
    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a TV found via zeroconf."""
        try:
            await self._async_fetch_system(discovery_info.host)
        except CannotConnect:
            return self.async_abort(reason="cannot_connect")
        except NotSupported:
            return self.async_abort(reason="not_supported")
        self._abort_if_unique_id_configured(updates={CONF_HOST: self.host})
        self.context["title_placeholders"] = {"name": self.system.name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before pairing a discovered TV."""
        if user_input is not None:
            return await self.async_step_pair()
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"name": self.system.name},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle credentials the TV no longer accepts."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before pairing again."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._async_fetch_system(self._get_reauth_entry().data[CONF_HOST])
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except NotSupported:
                return self.async_abort(reason="not_supported")
            else:
                self._abort_if_unique_id_mismatch()
                return await self.async_step_pair()
        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"name": self._get_reauth_entry().title},
            errors=errors,
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Request a PIN on the TV and confirm it."""
        errors: dict[str, str] = {}
        if user_input is not None and self.pairing is not None:
            try:
                auth_key = await self.client.pair_grant(
                    self.pairing, user_input[CONF_PIN]
                )
                mac = _wol_mac(await self.client.get_network_devices(), self.host)
            except PairingError:
                errors["base"] = "invalid_pin"
            except TitanOSError:
                errors["base"] = "cannot_connect"
            else:
                data = {
                    CONF_HOST: self.host,
                    CONF_DEVICE_ID: self.pairing.device_id,
                    CONF_AUTH_KEY: auth_key,
                    CONF_MAC: mac,
                }
                if self.source == SOURCE_REAUTH:
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(), data=data
                    )
                return self.async_create_entry(title=self.system.name, data=data)

        # The PIN is only visible while the TV is on; without it, the form
        # just offers to retry.
        self.pairing = None
        schema = vol.Schema({})
        if not await self.client.is_on():
            errors["base"] = "tv_off"
        else:
            try:
                self.pairing = await self.client.pair_request(self.system.featuring)
            except TitanOSError:
                _LOGGER.debug("Pairing request failed", exc_info=True)
                return self.async_abort(reason="cannot_connect")
            schema = vol.Schema({vol.Required(CONF_PIN): str})
        return self.async_show_form(
            step_id="pair",
            data_schema=schema,
            description_placeholders={"name": self.system.name},
            errors=errors,
        )
