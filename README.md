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
  (`inverter`, `boost`, `silent`) covering all seven device modes including
  Boost-Cool and Silent-Cool, which the official HA Tuya integration cannot
  reach (see [home-assistant/core#117566][issue-117566]).
- Up to 25 firmware-dependent diagnostic sensors (13 of them disabled by
  default): water/ambient/coil/refrigerant temperatures, target/actual
  compressor frequency and load, EEV/valve steps, fan and pump rpm,
  runtime counters, and a decoded fault-code enum. The integration only
  registers entities for the DPs your firmware actually exposes — the
  minimal Poolex PC-SLP090N firmware ships five DPs and gets none of the
  101–111 diagnostics, while the Brustec / Steinbach variants ship the
  full set.
- 12 binary sensors: compressor running, water pump, and all ten fault
  bits — the five most common (water flow, antifreeze, high/low pressure,
  communication) enabled by default, the other five (inverter comms and
  the four temperature-sensor faults) disabled by default.
- A power `switch`, a target-temperature `number`, and two `select`
  entities (preset, operating mode) expose the climate controls
  individually for automations.
- Reauth flow when the local key rotates and reconfigure flow for IP
  changes.
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
| Poolex PC-SLP090N (Silverline FI 90) | v3.3 | ✅ | ✅ | ❌ none (5-DP firmware) | ✅ | 🟢 live-verified |
| Poolex Silverline FI 120 / 180 / 200 | v3.3 | ✅ | ✅ | ❓ firmware-dependent | ✅ | 🔵 inferred |
| Poolex Silverline FI 120 V2 / PC-INV-120V2 | v3.3 | ✅ | ✅ | ❌ none (5-DP, tenths °C) | ❌ (DP 9, not 13) | 🟡 user-reported |
| Poolex JetLine Selection FI | v3.3 | ✅ | ✅ | ❓ firmware-dependent (some units 5-DP) | ✅ | 🟡 user-reported |
| Poolex JetLine FI (new v3.5 WiFi control board) | v3.5 | ✅ | ✅ | ✅ full | ✅ | 🟢 live-verified |
| Brustec BR series | v3.3 / v3.5 | ✅ | ✅ | ✅ full | ✅ | 🔵 inferred |
| Steinbach Silent Mini | v3.3 / v3.5 | ✅ | ✅ | ✅ full | ✅ | 🔵 inferred |
| Phalén Calidi XP | v3.3 / v3.5 | ✅ | ✅ | ✅ full | ✅ | 🔵 inferred |
| Nulite | v3.3 / v3.5 | ✅ | ✅ | ✅ full | ✅ | 🔵 inferred |
| Poolex Silverline (Tuya v3.4 firmware) | v3.4 | ✅ | ✅ | ✅ full (own DP map) | ✅ | 🟢 live-verified |
| Poolex Nano Fi 3kW (PC-NANO-B3N) | v3.5 | ✅ | ✅ | ✅ own DP map (own AC voltage/current sensors too) | ✅ | 🟢 live-verified |
| Other Poolstar / Tuya WBR3 OEM | auto | ✅ | ✅ | live-detected | ✅ | ⚪ unknown |

**Legend** — 🟢 live-verified · 🔵 high confidence (same OEM platform, not
tested directly) · 🟡 user-reported (confirmed from a reporter's device, not
in-house) · ⚪ unknown · ✅ present · ❌ absent · ❓ firmware-dependent

- **The protocol version is auto-detected** (probed in order v3.5 → v3.4 →
  v3.3) and can be pinned on the config entry. v3.5 — including local control
  writes — is now **live-verified against real v3.5 hardware** (see below);
  v3.4 is likewise validated against real hardware. The v3.4 probe is also
  field-tested as benign against the live v3.3 device (it falls back cleanly
  without disturbing the pump).
- **v3.5 local control is live-verified, and needs integration 0.9.14 or
  later.** Confirmed on a real Poolex JetLine FI with the new WiFi control
  board (productKey `b4zr9ugt1q8xn9af`), contributed by
  [@Paulus385](https://github.com/Paulus385) (issue #7). These newer boards
  require a 15-byte Tuya version header inside the encrypted payload on control
  writes (reads and the handshake are header-less); pysilverline < 0.4.9 /
  integration < 0.9.14 omitted it, so the board accepted local *reads* but
  silently rejected every local *write* with `retcode=0x01000000` — the symptom
  was "turns on from HA, then reverts to OFF after a few seconds." Update to
  0.9.14+ if you see that on a v3.5 unit.
- **v3.4 support is live-verified** on a real Poolex Silverline (productKey
  `wfzeiyn1ed3axxde`, 2026 firmware), contributed by Martin Čarek
  ([@olomouckyorel](https://github.com/olomouckyorel), PR #3). That firmware
  renumbers its DPs (fan on DP 114, swapped suction/outlet) and uses
  request-scoped sockets (closes TCP after each query, no heartbeat); both are
  handled when the **`Poolex Silverline (Tuya v3.4)`** model is selected.
- **Diagnostic DPs (101–111) are firmware-dependent, not model-dependent:**
  the same SKU can ship full or bare depending on its firmware. The
  integration only registers the DPs the first `DP_QUERY` returns, so missing
  diagnostics never show up as `unavailable` clutter.
- **The same product key spans full and minimal firmware.** Product key
  `3bhylhz5zhogklel` is shared across the PC-SLP090N and the JetLine Selection
  FI family, and some JetLine Selection FI 95 units report only the minimal
  5-DP set (`1, 2, 3, 4, 13`) — no DP 108, so no diagnostics (issue #6). If your
  unit only exposes those five DPs, the behaviour matches the PC-SLP090N
  profile.
- **The "Compressor" binary sensor needs the actual-compressor-frequency DP
  (DP 108 on the standard layout).** It is the only authoritative compressor
  telemetry; on firmware that doesn't expose it — including the minimal 5-DP
  units (issue #6) and the v3.4 `wfzeiyn1ed3axxde` firmware, where DP 108 is
  the indoor coil temperature instead — the sensor is not created at all (it
  would otherwise echo heating *demand* during the startup delay rather than
  real running state). The climate card's heating/cooling colour still
  reflects demand.
- **PC-INV-120V2 / Silverline FI 120 V2 reports water temperature in tenths
  of a degree.** This OEM Poolstar variant sends DP 3 as e.g. `277` for 27.7 °C
  (issue #5). Select the **`Poolex Silverline FI 120 V2 / PC-INV-120V2`** model
  during setup (or via *Reconfigure*) to apply the ÷10 scaling; the setpoint
  (DP 2) stays whole °C and is unaffected. Note this firmware uses a different
  DP-4 mode vocabulary (e.g. `h_powerful`); full mode mapping for it is still
  being collected.
- **°C only.** °F shifts the fault bitmap and is not supported (see
  [Known limitations](#known-limitations)).
- Presets `boost` / `eco` do not apply in `heat_cool` (Auto) — a device
  limitation.

## Installation

### Via HACS (recommended)

1. In HACS, open the integrations tab → "⋮" menu → "Custom repositories".
2. Add `https://github.com/christianreiss/ha-silverline` as type
   "Integration".
3. Install **Poolex Silverline** from the new entry, restart Home Assistant.

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
`cannot_connect` (network/host) or `invalid_auth` (wrong local key) right
in the form.

After the credentials validate, a second step asks for your **device model**
in a dropdown. The choice sets the device's model name in Home Assistant and
pre-configures which sensors are available for that model. If your model is
not listed (or you are unsure), pick **Other / Unknown** — the integration
then detects the supported sensors automatically from the device's first
status report.

## Configuration parameters

There is no options flow; all configuration happens during setup or via the
**Reconfigure** action on the device entry. To change the host, port, device
ID, local key, or device model after setup, click the three-dot menu on the
device in **Settings → Devices** and choose **Reconfigure**. Reconfigure
re-validates the connection details and then shows the same model dropdown,
pre-selected with the current choice.

## Data update model

- **Polling**: every 30 seconds the integration issues a Tuya `DP_QUERY` to
  refresh the full state. Polling faster than ~8 s causes the WBR3 WiFi
  module to reboot — don't lower this.
- **Push**: the device pushes spontaneous state changes within ~200 ms of
  any DP changing. The integration listens for those on the persistent
  socket and applies them immediately, so most updates feel instant.

## Known limitations

- **Diagnostic sensors are firmware-dependent.** DPs 101–111 are populated
  on the Brustec / Steinbach firmware variants; some Poolex Silverline FI
  firmwares (verified live: PC-SLP090N) only expose DPs 1, 2, 3, 4, and 13.
  Unsupported diagnostic DPs are not registered as entities at all — they
  do not appear in your device page, so they cannot show up as `unavailable`
  and clutter dashboards.
- **°F mode is not supported.** Lock the wired remote to °C — on °F some
  firmwares move the fault bitmap from DP 13 to DP 21 and reuse DP 13 for
  the unit-conversion enum, which the integration does not yet handle.
- **Auto mode has no Boost or Silent variant** — this is a device
  limitation. Selecting `boost` or `eco` while in `heat_cool` raises a
  service-validation error with a translated message.

## Per-mode setpoints

The device keeps a separate stored setpoint per mode-family (Heat, Cool,
Auto) and restores that mode's last value when you switch into it. For a
pool heat pump this is usually what you want — Heat is for warming the
pool (typically 26–30 °C), Cool is for chilling it during a heat wave
(typically 18–24 °C), and Auto holds a band in the middle. The setpoint
slider also adapts its min/max to the active mode (Heat 15–40 °C, Cool
8–28 °C, Auto 8–40 °C). If you change mode and target in one HA service
call (`climate.set_temperature` with both `hvac_mode` and `temperature`),
the mode change is applied first so your target lands under the new mode.

## Troubleshooting

**"cannot_connect" during setup**
- Verify the IP is reachable: `nc -vz <host> 6668`.
- Ensure the device is not already connected to the Smart Life app on the
  same network — the WBR3 only accepts one local TCP client at a time. The
  cleanest fix is to firewall the device from outbound 443/8886 so it stays
  LAN-only.

**"invalid_auth" during setup**
- The local key is regenerated whenever the device is re-paired in Smart
  Life. Re-fetch it from the Tuya IoT Platform after any Smart Life touch.

**A diagnostic sensor I expected is missing**
- Your firmware variant likely doesn't expose that DP, in which case
  the integration omits the entity on purpose (the alternative — a
  permanently `unavailable` sensor — confuses dashboards and template
  sensors). Compare with the supported DPs in the device's diagnostics
  download to confirm.

**Capturing a live DP dump for a bug report**
- The repository ships `scripts/probe.py`, which reads credentials
  from an `access.yaml` at the repo root and dumps every DP the device
  exposes. `access.yaml` holds your Tuya local_key, so after creating
  it run `chmod 600 access.yaml` to keep it readable only by your user.

**Boost or Silent doesn't apply when in Auto**
- This is a device limitation, not an integration bug. Switch to
  Heat or Cool first; the preset will then apply.

## Reporting a problem

`pysilverline` ships a diagnostic tool that gathers everything a maintainer
needs — the protocol probe ladder, the full DP map, and (optionally) the
control-write result — into one paste-ready report, with your device id, local
key and host/IP redacted. It runs without Home Assistant, so it also helps when
the integration won't connect at all.

Run it with **no arguments** and it walks you through everything — scanning your
network, letting you pick the heat pump, and asking for the local key:

```bash
pip install pysilverline
pysilverline diagnose
```

```text
Scanning for devices (6s)…
Devices found on your network:
  [1] 192.168.1.42  (productKey 3bhylhz5zhogklel, v3.3, id bf90…)
Pick a number, or press Enter to type the details manually: 1
Local key (16 characters): ••••••••••••••••
Protocol version (auto / 3.3 / 3.4 / 3.5) [auto]:
Also test the control path? … [y/N]: n
```

It then prints the report and offers to save it. Paste the output into a
[bug report](https://github.com/christianreiss/ha-silverline/issues/new/choose).

- Your **local key** comes from `python -m tinytuya wizard` or the Tuya IoT
  Platform (Cloud → Devices) — the same place you found it during setup. Home
  Assistant's diagnostics download redacts it deliberately, so don't look there.
- Say **yes** to the control-path test only if you want it: it writes the
  setpoint (DP 2) back to its *current* value, so it changes nothing, but it
  reveals whether local writes are accepted (the signal behind issue #7).
- Already running Home Assistant? *Download diagnostics* on the device page
  carries the same information.

<details>
<summary>Scripted / non-interactive use</summary>

Pass all three connection details (plus any options) as flags to skip the
prompts — handy for scripts, or when you already know them:

```bash
pysilverline diagnose --host <device-ip> --device-id <id> --local-key <key> \
    [--probe-write] [--version 3.5] [--output report.md] [--no-redact]
```
</details>

## Use cases

- **Seasonal pool warmup.** Set `hvac_mode: heat` with the `boost`
  preset and a 28 °C target in spring; the heat pump runs at maximum
  inverter speed until the pool reaches setpoint, then naturally
  modulates down.
- **Overnight quiet operation.** Use the `eco` preset (mapped to the
  Silent DP variant) during sleeping hours — the compressor caps its
  frequency to a quieter rpm at the cost of slower heating.
- **PV-surplus heating.** Trigger `climate.set_temperature` from a
  template sensor watching your solar surplus: setpoint moves up by a
  few degrees when there's free electricity, back down when there
  isn't.
- **Frost protection in the off-season.** Park the unit at a low
  target with the `eco` preset; the inverter pulses only when water
  temperature drops near the antifreeze threshold.

## Examples

### Prevent dry-running by sequencing the filter pump first

The unit hard-faults to E03 (water flow) within ~30 s of running dry.
Always start the filter pump before turning the heat pump on:

```yaml
automation:
  - alias: "Pool: pump before heat"
    triggers:
      - platform: state
        entity_id: climate.pool_heatpump
        from: "off"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.pool_filter_pump
      - delay: "00:00:15"
```

### Heat only during low-tariff hours

Drop the setpoint to a frost-protection floor at peak-rate times; raise
it during the cheap window so the inverter runs when electricity is
cheapest. Pair with a tariff sensor or a static time schedule.

```yaml
automation:
  - alias: "Pool: warm during off-peak"
    triggers:
      - platform: time
        at: "22:00:00"
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.pool_heatpump
        data:
          temperature: 28
      - action: climate.set_preset_mode
        target:
          entity_id: climate.pool_heatpump
        data:
          preset_mode: boost

  - alias: "Pool: idle during peak"
    triggers:
      - platform: time
        at: "06:00:00"
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.pool_heatpump
        data:
          temperature: 18
      - action: climate.set_preset_mode
        target:
          entity_id: climate.pool_heatpump
        data:
          preset_mode: eco
```

### Notify when a fault appears (with self-clearing)

The integration also surfaces fault bits as Home Assistant **Repair
issues** (Settings → Repairs) that auto-clear when the device clears
the fault. For an active push notification on top of that, watch the
fault binary sensors directly:

```yaml
automation:
  - alias: "Pool: notify on water-flow fault"
    triggers:
      - platform: state
        entity_id: binary_sensor.pool_heatpump_water_flow_fault
        from: "off"
        to: "on"
    actions:
      - action: notify.mobile_app
        data:
          title: "Pool heat pump: E03 water flow"
          message: >
            The heat pump can't detect water flow. Check the filter
            pump. The unit stops heating until flow is restored.
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
  v3.5 client, reusable outside Home Assistant.
- [`tinytuya`](https://github.com/jasonacox/tinytuya) — generic Tuya local
  protocol library that informed parts of the protocol implementation.
- [`tuya-local`](https://github.com/make-all/tuya-local) — community Tuya
  integration with extensive device YAMLs; the source for several of the
  DP mappings used here.

## Acknowledgments

Hardware verification on devices the maintainer doesn't own is what keeps this
integration honest — huge thanks to the contributors who ran the tests:

- **[@Paulus385](https://github.com/Paulus385)** — verified v3.5 local control
  on a JetLine FI with the new WiFi control board, and whose Wireshark capture
  and tinytuya cross-check root-caused the missing-version-header write bug
  (issue #7).
- **Martin Čarek ([@olomouckyorel](https://github.com/olomouckyorel))** —
  contributed and live-verified v3.4 support on real Poolex Silverline hardware
  (PR #3).
- **[@lepatou](https://github.com/lepatou)** — contributed the dedicated Nano
  Fi 3kW (PC-NANO-B3N) DP layout and diagnostic sensor catalog, cross-checked
  DP-by-DP against the official Tuya cloud product schema and live hardware
  (issue #11, PRs #12 and #14).
- **[@trothe](https://github.com/trothe)** — reported the minimal 5-DP JetLine
  Selection FI variant and the compressor-sensor false positive, with an `Er 03`
  no-flow cross-check (issue #6).

## Development

After cloning, install the git hooks once:

```bash
./scripts/install-hooks.sh
```

This points `core.hooksPath` at the tracked `.githooks/` directory. The
`pre-commit` hook runs the `pysilverline` protocol/client API test suite
(Tuya **v3.3**, **v3.4** and **v3.5**) before every commit, so a change that
breaks any wire protocol can't land. It's the library suite only (fast, ~1–2 s);
linting, type-checking, and the Home Assistant integration tests are left to
CI and `scripts/platinum-gate.sh`.

Bypass the hook for a single commit with `git commit --no-verify`, or set
`SKIP_HOOK_TESTS=1` in the environment.

## Release notes

The Home Assistant integration pins `pysilverline` in `manifest.json`, so
publish the matching library release to PyPI before tagging an integration
release:

1. Create a PyPI account, then configure a Trusted Publisher for project
   `pysilverline`, repository `christianreiss/ha-silverline`, workflow
   `pysilverline-pypi.yaml` (in `.github/workflows/`), environment `pypi`.
2. Ensure `pysilverline/pyproject.toml` has the intended version.
3. Push `pysilverline-vX.Y.Z`; the PyPI workflow builds and publishes that
   exact version.
4. Verify `python -m pip index versions pysilverline` lists the version, then
   tag the Home Assistant integration release.

## License

MIT — see [LICENSE](./LICENSE).

[issue-117566]: https://github.com/home-assistant/core/issues/117566
