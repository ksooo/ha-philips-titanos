# Philips Titan OS TV for Home Assistant

Controls Philips TVs running Titan OS over their local JointSpace API, version
6. It is written for these TVs rather than adapted from the Android TV models
the built-in `philips_js` integration serves, because their firmware behaves
differently: most of what it reports is not what the TV is doing, see
[MAINTENANCE.md](MAINTENANCE.md).

Tested on a 43PUS8400/12. Other Titan OS models and the Linux-based Philips
TVs from 2023 report the same API and are accepted, but are untested.

**Status:** setup and pairing work; entities are not there yet.

## Requirements

* Home Assistant 2026.10 or newer
* The TV in the same network as Home Assistant, switched on for pairing
* Chromecast built-in left enabled on the TV. Its port is the only reliable
  sign of whether the TV is on.

No Python packages besides Home Assistant's own are needed.

## Installation

With [HACS](https://hacs.xyz): add `https://github.com/ksooo/ha-philips-titanos`
as a custom repository of type *Integration*, install **Philips Titan OS TV**
and restart Home Assistant.

Without HACS: copy `custom_components/philips_titanos` into the
`custom_components` folder of your configuration and restart.

## Setup

The TV announces itself via zeroconf and shows up under *Settings ->
Devices & services* as discovered. To add it by hand, choose *Add integration
-> Philips Titan OS TV* and enter its IP address.

1. Switch the TV on.
2. Confirm the discovered TV, or enter its address.
3. The TV shows a PIN for 30 seconds. Enter it.

If the TV is in standby, the dialog says so and offers to try again. A wrong
or expired PIN makes the TV show a new one.

During pairing the integration also reads the MAC address of the network
interface that has Wake-on-LAN enabled; it is needed to switch the TV on.

When the TV gets a new IP address, discovery updates it. *Reconfigure* on the
integration entry changes it by hand.

## Development

Tests and formatting run with [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run pytest
uv run ruff format
uv run ruff check
```

## License

Apache 2.0, see [LICENSE](LICENSE).
