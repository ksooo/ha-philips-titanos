"""Fixtures for the Philips Titan OS TV tests."""

import base64
from collections.abc import Generator
import os
from typing import Any
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.philips_titanos.api import (
    _SHARED_KEY,
    PairingSession,
    TitanOSClient,
)
from custom_components.philips_titanos.const import (
    CONF_AUTH_KEY,
    CONF_DEVICE_ID,
    DOMAIN,
)
from homeassistant.const import CONF_HOST, CONF_MAC
from homeassistant.core import HomeAssistant

HOST = "192.0.2.10"
SERIAL = "FZ1A0000000000-001122334455"
MAC = "02:00:00:00:00:01"


def encrypt(value: str) -> str:
    """Encrypt like the TV does for its /system fields."""
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(value.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_SHARED_KEY[:16]), modes.CBC(iv)).encryptor()
    return base64.b64encode(
        iv + encryptor.update(padded) + encryptor.finalize()
    ).decode()


def system(os_type: str = "Linux", serial: str = SERIAL) -> dict[str, Any]:
    """Return a /system response."""
    return {
        "name": "43PUS8400/12",
        "serialnumber_encrypted": encrypt(serial),
        "softwareversion_encrypted": encrypt("-000.000.000.000"),
        "model_encrypted": encrypt("TPN256E"),
        "api_version": {"Major": 6, "Minor": 1, "Patch": 0},
        "featuring": {
            "jsonfeatures": {"ambilight": ["Ambilight"]},
            "systemfeatures": {
                "pairing_type": "digest_auth_pairing",
                "secured_transport": True,
                "os_type": os_type,
            },
        },
    }


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


CHANNEL_DB = {
    "channelLists": [{"id": "allst", "name": "Streaming channels"}],
    "favoriteLists": [],
}
CHANNELS = [
    {"ccid": 40005004, "preset": 5004, "name": "CNN International"},
    {"ccid": 40005006, "preset": 5006, "name": "Euronews Deutsch"},
]


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Mock the API client of config flow and integration."""
    client = AsyncMock(spec=TitanOSClient)
    client.get_system.return_value = system()
    client.is_on.return_value = True
    client.pair_request.return_value = PairingSession(
        device_id="0123456789abcdef", auth_key="pending", timestamp=42, timeout=30
    )
    client.pair_grant.return_value = "granted"
    client.get_network_devices.return_value = [
        {"id": "eth0", "mac": "02:00:00:00:00:02", "wake-on-lan": "Disabled"},
        {"id": "wifi0", "mac": MAC, "ip": HOST, "wake-on-lan": "Enabled"},
    ]
    client.get_current_activity.return_value = "browser"
    client.is_muted.return_value = False
    client.get_channel_db.return_value = CHANNEL_DB
    client.get_channels.return_value = CHANNELS
    with (
        patch(
            "custom_components.philips_titanos.config_flow.TitanOSClient",
            return_value=client,
        ),
        patch("custom_components.philips_titanos.TitanOSClient", return_value=client),
    ):
        yield client


@pytest.fixture
def mock_wake_on_lan() -> Generator[AsyncMock]:
    """Mock sending the magic packet."""
    with patch(
        "custom_components.philips_titanos.coordinator.wake_on_lan"
    ) as wake_on_lan:
        yield wake_on_lan


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a paired TV."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        title="43PUS8400/12",
        data={
            CONF_HOST: HOST,
            CONF_DEVICE_ID: "0123456789abcdef",
            CONF_AUTH_KEY: "granted",
            CONF_MAC: MAC,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: AsyncMock
) -> MockConfigEntry:
    """Set up the integration with a TV that is on."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
