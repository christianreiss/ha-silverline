# Poolex Silverline — Home Assistant integration

Local-only Home Assistant integration for the **Poolex PC-SLP090N "Silverline
Inverter 90"** pool heat pump and its OEM siblings. Talks the Tuya local
protocol v3.3 directly over your LAN — no Smart Life cloud dependency at
runtime.

## Features

- One `climate` entity (`off / heat / cool / heat_cool`) with three presets
  (`inverter`, `boost`, `silent`) covering all seven device modes including
  Boost-Cool and Silent-Cool, which the official HA Tuya integration cannot
  reach (see [home-assistant/core#117566][issue-117566]).
- Up to eleven firmware-dependent diagnostic sensors: compressor
  exhaust/return temperatures, evaporator and ambient temperatures, water
  inlet/outlet temperatures, target/actual compressor frequency, EEV step
  count, fan rpm, and a decoded fault-code enum. The integration only
  registers entities for the DPs your firmware actually exposes — the
  minimal Poolex PC-SLP090N firmware ships five DPs and gets none of the
  101–111 diagnostics, while the Brustec / Steinbach variants ship the
  full set.
- Binary sensors for the water-pump relay and the five most common fault
  bits (water flow, antifreeze, high/low pressure, communication).
- Reauth flow when the local key rotates and reconfigure flow for IP
  changes.
- Full diagnostics download with secrets redacted.
- German and English translations.

## Supported devices

The Tuya schema is shared across the Poolex Silverline FI family and several
OEM siblings; the integration is expected to work with all of them, though
only the PC-SLP090N has been verified directly:

- Poolex Silverline FI 90 / 120 / 180 / 200 (PC-SLP090N, PC-SLP120N, …)
- Poolex JetLine Selection FI
- Steinbach Silent Mini
- Brustec BR series
- Phalén Calidi XP
- Other Poolstar-platform OEMs with a Tuya WBR3 module

## Installation

### Via HACS (recommended)

1. In HACS, open the integrations tab → "⋮" menu → "Custom repositories".
2. Add `https://github.com/christian-reiss/ha-silverline` as type
   "Integration".
3. Install **Poolex Silverline** from the new entry, restart Home Assistant.

### Manual

Copy the `custom_components/poolex_silverline/` directory into your Home
Assistant `config/custom_components/` and restart.

## Setup

You need three pieces of information from the Tuya cloud:

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

## Configuration parameters

There is no options flow in v0.1; all configuration happens during setup or
via the **Reconfigure** action on the device entry. To change the host, port,
device ID, or local key after setup, click the three-dot menu on the device
in **Settings → Devices** and choose **Reconfigure**.

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
- **Switching HVAC mode resets the target temperature.** The device
  keeps a separate stored setpoint per mode-family (Heat, Cool, Auto)
  and silently restores that mode's last value ~500 ms after a mode
  change. The integration's setpoint slider also adapts its min/max to
  the active mode (Heat 15–40 °C, Cool 8–28 °C, Auto 8–40 °C).

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

**Boost or Silent doesn't apply when in Auto**
- This is a device limitation, not an integration bug. Switch to
  Heat or Cool first; the preset will then apply.

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

- [`pysilverline`](./pysilverline) — the underlying async Tuya v3.3 client,
  reusable outside Home Assistant.
- [`tinytuya`](https://github.com/jasonacox/tinytuya) — generic Tuya local
  protocol library that informed parts of the protocol implementation.
- [`tuya-local`](https://github.com/make-all/tuya-local) — community Tuya
  integration with extensive device YAMLs; the source for several of the
  DP mappings used here.

## License

MIT — see [LICENSE](./LICENSE).

[issue-117566]: https://github.com/home-assistant/core/issues/117566
