"""Poolex Nano Fi 3kW (ref. PC-NANO-B3N), Tuya productId ``am4nomaadnhwvekq``.

Distributed by Poolstar SAS. Reported and cross-checked against the official
Tuya cloud product schema (pulled live via the Tuya IoT platform through the
``xtend_tuya`` integration's diagnostics, then compared DP-by-DP against what
this repo's "other" fallback profile was actually reading locally).

Confirmed live on real hardware (protocol v3.5, local port 6668):

    supported_dps: {1, 2, 3, 4, 13, 103, 104, 105, 106, 108, 110, 111, 117, 120, 121}

DP 114 (fan_speed) is declared in the generic Tuya product schema for this
pid but has never been observed on the wire — left unmapped (``None``)
rather than guessed.

DP 109, 124, 132 and 142 were confirmed live on a Poolex Nano Fi 5kW
(PC-NANO-B5N) — the same pid, a larger sibling in this line — via a
diagnostics dump in issue #19 (reporter @patrickpetos, 2026-08-18):

    "109": 0, "124": 45, "125": 8, "126": 18, "127": 3, "128": 1,
    "130": 2, "131": 1, "132": -5, "142": 40, "145": 7

109/124/132/142 map onto fields this layout already declares (target_frequency,
condensing_temp, superheat, target_condensing) with plausible physical values,
so they are wired in below. 125/126/127/128/130/131/145 have no known meaning
yet and stay unmapped — no ``DpLayout`` field fits them, and guessing wire
semantics from a single reading risks mislabeling a working sensor (the exact
"other"-fallback failure mode this profile exists to avoid — see issue #19's
root cause: an entry left on "other" reads DP 108, indoor coil temp, as
actual_frequency, so the compressor/pool-heat state never leaves "active").

Cross-referenced field meanings (official Tuya schema for this pid):

    DP 101  aux_manual         (bool, electric-aux-heater manual switch — NOT a temp probe)
    DP 102  pump_manual        (bool, water-pump manual switch — NOT ambient temp)
    DP 103  inlet_temp         real water inlet temperature
    DP 104  outlet_temp        real water outlet temperature
    DP 105  outdoor_coil_temp  outdoor coil (evaporator) temperature
    DP 106  outdoor_ambient_temp  outdoor ambient air temperature
    DP 108  indoor_coil_temp   indoor coil temperature
    DP 109  target_frequency   requested compressor frequency (0 = idle;
                                confirmed on the Fi 5kW, issue #19)
    DP 110  actual_frequency   real compressor frequency
    DP 111  main_valve         main valve opening (%) — reused as the closest
                                available "pump/valve activity" proxy, same
                                role DP 111 plays as ``water_pump`` on the
                                standard layout
    DP 112  aux_valve          (not observed on the wire on this unit)
    DP 116  exhaust_temp       always reports -30 on this unit (no working
                                sensor wired to this DP) — left unmapped
    DP 117  return_temp        compressor return/suction gas temperature
    DP 120  ac_voltage         AC line voltage — **not** a runtime-hours
                                counter. The generic "other" fallback profile
                                (which assumes the standard family's DP 120 =
                                total_hours) misreads this as total operating
                                hours; this device has no exposed lifetime
                                runtime counter at all.
    DP 121  ac_current         AC line current (no DpLayout field exists for
                                this yet, so it stays unsurfaced for now)
    DP 124  condensing_temp    condenser temperature (confirmed on the Fi
                                5kW, issue #19)
    DP 132  superheat          compressor suction superheat, can be negative
                                (confirmed on the Fi 5kW, issue #19)
    DP 142  target_condensing  target condensing temperature (confirmed on
                                the Fi 5kW, issue #19)

Everything not listed above (evaporating temp, compressor load, EEV steps,
defrosting, and DP 125/126/127/128/130/131/145) is either not exposed by
this firmware or of unconfirmed meaning.
"""

from __future__ import annotations

from .base import DpLayout

#: Poolex Nano Fi 3kW / PC-NANO-B3N, Tuya pid am4nomaadnhwvekq.
LAYOUT_NANO_FI_3KW = DpLayout(
    outlet_temp=104,
    ambient_temp=106,
    pool_temp=3,  # No distinct probe on this firmware — DP 3 is the same
    # value already used for temp_current, aliased here so this dedicated
    # sensor entity stays populated instead of going unavailable.
    discharge_temp=None,
    inlet_temp=103,
    suction_temp=117,
    outdoor_coil_temp=105,
    indoor_coil_temp=108,
    target_frequency=109,
    actual_frequency=110,
    eev_steps=None,
    fan_speed=None,
    aux_valve_opening=None,
    water_pump=111,
    condensing_temp=124,
    evaporating_temp=None,
    superheat=132,
    compressor_load=None,
    total_hours=None,
    target_superheat=None,
    target_condensing=142,
    ac_voltage=120,
    ac_current=121,
    # UNCONFIRMED, same open question as LAYOUT_V34_WFZEIYN: the Tuya schema
    # declares DP 121 with scale=1 (tenths of an amp), but no one has checked
    # this firmware against a clamp meter. Kept at 1 so both AC layouts behave
    # alike; set to 10 here if a reporter confirms tenths on this unit.
    ac_current_divisor=1,
)
