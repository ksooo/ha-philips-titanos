# Keeping up with Titan OS

The integration talks to the JointSpace API of the TV, version 6, and nothing
about that API is documented. Everything below was measured on a 43PUS8400/12
(model TPN256E, API 6.1.0) in October 2026. Most of it contradicts what older
Philips TVs do and what other projects assume, so a firmware update can change
any of it.

## The API

`custom_components/philips_titanos/api.py`

| What | Value |
| --- | --- |
| Transport | HTTPS on port 1926, self-signed certificate without SAN |
| Path prefix | `/6` |
| Authentication | Digest, user = device ID, password = auth key from pairing |
| Unauthenticated | `GET /6/system` only |

Port 1925 serves the same API over plain HTTP; `/system` works there, the
authenticated endpoints tried answered 403.

`/system` carries model, serial number and software version only encrypted.
They are AES-128-CBC with the first 16 bytes of the shared pairing key, the IV
in the first block. The serial number is the config entry's unique ID; if
Philips changes the key, setup aborts with "not supported".

## Pairing

`POST /6/pair/request` makes the TV show a PIN and returns `auth_key`,
`timestamp` and `timeout` (30 seconds). `POST /6/pair/grant`, already digest
authenticated with the pending `auth_key`, confirms it with an HMAC-SHA1
signature over timestamp and PIN. The grant returns no new key; the pending
one stays the password.

## State: what can be read

Most GET endpoints return what was last set through the API, not what the TV
is doing:

| Endpoint | Behavior |
| --- | --- |
| `audio/volume` | does not follow the remote or key presses |
| `ambilight/power`, `ambilight/currentconfiguration` | report the last API write, even when it had no effect |
| `ambilight/mode` | unrelated to power or Ambilight; flips between `internal` and `unknown` |
| `screenstate` | `Off` while the picture is on |
| `powerstate` | 404 |
| `activities/tv` | 404 while live TV runs, so the current channel cannot be read |

For Ambilight this is confirmed upstream: ha-philipsjs remembers the last
mode it set on every TV with `os_type` Linux (`quirk_ambilight_mode_ignored`).
The other rows are our own measurements.

What does reflect reality:

* `activities/current`: `browser` on the home screen, `extension` on an HDMI
  input, `watchtv` in live TV. Meaningless in standby.
* The Cast receiver on port 8009. It closes about 11 seconds after standby
  and opens 15 to 30 seconds after waking. JointSpace itself, port 1926, and
  its mDNS announcement stay up in standby, so they say nothing about power.
  `is_on()` depends on this; a TV with Chromecast switched off always reads as
  off.

## Commands

| Action | Works | Ignored or refused |
| --- | --- | --- |
| Power on | Wake-on-LAN to the WLAN MAC, near instant | |
| Power off | key `Standby` | |
| Volume, mute | keys `VolumeUp`, `VolumeDown`, `Mute` | `POST audio/volume`: dropped or mutes instead, on HDMI |
| Ambilight on, style | `POST ambilight/currentconfiguration` with `FOLLOW_VIDEO`, `FOLLOW_AUDIO` plus algorithm | |
| Ambilight color | the same in expert mode, `FOLLOW_COLOR` / `MANUAL_HUE`, hue, saturation, brightness 0-255 | |
| Ambilight off | menu node with context `ambilight_off` via `menuitems/settings/update` | style `OFF`, `ambilight/power` |
| Channel | `POST activities/tv` with `ccid` and list ID, also from the home screen | |
| Channel up / down | keys `ChannelStepUp`, `ChannelStepDown` | |
| Input, app | | `sources` 403, `applications` 403, `activities/launch` 405 |

Ambilight set through `currentconfiguration` bypasses the TV's settings menu.
The menu keeps showing the old value, and after standby the TV restores the
menu value. Only the menu node writes it.

The menu node ID is looked up by context, not hard-coded. The body sends the
ID under `Nodeid`, `nodeid` and `node_id`.

## Breaks loudly

* A changed pairing signature or key: the grant fails, setup shows "invalid
  PIN" for every PIN.
* An endpoint that starts answering 403 or 404: the action raises `ApiError`
  and shows up in the log.

## Breaks quietly

* A firmware that stops ignoring a command or starts reporting real state.
  Nothing fails, but the integration keeps its own state where it no longer
  needs to.
* Cast moved to another port or kept alive in standby: power reads wrong.
* `activities/tv` answers with malformed JSON on this model; the client
  ignores bodies it cannot parse, so any other endpoint doing the same reads
  as empty.

## Not used

* `notifychange`: one long poll per device, held up to about 120 seconds,
  answered empty when nothing changed. A request the client abandons keeps
  the slot busy and makes further requests fail with 503. It reported
  neither power nor channel changes.
* Aurora: the endpoints exist, but opening it is refused with 405 and this
  model has no Aurora in its menus.
