# Acknowledgments

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
- **[@TRIAG73](https://github.com/TRIAG73)** — reported the Poolex Silverline
  FI 70 (PC-SLP070N), confirmed its five-DP set and standard mode vocabulary
  against the 090N profile, and supplied the DP 13 = 64 / panel-shows-Er10
  reading that stopped the classic bit table's "inlet sensor (P3)" label from
  being applied to this firmware (issue #21).
- **[@trothe](https://github.com/trothe)** — reported the minimal 5-DP JetLine
  Selection FI variant and the compressor-sensor false positive, with an `Er 03`
  no-flow cross-check (issue #6).
- **[@nickfr](https://github.com/nickfr)** — reported and patiently helped
  diagnose the Silverline FI 150's diagnostic DP mapping and a broken
  water-pump sensor, cross-checking readings against live outdoor temperature
  across several rounds (issue #1).
- **[@squitel](https://github.com/squitel)** — settled the Silverline FI 150's
  whole DP block by running a controlled load transition on a live unit
  (setpoint down to stop the compressor, back up to restart it) and logging
  every poll through it, which is what separated the IPM heatsink temperature
  from a compressor frequency, the main EEV opening from a circulation pump,
  and mains voltage from a lifetime hour counter — then wrote the layout up
  DP-by-DP (issue #20).
- **[@froggy974](https://github.com/froggy974)** — reported and iteratively
  verified PC-INV-120V2 support end-to-end: the tenths-of-a-degree temperature
  scaling, the full DP-4 mode vocabulary, and a transient write regression,
  across five release rounds (issue #5).
- **[@darkm00nch](https://github.com/darkm00nch)** — reported the reconnect
  logic giving up after a fixed backoff window and confirmed the fix that
  made it retry indefinitely (issue #9), and confirmed the v3.5 write fix on
  a JetLine FI (issue #8).
- **[@martin0675](https://github.com/martin0675)** — diagnosed the Steinbach
  Silent Mini's full-word DP-4 mode vocabulary (`"Heating"`/`"Cooling"`) that
  left the climate entity stuck on "unknown" (issue #10).
- **[@chmielot](https://github.com/chmielot)** — hardware-confirmed that the
  WBR3 WiFi module only serves live measurement telemetry while it holds a
  Tuya cloud MQTT session, pinning the cloud-gated staleness down to
  port-level isolation (issue #15).
- **Andre Gross** — found on real Silverline Top 9 hardware that the v3.4
  firmware's DP 120/121 are line voltage and current, not the lifetime
  runtime counter the layout assumed, and built the derived power and energy
  sensors on top. Ported back here from the
  [ha-poolex-heatpump](https://github.com/cococheaf/ha-poolex-heatpump) fork.
