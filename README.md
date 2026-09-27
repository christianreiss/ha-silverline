# Poolex Silverline — Home Assistant integration

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Custom%20Integration-41BDF5?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Tests](https://github.com/christianreiss/ha-silverline/actions/workflows/tests.yaml/badge.svg)](https://github.com/christianreiss/ha-silverline/actions/workflows/tests.yaml)
[![hassfest](https://github.com/christianreiss/ha-silverline/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/christianreiss/ha-silverline/actions/workflows/hassfest.yaml)
[![HACS validation](https://github.com/christianreiss/ha-silverline/actions/workflows/hacs.yaml/badge.svg)](https://github.com/christianreiss/ha-silverline/actions/workflows/hacs.yaml)
[![Last commit](https://img.shields.io/github/last-commit/christianreiss/ha-silverline)](https://github.com/christianreiss/ha-silverline/commits/main)

Local-only Home Assistant integration for **Poolex Silverline FI** pool heat
pumps (Tuya v3.3 / v3.4 / v3.5) and OEM siblings. Connects directly over LAN —
**no cloud runtime dependency**.

## At a glance

- ✅ Full `climate` support (`off / heat / cool / heat_cool`)
- ✅ Presets incl. boost + silent variants that standard Tuya integrations miss
- ✅ Firmware-aware diagnostics + fault sensors
- ✅ Reauth/reconfigure flow for key/IP changes
- ✅ HACS-installable, multilingual (EN/DE/FR/ES/IT/NL/PL)

## Features

- One `climate` entity (`off / heat / cool / heat_cool`) with three presets
  (`none`, `boost`, `eco` — shown as Inverter / Boost / Silent) covering all seven device modes including
  Boost-Cool and Silent-Cool, which the official HA Tuya integration cannot
  reach (see [home-assistant/core#117566][issue-117566]).
- Up to 30 firmware-dependent diagnostic sensors — water/ambient/coil/refrigerant
  temperatures, compressor frequency and load, EEV/valve steps, fan and pump
  rpm, runtime counters — plus a decoded fault-code sensor. Only the DPs your
  firmware actually exposes become entities, so there is no `unavailable`
  clutter ([sensors by firmware](https://github.com/christianreiss/ha-silverline/blob/main/docs/supported-devices.md#sensors-by-firmware)).
- Binary sensors for compressor running and water pump (each only where the
  firmware reports it), plus one per decoded fault bit; active faults also
  raise self-clearing Repair issues.
- A power `switch`, a target-temperature `number`, and two `select`
  entities (preset, operating mode) expose the climate controls
  individually for automations.
- On firmware that reports line voltage and current (DP 120/121), a derived
  electrical-power sensor and a lifetime energy counter in kWh — the latter
  restores across restarts and can feed the HA Energy Dashboard directly,
  with no Riemann-sum helper to wire up.
- Reauth flow when the local key rotates, reconfigure flow for IP changes,
  and an options flow for the fallback polling interval.
- Full diagnostics download with secrets redacted.
- Translations for English, German, French, Spanish, Italian, Dutch, and Polish.

## Supported devices

The Tuya schema is shared across the Poolex Silverline FI family and several
OEM siblings; the integration is expected to work with all of them. Three
firmware generations have been verified directly against live hardware: the
PC-SLP090N (v3.3), a v3.4 Poolex Silverline, and a v3.5 JetLine FI with the new
WiFi control board (issue #7).

| Model | Protocol | Climate (off/heat/cool/auto) | Presets (boost/silent) | Diagnostics (DP 101–111) | Fault sensors | Status |
|---|---|---|---|---|---|---|
| Poolex PC-SLP090N (Silverline FI 90; also sold as "PoolStar OPool Premium 90") | v3.3 | ✅ | ✅ | ❌ none (5-DP firmware) | ✅ | 🟢 live-verified |
| Poolex Silverline FI 70 / PC-SLP070N | v3.3 | ✅ | ✅ | ❌ none (5-DP firmware) | ✅ (bit 6 undecoded — [why](https://github.com/christianreiss/ha-silverline/blob/main/docs/supported-devices.md#model-and-firmware-notes)) | 🟢 user-verified |
| Poolex Silverline FI 120 / 180 / 200 | v3.3 | ✅ | ✅ | ❓ firmware-dependent | ✅ | 🔵 inferred |
| Poolex Silverline FI 120 (Full Inverter) | v3.5 | Heating reported; other modes unverified | Unverified | ✅ FI 150 DP map — select **Silverline FI 120 (Tuya v3.5 / Full Inverter)** | Full Inverter table (inherited) | 🟢 telemetry confirmed by reporter ([issue #22](https://github.com/christianreiss/ha-silverline/issues/22)) |
| Poolex Silverline FI 150 | v3.5 | ✅ | ✅ | ✅ own DP map (IPM temperature, main EEV opening, AC voltage/current) — select the **Silverline FI 150** profile | ✅ | 🟢 DP map from a live load-transition test ([issue #20](https://github.com/christianreiss/ha-silverline/issues/20)) |
| Poolex Silverline FI 120 V2 / PC-INV-120V2 | v3.3 | ✅ | ✅ | ❌ none (5-DP, tenths °C) | ❌ (DP 9, not 13) | 🟢 user-verified |
| Poolex JetLine Selection FI | v3.3 | ✅ | ✅ | ❓ firmware-dependent (some units 5-DP) | ✅ | 🟢 user-verified |
| Poolex JetLine FI (new v3.5 WiFi control board) | v3.5 | ✅ | ✅ | ✅ full | ✅ | 🟢 live-verified |
| Brustec BR series | v3.3 / v3.5 | ✅ | ✅ | ✅ full | ✅ | 🔵 inferred |
| Steinbach Silent Mini | v3.4 | ✅ (DP 4 uses `Heating`/`Cooling`) | ⚠️ boost/silent fall back to the plain mode string (unconfirmed on this firmware) | ⚠️ partial (DP 101–104, 106–108) | ❌ (no DP 13 reported by the verified unit) | 🟢 user-verified (both directions, [issue #10](https://github.com/christianreiss/ha-silverline/issues/10)) |
| Phalén Calidi XP | v3.3 / v3.5 | ✅ | ✅ | ✅ full | ✅ | 🔵 inferred |
| Nulite | v3.3 / v3.5 | ✅ | ✅ | ✅ full | ✅ | 🔵 inferred |
| Poolex Silverline (Tuya v3.4 firmware) | v3.4 | ✅ | ✅ | ✅ full (own DP map) | ✅ | 🟢 live-verified |
| Poolex Nano Fi 3kW / 5kW (PC-NANO-B3N / B5N) | v3.5 | ✅ | ✅ | ✅ own DP map (own AC voltage/current sensors too) | ✅ | 🟢 live-verified |
| Poolex F-Spa 7kW | auto | ✅ | ✅ | ✅ Nano Fi DP map — select the **Nano Fi 3kW / 5kW** profile (a community DP dump matches it DP-for-DP, [thread post 23](https://community.home-assistant.io/t/1011340/23); not yet run under this integration) | ✅ | 🔵 inferred |
| Poolex Nano 5kW WiFi (also sold as "Spawler o'spa Flow 5kW", "Varpoolfaye Pool Mini") | v3.4 / v3.5 | ✅ | ❓ unconfirmed (falls back to plain heat/cool) | ❌ none (5-DP firmware) | ✅ (DP 21 water-flow bit only) | 🟢 live-verified |
| Other Poolstar / Tuya WBR3 OEM | auto | ✅ | ✅ | live-detected | ✅ | ⚪ unknown |

**Legend** — 🟢 live-verified (verified directly by me, or by a community
reporter's device) · 🔵 high confidence (same OEM platform, not tested
directly) · ⚪ unknown · ✅ present · ❌ absent · ❓ firmware-dependent

The protocol version (v3.3 / v3.4 / v3.5) is auto-detected during setup and
pinned on the config entry. Which profile to pick, per-firmware sensor coverage and
model quirks are in the [model notes](https://github.com/christianreiss/ha-silverline/blob/main/docs/supported-devices.md).

## Installation

### Via HACS (recommended)

1. In HACS, open the "⋮" menu (top right) → "Custom repositories".
2. Add `https://github.com/christianreiss/ha-silverline` as type
   "Integration".
3. Install **Poolex Silverline** from the new entry, restart Home Assistant.

> **Why the custom-repository step?** This integration has been submitted to
> the HACS *default* store, but that queue is measured in months. Until it is
> merged, adding the repository by hand is the supported install path — the
> steps above are not a workaround and nothing changes for you when the
> listing lands, beyond being able to skip step 1 and 2.

### Manual

Copy the `custom_components/poolex_silverline/` directory into your Home
Assistant `config/custom_components/` and restart.

## Setup

You need four pieces of information:

| Field | What it is | Where to find it |
|---|---|---|
| Host / IP | The heat pump's address on your LAN | Router DHCP leases, `nmap`, or `python -m tinytuya scan` |
| Port | TCP port (default 6668) | Always 6668 unless you changed it |
| Device ID | The 22-character Tuya device ID | Tuya IoT Platform → "Cloud" → "Devices", or `tinytuya wizard` |
| Local key | The 16-character device-specific encryption key | Same place — re-issued whenever the device is re-paired in Smart Life |

Then in HA: **Settings → Devices & Services → "Add integration" → search for
"Poolex Silverline"** and fill in the form.

The integration validates the credentials and confirms it can reach the
device before creating the config entry. A failure surfaces as
`cannot_connect` (network/host), `invalid_key_format` (the local key is not
exactly 16 characters, checked before contacting the device),
`invalid_auth` (local key rejected — usually a wrong key) or `unknown` (unexpected error) right in
the form.

After the credentials validate, a second step asks for your **device model**
in a dropdown. The choice sets the device's model name in Home Assistant and
pre-configures which sensors are available for that model. If your model is
not listed, pick **Other / Unknown** — the integration then detects the
supported sensors automatically from the device's first status report, read
with the standard DP layout. Pick your listed model whenever there is one:
models with their own DP map (Nano Fi, FI 150, FI 120 v3.5, the v3.4
Silverline) get mislabelled sensors under **Other / Unknown**.

## Configuration

Connection settings are changed with **Reconfigure** on the device; the
fallback polling interval (default 30 s, minimum 8 s) lives under
**Configure** on the integration entry. v3.3 and v3.5 devices push changes
within ~200 ms, and the poll keeps the full DP set fresh. Details, including
per-mode setpoint ranges: [configuration](https://github.com/christianreiss/ha-silverline/blob/main/docs/configuration.md).

## Documentation

- [Model notes](https://github.com/christianreiss/ha-silverline/blob/main/docs/supported-devices.md) — profile selection, sensors by firmware, per-model quirks
- [Configuration](https://github.com/christianreiss/ha-silverline/blob/main/docs/configuration.md) — options, data update model, per-mode setpoints
- [Known limitations](https://github.com/christianreiss/ha-silverline/blob/main/docs/known-limitations.md) — cloud-gated telemetry, °F, Auto presets
- [Troubleshooting](https://github.com/christianreiss/ha-silverline/blob/main/docs/troubleshooting.md) — setup errors, missing sensors, reporting a problem
- [Use cases and example automations](https://github.com/christianreiss/ha-silverline/blob/main/docs/automations.md)
- [Development and releases](https://github.com/christianreiss/ha-silverline/blob/main/docs/development.md)

## Troubleshooting

See the [troubleshooting guide](https://github.com/christianreiss/ha-silverline/blob/main/docs/troubleshooting.md). To report a problem,
run the bundled diagnostic tool — it redacts your device id, local key and IP —
and paste its output into a
[bug report](https://github.com/christianreiss/ha-silverline/issues/new/choose):

```bash
pip install pysilverline
pysilverline diagnose
```

## Removal

1. **Settings → Devices & Services → Poolex Silverline → ⋮ → Delete**.
2. Optionally uninstall via HACS or remove
   `custom_components/poolex_silverline/` from your config.

## Why a custom integration instead of LocalTuya?

LocalTuya conflates HVAC mode (DP 1: power) and operating mode (DP 4:
seven-string enum) onto a single bound DP. This collapses preset
information, so users can't toggle Boost or Silent through the climate
entity reliably. The official Tuya cloud component has a similar bug
(see [home-assistant/core#117566][issue-117566]).

This integration models the device's two-DP state machine cleanly: power
maps to HVAC mode on/off, the DP-4 enum prefix becomes the preset, the
suffix becomes heat/cool. All seven modes are accessible.

## Related projects

- [`pysilverline`](./pysilverline) — the underlying async Tuya v3.3 / v3.4 /
  v3.5 client, reusable outside Home Assistant on Python 3.12+.
- [`tinytuya`](https://github.com/jasonacox/tinytuya) — generic Tuya local
  protocol library that informed parts of the protocol implementation.
- [`tuya-local`](https://github.com/make-all/tuya-local) — community Tuya
  integration with extensive device YAMLs; the source for several of the
  DP mappings used here.

## Acknowledgments

Hardware verification on devices the maintainer doesn't own is what keeps this
integration honest — thanks to everyone who ran the tests; see
[acknowledgments](https://github.com/christianreiss/ha-silverline/blob/main/docs/acknowledgments.md).

## License

MIT — see [LICENSE](./LICENSE).

[issue-117566]: https://github.com/home-assistant/core/issues/117566
