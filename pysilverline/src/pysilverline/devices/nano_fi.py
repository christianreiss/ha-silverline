"""Poolex Nano Fi 3kW (ref. PC-NANO-B3N), Tuya productId ``am4nomaadnhwvekq``.

Distributed by Poolstar SAS. Reported and cross-checked against the official
Tuya cloud product schema (pulled live via the Tuya IoT platform through the
``xtend_tuya`` integration's diagnostics, then compared DP-by-DP against what
this repo's "other" fallback profile was actually reading locally).

Confirmed live on real hardware (protocol v3.5, local port 6668):

    supported_dps: {1, 2, 3, 4, 13, 103, 104, 105, 106, 108, 110, 111, 117, 120, 121}

DP 109 (target_frequency) and DP 114 (fan_speed) are declared in the generic
Tuya product schema for this pid but were never observed on the wire for this
specific unit — left unmapped (``None``) rather than guessed.

Cross-referenced field meanings (official Tuya schema for this pid):

    DP 101  aux_manual         (bool, electric-aux-heater manual switch — NOT a temp probe)
    DP 102  pump_manual        (bool, water-pump manual switch — NOT ambient temp)
    DP 103  inlet_temp         real water inlet temperature
    DP 104  outlet_temp        real water outlet temperature
    DP 105  outdoor_coil_temp  outdoor coil (evaporator) temperature
    DP 106  outdoor_ambient_temp  outdoor ambient air temperature
    DP 108  indoor_coil_temp   indoor coil temperature
    DP 109  target_frequency   (not observed on the wire on this unit)
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

Everything not listed above (condensing/evaporating temp, superheat,
compressor load, EEV steps, defrosting) is not exposed by this firmware.
"""

from __future__ import annotations

from .base import DpLayout

#: Poolex Nano Fi 3kW / PC-NANO-B3N, Tuya pid am4nomaadnhwvekq.
LAYOUT_NANO_FI_3KW = DpLayout(
    outlet_temp=104,
    ambient_temp=106,
    pool_temp=None,
    discharge_temp=None,
    inlet_temp=103,
    suction_temp=117,
    outdoor_coil_temp=105,
    indoor_coil_temp=108,
    target_frequency=None,
    actual_frequency=110,
    eev_steps=None,
    fan_speed=None,
    aux_valve_opening=None,
    water_pump=111,
    condensing_temp=None,
    evaporating_temp=None,
    superheat=None,
    compressor_load=None,
    total_hours=None,
    target_superheat=None,
    target_condensing=None,
)
