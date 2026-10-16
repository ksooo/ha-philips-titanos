"""Fixtures for the Philips Titan OS TV tests."""

import base64
from collections.abc import Generator
import os
from typing import Any
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import pytest

from custom_components.philips_titanos.api import _SHARED_KEY, PairingSession

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


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Mock the API client used by the config flow."""
    with (
        patch(
            "custom_components.philips_titanos.config_flow.TitanOSClient",
            autospec=True,
        ) as client_cls,
        patch("custom_components.philips_titanos.TitanOSClient", autospec=True),
    ):
        client = client_cls.return_value
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
        yield client
