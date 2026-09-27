# Known limitations

- **Diagnostic sensors are firmware-dependent.** DPs 101–111 are populated
  on the Brustec firmware variants (Steinbach Silent Mini: 101–104 and 106–108); some Poolex Silverline FI
  firmwares (verified live: PC-SLP090N) only expose DPs 1, 2, 3, 4, and 13.
  Unsupported diagnostic DPs are not registered as entities at all — they
  do not appear in your device page, so they cannot show up as `unavailable`
  and clutter dashboards.
- **On the standard WBR3-module devices, measurement DPs (temps, frequency,
  pressures — DP 101+) go stale if the device loses its Tuya cloud session —
  a module limitation, not an integration bug, and not confirmed universal
  across every supported model.** Confirmed on a Poolex Silverline FI 120
  (protocol 3.5, issue #15): the WiFi module only serves a live MCU sensor
  reading over the local socket while it holds an active session with
  Tuya's cloud MQTT broker on **TCP 8886**. Block that (even with DNS/NTP
  still reachable) and the local socket keeps answering, but with a frozen
  snapshot — no local command forces a fresh read, including `updatedps`,
  alternate query opcodes, a control write, or even a full power-cycle.
  Control DPs (power/mode/setpoint) are unaffected and always read/write
  live regardless of cloud state. Not confirmed on the newer v3.5 JetLine FI
  WiFi control board (issue #7) — that may behave differently. If you want a
  WBR3-module device fully air-gapped, only control is guaranteed to stay
  current — sensor readings will freeze at their last cloud-synced values.
- **Confirmed: a v3.4 Nano 5kW-family unit drops measurement DPs entirely
  (not just freezes them) if it restarts without Tuya cloud access** — a
  different, harder failure mode than the WBR3 freeze-in-place behaviour
  above. A reporter running the "Spawler o'spa Flow 5kW" rebrand isolated
  this with a same-power-state A/B (issue #18): re-enabling a firewall rule
  on a device that was already running, with an already-populated DP cache,
  changed nothing — DPs 1, 2, 3 (current temp), 4 (mode) and 21 (fault
  bitmap) kept reporting. Power-cycling the unit while still firewalled did:
  the next `DP_QUERY` returned only DP 1 (bare power state), everything
  else gone. So the trigger is a restart without cloud reachability, not
  merely being firewalled while already running. Control is partly
  recoverable without the cloud — writing to the device locally (e.g.
  toggling power or changing the setpoint from Home Assistant) brought DP 2
  (setpoint) and DP 4 (mode) back into the reported set, because the local
  socket echoes back whatever was just written — but DP 3 (current
  temperature) and DP 21 (fault bitmap) stayed missing regardless, since
  nothing writes those locally; the module apparently only populates
  read-only measurement DPs via an actual cloud session, same underlying
  cause as the WBR3 behaviour, just manifesting as absence instead of a
  frozen value. If you keep this hardware firewalled, avoid power-cycling
  it (including brief power outages); if it does restart while blocked,
  Home Assistant can restore control but not current temperature or fault
  reporting until connectivity comes back. Confirmed stable, not decaying:
  a follow-up capture taken 24+ hours into the same firewall rule showed
  no further change beyond what the restart already caused. One loose end:
  an early capture from the same unit also showed a DP 101 that has never
  reappeared since, including after using the device's own physical
  buttons — unrelated to the mechanism above and still unexplained.
- **°F mode is not supported.** Lock the wired remote to °C — on °F some
  firmwares move the fault bitmap from DP 13 to DP 21 and reuse DP 13 for
  the unit-conversion enum, which the integration does not yet handle.
- **Auto mode has no Boost or Silent preset.** Most firmwares have no Auto
  variant, and the integration does not offer one on any model. That
  includes the PC-INV-120V2, whose firmware vocabulary lists
  `a_powerful`/`a_silent`: those are read as `heat_cool` but never written. Selecting `boost` or `eco` while in `heat_cool` raises a
  service-validation error with a translated message.
- **Reported water temperature drifts toward ambient after the filter pump
  has been off for a while, then snaps back within seconds once circulation
  resumes** (observed on a Poolex Nano 5kW WiFi, issue #16, and expected to
  apply to any model — it's a sensor-placement characteristic, not
  firmware-specific). The temp sensor reads the water sitting in the heat
  exchanger, not the bulk pool, so without flow it settles toward the
  surrounding air temperature instead of the pool's. If `current_temperature`
  looks implausible, check whether circulation had been stopped.
