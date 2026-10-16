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

Several GET endpoints return what was last set through the API, not what the
TV is doing:

| Endpoint | Behavior |
| --- | --- |
| `audio/volume` | `current` does not follow the remote or key presses; `muted` does |
| `ambilight/mode` | unrelated to power or Ambilight; flips between `internal` and `unknown` |
| `screenstate` | `Off` while the picture is on |
| `powerstate` | 404 |
| `activities/tv` | 404 while live TV runs, so the current channel cannot be read |

What does reflect reality:

* `activities/current`: `browser` on the home screen, `extension` on an HDMI
  input, `watchtv` in live TV. Meaningless in standby.
* The Cast receiver on port 8009. It closes about 11 seconds after standby
  and opens 15 to 30 seconds after waking. JointSpace itself, port 1926, and
  its mDNS announcement stay up in standby, so they say nothing about power.
  `is_on()` depends on this; a TV with Chromecast switched off always reads as
  off. The TV also wakes itself with the screen dark and Cast up (seen on
  2026-10-21 for 26 minutes, until switched off). Nothing found tells this
  apart from on: the same ports are open, and of the endpoints only
  `ambilight/mode`, `activities/current`, `context` and the LED colors
  differ, all of which also change while the picture is on.
* `ambilight/currentconfiguration` follows the menu and the remote; an empty
  body means off. Right after an API write it echoes the write, which once
  was not what the LEDs did. ha-philipsjs distrusts it on all Linux firmware
  (`quirk_ambilight_mode_ignored`); on this TV it was right in every test on
  2026-10-20.
* `ambilight/processed`: the colors the LEDs show. The only source for a
  fixed color: the configuration never carries one, and after a color set
  through the API it even reads `FOLLOW_COLOR` / `PTA_LOUNGE` ("Cool White").
  All zero with an audio style and no sound, so it does not tell on from off.

The TV's menu items, as `menuitems/settings/structure` lists them, and what
the configuration reads after selecting each:

| Menu | Node / item | `string_id` | `styleName` / `menuSetting` |
| --- | --- | --- | --- |
| Aus | 310 | `…AMBILIGHT_STYLE_OFF` | empty |
| AI | 320 / 321 | `MLM_PHM_KEY_MISC_AI` | `FOLLOW_VIDEO` / `NATURAL` |
| Standard | 320 / 322 | `…AMBILIGHT_STYLE_VIDEO_1_2K20` | `FOLLOW_VIDEO` / `STANDARD` |
| Sport | 320 / 323 | `…AMBILIGHT_STYLE_VIDEO_3_2K20` | `FOLLOW_VIDEO` / `SPORTS` |
| Kino | 320 / 324 | `…AMBILIGHT_CINEMA` | `FOLLOW_VIDEO` / `NATURAL` |
| Spiele | 320 / 325 | `…AMBILIGHT_GAME_2K23` | `FOLLOW_VIDEO` / `GAME` |
| Musik | 330 / 331 | `…AMBILIGHT_STYLE_AUDIO_1_2K20` | `FOLLOW_AUDIO` / `ENERGY_ADAPTIVE_BRIGHTNESS` |
| not shown | 330 / 332 | `…FOLLOW_AUDIO_STYLE_3_2K23` | `FOLLOW_AUDIO` / `PARTY` |
| not shown | 330 / 333 | `…AMBILIGHT_STYLE_AUDIO_3_2K20` | `FOLLOW_AUDIO` / `RANDOM_PIXEL_FLASH` |
| Feste Farben | 340 / 345 | `…AMBILIGHT_FIXED_COLOUR` | `FOLLOW_COLOR`, nothing else |

`…` stands for `MLM_PHM_KEY_MAIN_`. The integration maps styles to items by
`string_id`; a model with other IDs falls back to
`ambilight/currentconfiguration`. Setting `NATURAL` through the API gives the
calm behavior of Kino, not AI.

## Commands

| Action | Works | Ignored or refused |
| --- | --- | --- |
| Power on | Wake-on-LAN to the WLAN MAC, near instant | |
| Power off | key `Standby` | |
| Volume, mute | keys `VolumeUp`, `VolumeDown`, `Mute` | `POST audio/volume`: dropped or mutes instead, on HDMI |
| Ambilight on, style | `POST ambilight/currentconfiguration` with `FOLLOW_VIDEO`, `FOLLOW_AUDIO` plus algorithm; for a menu style first its menu node and list item via `menuitems/settings/update` | |
| Ambilight color | the same in expert mode, `FOLLOW_COLOR` / `MANUAL_HUE`, hue, saturation, brightness 0-255 | |
| Ambilight off | menu node with context `ambilight_off` via `menuitems/settings/update` | style `OFF`, `ambilight/power` |
| Channel | `POST activities/tv` with `ccid` and list ID, also from the home screen | |
| Channel up / down | keys `ChannelStepUp`, `ChannelStepDown` | |
| Input | waking a device with HDMI-CEC on that input | `sources`, `sources/current` 403, `POST activities/current` 405 |
| App | | `applications` 403, `activities/launch` 405 |

`sources`, `applications`, `quickmenu` and `matter` answer 403 even to a
device that listed them in `featuring` when pairing. The menu of the `Source`
key lists the inputs in no numeric order and opens on the current one, so
keys cannot reliably reach a given input.

Ambilight set through `currentconfiguration` bypasses the TV's settings menu.
The menu keeps showing the old value, and after standby the TV restores the
menu value. Only menu nodes write it. A settings menu that is open on screen
does not refresh; reopened, it shows the new value.

`supportedstyles` lists algorithms for `FOLLOW_AUDIO` only, all settable
through the API though the menu offers just one. The settings offered for
`FOLLOW_VIDEO` are the four the menu sets.

Selecting the menu entry that is already checked does nothing, even when the
LEDs show something else set through the API.

Woken through Wake-on-LAN, the TV leaves Ambilight off, even after a standby
of seconds and with a style in its menu; switched on with the remote, it
applies the menu. `POST menuitems/settings/current` with
`{"nodes": [{"nodeid": …}]}` reads the menu: the style node (300) has the
active child in `activenode_id`, 310 meaning off, and each list node the
item in `selected_item`. The integration applies that style when the TV comes
on with Ambilight off after its own Wake-on-LAN, not when Cast comes up
otherwise, which may be the TV waking itself in the dark. Restoring the fixed
colors or AI items, which have no style of their own, selects the menu item
again, which may change nothing.

Menu nodes are looked up by context, not hard-coded. The body sends the node
ID under `Nodeid`, `nodeid` and `node_id`, a list item as
`data.selected_item`.

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
* `ambilight/currentconfiguration` no longer following the remote, as
  ha-philipsjs reports for other Linux TVs: the light shows a stale state.
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
