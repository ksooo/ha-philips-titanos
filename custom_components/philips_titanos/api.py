"""Async client for the JointSpace v6 API of Philips Titan OS TVs.

Most GET endpoints of this firmware return what was last set through the API,
not the actual device state, and the API stays reachable in standby. The TV's
Cast receiver runs only while the TV is on, so its port is the power indicator.
"""

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import logging
import secrets
from typing import Any

import aiohttp
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

PORT = 1926
CAST_PORT = 8009
DEFAULT_TIMEOUT = 10

# Philips' app secret: signs pairing grants and encrypts the /system fields.
_SHARED_KEY = base64.b64decode(
    "ZmVay1EQVFOaZhwQ4Kv81ypLAZNczV9sG4KkseXWn1NEk6cXmPKO/MCa9sryslvLCFMnNe4Z4CPXzToowvhHvA=="
)
_PAIRING_SCOPE = ["read", "write", "control"]


class TitanOSError(Exception):
    """Base class for errors talking to the TV."""


class CannotConnect(TitanOSError):
    """The TV is not reachable."""


class InvalidAuth(TitanOSError):
    """The TV rejected the stored credentials."""


class PairingError(TitanOSError):
    """Pairing was refused, e.g. because of a wrong or expired PIN."""


class ApiError(TitanOSError):
    """The TV answered with an unexpected HTTP status."""

    def __init__(self, method: str, path: str, status: int) -> None:
        """Initialize the error."""
        super().__init__(f"{method} {path}: HTTP {status}")
        self.status = status


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """Device data from the unauthenticated /system endpoint."""

    name: str
    model: str | None
    serial_number: str | None
    software_version: str | None
    api_version: str
    featuring: dict[str, Any]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SystemInfo:
        """Build from a /system response."""
        version = data.get("api_version", {})
        return cls(
            name=data.get("name", ""),
            model=decrypt(data.get("model_encrypted")),
            serial_number=decrypt(data.get("serialnumber_encrypted")),
            software_version=decrypt(data.get("softwareversion_encrypted")),
            api_version=".".join(
                str(version.get(part, 0)) for part in ("Major", "Minor", "Patch")
            ),
            featuring=data.get("featuring", {}),
        )


@dataclass(frozen=True, slots=True)
class PairingSession:
    """State between pairing request and grant."""

    device_id: str
    auth_key: str
    timestamp: int
    timeout: int


def decrypt(value: str | None) -> str | None:
    """Decrypt an AES-CBC encrypted /system field."""
    if not value:
        return None
    raw = base64.b64decode(value)
    decryptor = Cipher(
        algorithms.AES(_SHARED_KEY[:16]), modes.CBC(raw[:16])
    ).decryptor()
    unpadder = padding.PKCS7(128).unpadder()
    padded = decryptor.update(raw[16:]) + decryptor.finalize()
    return (unpadder.update(padded) + unpadder.finalize()).decode()


def find_menu_node(node: dict[str, Any], context: str) -> int | None:
    """Return the id of the settings menu node with this context."""
    if node.get("context") == context:
        return node.get("node_id")
    for child in (node.get("data") or {}).get("nodes") or []:
        if (node_id := find_menu_node(child, context)) is not None:
            return node_id
    return None


def _device(device_id: str) -> dict[str, str]:
    return {
        "device_name": "Home Assistant",
        "device_os": "Home Assistant",
        "app_name": "Home Assistant",
        "app_id": "homeassistant",
        "type": "native",
        "id": device_id,
    }


class TitanOSClient:
    """JointSpace v6 client over HTTPS with digest authentication."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        device_id: str | None = None,
        auth_key: str | None = None,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self.host = host
        self._base = f"https://{host}:{PORT}/6"
        self._auth: aiohttp.DigestAuthMiddleware | None = None
        if device_id and auth_key:
            self.set_credentials(device_id, auth_key)

    def set_credentials(self, device_id: str, auth_key: str) -> None:
        """Use these credentials for all further requests."""
        self._auth = aiohttp.DigestAuthMiddleware(device_id, auth_key)

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        auth: bool = True,
        request_timeout: float = DEFAULT_TIMEOUT,
    ) -> Any:
        middlewares = (self._auth,) if auth and self._auth else ()
        try:
            async with self._session.request(
                method,
                self._base + path,
                json=body,
                # Self-signed certificate without SAN.
                ssl=False,
                middlewares=middlewares,
                timeout=aiohttp.ClientTimeout(total=request_timeout),
            ) as resp:
                text = await resp.text()
        except (aiohttp.ClientError, OSError) as err:
            raise CannotConnect(f"{method} {path}: {err}") from err
        _LOGGER.debug("%s %s -> %s %s", method, path, resp.status, text[:500])
        if resp.status == 401 and auth:
            raise InvalidAuth(f"{method} {path}")
        if resp.status != 200:
            raise ApiError(method, path, resp.status)
        text = text.strip()
        # Setters answer with an empty body, "Ok" or a tiny HTML page.
        if not text or text[0] not in "[{":
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # TPN256E sends malformed JSON for activities/tv.
            _LOGGER.debug("Ignoring malformed JSON from %s %s", method, path)
            return None

    async def get_system(self) -> dict[str, Any]:
        """Return the raw /system response, no credentials needed."""
        return await self._request("GET", "/system", auth=False)

    async def pair_request(self, featuring: dict[str, Any]) -> PairingSession:
        """Make the TV show a PIN."""
        device_id = secrets.token_hex(8)
        data = await self._request(
            "POST",
            "/pair/request",
            {
                "access": {"scope": _PAIRING_SCOPE, "featuring": featuring},
                "device": _device(device_id),
            },
            auth=False,
        )
        if not data or data.get("error_id") != "SUCCESS":
            raise PairingError(str(data))
        return PairingSession(
            device_id=device_id,
            auth_key=data["auth_key"],
            timestamp=int(data["timestamp"]),
            timeout=int(data.get("timeout", 30)),
        )

    async def pair_grant(self, pairing: PairingSession, pin: str) -> str:
        """Confirm the PIN and return the auth key to store."""
        signature = hmac.new(
            _SHARED_KEY, f"{pairing.timestamp}{pin}".encode(), hashlib.sha1
        ).digest()
        self.set_credentials(pairing.device_id, pairing.auth_key)
        try:
            data = await self._request(
                "POST",
                "/pair/grant",
                {
                    "auth": {
                        "pin": pin,
                        "auth_timestamp": pairing.timestamp,
                        "auth_signature": base64.b64encode(signature).decode(),
                        "auth_appId": "1",
                        "auth_key": pairing.auth_key,
                    },
                    "device": _device(pairing.device_id),
                },
            )
        except (InvalidAuth, ApiError) as err:
            self._auth = None
            raise PairingError(str(err)) from err
        if not data or data.get("error_id") != "SUCCESS":
            self._auth = None
            raise PairingError(str(data))
        auth_key = data.get("auth_key") or pairing.auth_key
        self.set_credentials(pairing.device_id, auth_key)
        return auth_key

    async def is_on(self) -> bool:
        """Return whether the TV is on rather than in standby."""
        try:
            async with asyncio.timeout(2):
                _, writer = await asyncio.open_connection(self.host, CAST_PORT)
        except OSError, TimeoutError:
            return False
        writer.close()
        return True

    async def get_network_devices(self) -> list[dict[str, Any]]:
        """Return the network interfaces including MAC and WoL state."""
        return await self._request("GET", "/network/devices") or []

    async def get_current_activity(self) -> str | None:
        """Return "watchtv", "extension" (HDMI) or "browser" (home screen)."""
        data = await self._request("GET", "/activities/current")
        return (data or {}).get("id")

    async def send_key(self, key: str) -> None:
        """Send a remote control key."""
        await self._request("POST", "/input/key", {"key": key})

    async def get_ambilight_styles(self) -> list[dict[str, Any]]:
        """Return the supported styles with their algorithms."""
        data = await self._request("GET", "/ambilight/supportedstyles")
        return (data or {}).get("supportedStyles", [])

    async def set_ambilight_style(self, style: str, setting: str | None) -> None:
        """Switch Ambilight on with a style and optional algorithm."""
        body: dict[str, Any] = {"styleName": style, "isExpert": False}
        if setting:
            body["menuSetting"] = setting
        await self._request("POST", "/ambilight/currentconfiguration", body)

    async def set_ambilight_color(
        self, hue: int, saturation: int, brightness: int
    ) -> None:
        """Switch Ambilight on with a fixed color, each channel 0-255."""
        await self._request(
            "POST",
            "/ambilight/currentconfiguration",
            {
                "styleName": "FOLLOW_COLOR",
                "isExpert": True,
                "algorithm": "MANUAL_HUE",
                "colorSettings": {
                    "color": {
                        "hue": hue,
                        "saturation": saturation,
                        "brightness": brightness,
                    },
                    "colorDelta": {"hue": 0, "saturation": 0, "brightness": 0},
                    "speed": 255,
                },
            },
        )

    async def get_menu_structure(self) -> dict[str, Any]:
        """Return the root node of the settings menu."""
        data = await self._request("GET", "/menuitems/settings/structure")
        return (data or {}).get("node", {})

    async def activate_menu_node(self, node_id: int) -> None:
        """Trigger a settings menu node.

        Ambilight off works only this way; style OFF and ambilight/power are
        acknowledged but ignored. It also updates the menu, which the TV
        restores after standby.
        """
        await self._request(
            "POST",
            "/menuitems/settings/update",
            {
                "values": [
                    {
                        "value": {
                            "Nodeid": node_id,
                            "nodeid": node_id,
                            "node_id": node_id,
                        }
                    }
                ]
            },
        )

    async def get_channel_db(self) -> dict[str, Any]:
        """Return the channel and favorite lists."""
        return await self._request("GET", "/channeldb/tv")

    async def get_channels(self, list_id: str) -> list[dict[str, Any]]:
        """Return the channels of a list."""
        data = await self._request(
            "GET", f"/channeldb/tv/channelLists/{list_id}", request_timeout=60
        )
        return (data or {}).get("Channel", [])

    async def set_channel(self, ccid: int, list_id: str) -> None:
        """Tune to a channel, switching to live TV if needed."""
        await self._request(
            "POST",
            "/activities/tv",
            {"channel": {"ccid": ccid}, "channelList": {"id": list_id}},
        )


class _WakeOnLanProtocol(asyncio.DatagramProtocol):
    pass


async def wake_on_lan(mac: str, broadcast: str = "255.255.255.255") -> None:
    """Send a magic packet."""
    payload = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "").replace("-", "")) * 16
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        _WakeOnLanProtocol, remote_addr=(broadcast, 9), allow_broadcast=True
    )
    try:
        transport.sendto(payload)
    finally:
        transport.close()
