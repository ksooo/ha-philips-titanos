"""Tests for the Philips Titan OS TV config flow."""

from ipaddress import ip_address
from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.philips_titanos.api import CannotConnect, PairingError
from custom_components.philips_titanos.const import (
    CONF_AUTH_KEY,
    CONF_DEVICE_ID,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PIN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .conftest import HOST, MAC, SERIAL, system

ENTRY_DATA = {
    CONF_HOST: HOST,
    CONF_DEVICE_ID: "0123456789abcdef",
    CONF_AUTH_KEY: "granted",
    CONF_MAC: MAC,
}

ZEROCONF = ZeroconfServiceInfo(
    ip_address=ip_address(HOST),
    ip_addresses=[ip_address(HOST)],
    hostname="n22074_linux_tk.local.",
    name="6_43PUS8400/12._philipstv_s_rpc._tcp.local.",
    port=1926,
    properties={},
    type="_philipstv_s_rpc._tcp.local.",
)


def _entry(hass: HomeAssistant, host: str = HOST) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        title="43PUS8400/12",
        data={**ENTRY_DATA, CONF_HOST: host},
    )
    entry.add_to_hass(hass)
    return entry


async def test_user_flow(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Pair a manually added TV."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pair"
    assert not result["errors"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "1234"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "43PUS8400/12"
    assert result["result"].unique_id == SERIAL
    assert result["data"] == ENTRY_DATA
    mock_client.pair_grant.assert_awaited_once()
    assert mock_client.pair_grant.await_args.args[1] == "1234"


async def test_user_cannot_connect(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Stay in the user step if the TV does not answer."""
    mock_client.get_system.side_effect = CannotConnect
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_not_supported(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Reject Android based Philips TVs."""
    mock_client.get_system.return_value = system(os_type="MSAF_2019_P")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_user_already_configured(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Update the host of an existing entry instead of pairing again."""
    entry = _entry(hass, host="192.0.2.99")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST
    mock_client.pair_request.assert_not_awaited()


async def test_pair_tv_off(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Request the PIN only once the TV is on."""
    mock_client.is_on.return_value = False
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={CONF_HOST: HOST}
    )
    assert result["step_id"] == "pair"
    assert result["errors"] == {"base": "tv_off"}
    mock_client.pair_request.assert_not_awaited()

    mock_client.is_on.return_value = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pair"
    assert not result["errors"]
    mock_client.pair_request.assert_awaited_once()


async def test_pair_invalid_pin(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Request a new PIN after a wrong one."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={CONF_HOST: HOST}
    )
    mock_client.pair_grant.side_effect = PairingError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "0000"}
    )
    assert result["step_id"] == "pair"
    assert result["errors"] == {"base": "invalid_pin"}
    assert mock_client.pair_request.await_count == 2

    mock_client.pair_grant.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "1234"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_pair_without_wol(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Store no MAC if no interface can wake the TV."""
    mock_client.get_network_devices.return_value = [
        {"id": "eth0", "mac": "02:00:00:00:00:02", "wake-on-lan": "Disabled"}
    ]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={CONF_HOST: HOST}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "1234"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MAC] is None


async def test_zeroconf(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Pair a discovered TV after confirmation."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=ZEROCONF
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    mock_client.pair_request.assert_not_awaited()

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pair"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "1234"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == ENTRY_DATA


async def test_zeroconf_updates_host(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Follow a new IP address of a configured TV."""
    entry = _entry(hass, host="192.0.2.99")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=ZEROCONF
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST


async def test_zeroconf_not_supported(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Ignore Android based Philips TVs."""
    mock_client.get_system.return_value = system(os_type="MSAF_2019_P")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=ZEROCONF
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_reauth(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Replace the credentials after pairing again."""
    entry = _entry(hass)
    mock_client.pair_grant.return_value = "new-key"
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pair"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "1234"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_AUTH_KEY] == "new-key"
    await hass.async_block_till_done()


async def test_reauth_other_tv(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Refuse to pair a different TV that took over the address."""
    entry = _entry(hass)
    mock_client.get_system.return_value = system(serial="OTHER")
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"


async def test_reconfigure(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """Change the address without pairing again."""
    entry = _entry(hass, host="192.0.2.99")
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == ENTRY_DATA
    mock_client.pair_request.assert_not_awaited()
    await hass.async_block_till_done()


async def test_reconfigure_other_tv(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Refuse an address that belongs to a different TV."""
    entry = _entry(hass, host="192.0.2.99")
    mock_client.get_system.return_value = system(serial="OTHER")
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data[CONF_HOST] == "192.0.2.99"
