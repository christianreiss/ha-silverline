# Supported devices — model notes

The compatibility table lives in the [README](../README.md#supported-devices).
This page collects the per-model details behind it: which profile to pick,
which sensors each firmware exposes, and the quirks found on real hardware.

## Choosing a model profile

For the FI 120 **v3.5 Full Inverter** firmware, choose **Poolex Silverline FI 120
(Tuya v3.5 / Full Inverter)** during setup or **Reconfigure** an existing entry
and change its model. This profile reuses the FI 150 telemetry mapping; the
existing FI 150 selection remains a working workaround on 0.11.14–0.11.16.
The **FI 120 V2 / PC-INV-120V2** profile is a separate firmware with different
DP numbering, mode strings and temperature scaling. Selection is explicit:
protocol version or an incomplete DP snapshot alone does not identify a layout.

## Sensors by firmware

- **Diagnostic sensors.** Up to 30 firmware-dependent diagnostic sensors (15
  of them disabled by default). The integration only registers entities for
  the DPs your firmware actually exposes — the minimal Poolex PC-SLP090N
  firmware ships five DPs and gets none of the 101–111 diagnostics, while the
  Brustec variant ships the full set (the Steinbach Silent Mini was observed
  reporting 101–104 and 106–108).
- **Fault binary sensors.** One per fault bit the firmware's fault table
  decodes. Classic DP 13 firmware gets all ten fault bits (nine on the FI 70,
  which leaves bit 6 undecoded) — the five most common (water flow,
  antifreeze, high/low pressure, communication) enabled by default, the other
  five (inverter comms and the four temperature-sensor faults) disabled by
  default. Full Inverter firmware (Nano Fi, FI 150, FI 120 v3.5) decodes water
  flow (bit 8) and ambient-range protection (bit 19), both enabled by default,
  and adds a defrosting sensor (DP 115). The Nano 5kW decodes only water flow
  (DP 21 bit 8).
- **Power and energy.** On firmware that reports line voltage and current
  (DP 120/121), a derived electrical-power sensor and a lifetime energy
  counter in kWh.

## Model and firmware notes

- **The protocol version is auto-detected** (probed in order v3.5 → v3.4 →
  v3.3) during setup, and the detected version is then pinned on the config
  entry. v3.5 — including local control
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
  handled: the socket behaviour automatically on any v3.4 connection, and the
  DP renumbering when the **`Poolex Silverline (Tuya v3.4 / wfzeiyn1ed3axxde)`**
  model is selected.
- **Diagnostic DPs (101–111) are firmware-dependent, not model-dependent:**
  the same SKU can ship full or bare depending on its firmware. The
  integration only registers the DPs the device has reported by setup (plus
  any DPs the selected model pins), so missing
  diagnostics never show up as `unavailable` clutter.
- **The same product key spans full and minimal firmware.** Product key
  `3bhylhz5zhogklel` is shared across the PC-SLP090N and the JetLine Selection
  FI family, and some JetLine Selection FI 95 units report only the minimal
  5-DP set (`1, 2, 3, 4, 13`) — no DP 108, so no diagnostics (issue #6). If your
  unit only exposes those five DPs, the behaviour matches the PC-SLP090N
  profile.
- **The FI 70's fault bit 6 is deliberately left unlabelled.** A reporter read
  DP 13 = 64 (bit 6 alone) off a real PC-SLP070N while the wired controller
  displayed **Er10** (issue #21) — the classic family's bit table calls that
  bit the inlet-water sensor and prints P3 for it. Rather than raise an
  "Inlet sensor fault (P3)" Repair card the hardware disagrees with, the
  **Poolex Silverline FI 70** profile decodes every other bit as usual and
  surfaces bit 6 as the raw `bit6` with no fault sensor and no Repair card.
  If you own an FI 70 and your manual says what Er10 means, please add it to
  issue #21 and it will be named.
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
  (issue #5). Select the **`Poolex Silverline FI 120 V2 / PC-INV-120V2 (tenths °C)`** model
  during setup (or via *Reconfigure*) to apply the ÷10 scaling; the setpoint
  (DP 2) stays whole °C and is unaffected. This firmware also uses a different
  DP-4 mode vocabulary (`heat`/`h_powerful`/`h_silent`, `cool`/`c_powerful`/
  `c_silent`, `auto`/`a_powerful`/`a_silent`); the integration reads and writes
  it automatically when this model is selected.
- **°C only.** °F shifts the fault bitmap and is not supported (see
  [Known limitations](known-limitations.md)).
- **Poolex Nano 5kW WiFi has a genuinely minimal DP schema — there is no
  richer telemetry hidden behind the Tuya cloud on this hardware tier.**
  Reported with only `{1, 2, 3, 4, 21}` available (issue #16, productKey
  `yk3bytlujz2xshuy`; also seen rebranded as the "Poolex Spawler o'spa Flow
  5kW" on protocol v3.4, issue #18). Cross-referenced against the public
  tuya-local project, which catalogues this exact productKey under a
  different OEM brand name ("Varpoolfaye Pool Mini") with the same 5-DP
  schema — confirming there is no inlet/outlet/suction/discharge temp,
  frequency, fan, pump, or voltage/current telemetry to expose on this
  hardware tier at all, unlike the richer Nano Fi 3kW. Some units also
  expose a DP 101 that is a boolean, not a temperature — the integration
  no longer maps it as one (issue #18), so it won't show a permanently
  "unavailable" suction-temperature sensor. DP 21 is a status/fault
  bitfield rather than a runtime counter on this firmware; bit 8 (raw
  value 256) is hardware-confirmed as the E6 / insufficient-water-flow
  fault (a reporter captured DP21 = 0 normally and 256 with the physical
  controller showing E6, self-clearing once flow was restored) and
  surfaces as the `fault_water_flow` binary sensor and the `fault_code`
  sensor. Other bits on this DP are not yet identified and surface as
  generic `bitN` placeholders rather than a guessed label. Boost/eco
  preset support is unconfirmed (the cross-referenced schema only models
  Heat/Cool/Auto, and doesn't cover presets either way), so selecting them
  degrades to the plain heat/cool string rather than risking an
  unsupported write. Which of those DPs the device actually *reports* has
  been observed to vary on the v3.4 rebrand — see the cloud-session note
  under [Known limitations](known-limitations.md).
- Presets `boost` / `eco` do not apply in `heat_cool` (Auto) — see
  [Known limitations](known-limitations.md).
