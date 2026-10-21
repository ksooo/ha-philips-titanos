# Philips Titan OS TV for Home Assistant

Controls Philips TVs running Titan OS over their local JointSpace API, version
6. It is written for these TVs rather than adapted from the Android TV models
the built-in `philips_js` integration serves, because their firmware behaves
differently: most of what it reports is not what the TV is doing, see
[MAINTENANCE.md](MAINTENANCE.md).

Tested on a 43PUS8400/12. Other Titan OS models and the Linux-based Philips
TVs from 2023 report the same API and are accepted, but are untested.

**Status:** media player and remote done; Ambilight is not there yet.

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
integration entry changes it by hand. If the TV forgets the pairing, for
instance after a factory reset, Home Assistant asks to pair again.

## Media player

The TV appears as a media player of type TV. It reads as playing while live
TV runs, otherwise as on or off. The state is polled every 10 seconds.

| Feature | How |
| --- | --- |
| On | Wake-on-LAN; offered only if pairing found a MAC address |
| Off | the remote's Standby key, not sent while the TV is off |
| Volume up / down | the remote's volume keys |
| Mute | the remote's mute key; the state is read from the TV |
| Source | *Home* and *Live TV* can be chosen; *HDMI* is shown but cannot be selected |
| Channels | listed in the media browser, also playable by name or ID |
| Stop, next / previous | in live TV only: back to the home screen, channel up / down |

On and off take effect on the TV at once, but the TV confirms them only up to
30 seconds later. Until then the media player shows the requested state.

*Live TV* tunes to the channel last chosen through Home Assistant, or to the
first one. In an automation a channel is played like this:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.43pus8400_12
data:
  media_content_type: channel
  media_content_id: Euronews Deutsch
```

The TV does not report the volume or the current channel, so the media
player shows neither; it does not even know the channel it tuned to once the
remote was used. Inputs and apps cannot be switched through the API. A
device on an HDMI input with HDMI-CEC switches the TV to that input when it
wakes up, so waking it, for instance through its own integration, selects
the input.

## Remote

The remote entity switches the TV on and off like the media player and sends
keys of the TV's remote with `remote.send_command`, by their JointSpace
names:

```yaml
action: remote.send_command
target:
  entity_id: remote.43pus8400_12
data:
  command: [Home, CursorDown, Confirm]
  delay_secs: 0.5
```

Keys known to the API include `Standby`, `Home`, `Back`, `Source`,
`Options`, `Info`, `Find`, `CursorUp`, `CursorDown`, `CursorLeft`,
`CursorRight`, `Confirm`, `VolumeUp`, `VolumeDown`, `Mute`,
`ChannelStepUp`, `ChannelStepDown`, `Digit0` to `Digit9`, `Dot`,
`PlayPause`, `Pause`, `Stop`, `Rewind`, `FastForward`, `Record`, `Next`,
`Previous`, `RedColour`, `GreenColour`, `YellowColour`, `BlueColour`,
`WatchTV`, `Teletext`, `Subtitle`, `Viewmode`, `Adjust` and `Online`. Only
`Standby`, `Home`, `Back`, `Source`, `VolumeUp`, `VolumeDown`, `Mute`,
`ChannelStepUp` and `ChannelStepDown` are tested.
Holding a key is not supported.

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
