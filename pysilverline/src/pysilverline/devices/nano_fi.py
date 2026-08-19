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

DP 109 was confirmed live on a Poolex Nano Fi 5kW (PC-NANO-B5N) — the same
pid, a larger sibling in this line — via a diagnostics dump in issue #19
(reporter @patrickpetos, 2026-08-18) and wired in as ``target_frequency``.

That same dump also carried DP 124/125/126/127/128/130/131/132/142/145. An
earlier revision of this file wired 124/132/142 in as condensing_temp/
superheat/target_condensing, reasoning that they "numerically match the
legacy layout's defaults" (see ``base.DpLayout``) — but that legacy default
comes from an unrelated product family (the standard PC-SLP090N/JetLine
layout), not from this pid, and was never actually cross-checked against
this device's schema. It was wrong: a public tuya-local device config for
this exact productId (``poolex_icespa51_heatpump.yaml``, "Poolex IceSpa 51")
defines the whole 124-145 block as **configuration setpoints**, not live
refrigeration-circuit telemetry:

    DP 124  Heating time              config, minutes, range 30-120
    DP 125  Defrost time limit        config, minutes, range 1-25
    DP 126  Defrost cutout temp       config, °C, range -20..20
    DP 127  Heating start hysteresis  config, °C delta, range 0..18
    DP 128  Heating end hysteresis    config, °C delta, range 0..18
    DP 130  Cooling start hysteresis  config, °C delta, range 0..18
    DP 131  Cooling end hysteresis    config, °C delta, range 0..18
    DP 132  Defrost temperature       config, °C, range -20..20
    DP 142  Maximum temperature       config, °C, range 35-60
    DP 145  Minimum temperature       config, °C, range 2-10

Every value in the issue #19 dump (124:45, 125:8, 126:18, 127:3, 128:1,
130:2, 131:1, 132:-5, 142:40, 145:7) falls inside its declared range above —
strong corroboration that this is the correct reading, not the "condenser
temp of 45°C" / "superheat of -5°C" reading the earlier revision assumed
from plausible-looking values alone (the exact same trap as this profile's
own root cause: the "other" fallback reading DP 108, indoor coil temp, as
compressor frequency because the number looked plausible). 124/132/142 are
reverted to unmapped as condensing_temp/superheat/target_condensing here.

Follow-up (issue #19, richardc1983): the manual shows this same DP block
behind an installer menu (H0-H3 / P0-P3), and asked whether they could be
exposed as configurable settings rather than just documented as unmapped.
tuya-local's schema models all ten as plain writable ``number`` entities
(``category: config``), so they are wired below onto dedicated ``DpLayout``
fields (``heating_time``, ``defrost_time_limit``, ``defrost_cutout_temp``,
``heating_start_hysteresis``, ``heating_end_hysteresis``,
``cooling_start_hysteresis``, ``cooling_end_hysteresis``, ``defrost_temp``,
``max_temp_limit``, ``min_temp_limit``) and exposed as CONFIG-category,
disabled-by-default `number` entities in ``number.py``. This is read
confidence only (tuya-local schema + range-matching, the same evidence
level as the mislabel fix above) — nobody has confirmed a write actually
takes on real hardware yet.

DP 115 was not in the issue #19 diagnostics dump (config-category DPs like
115-145 are typically only sent on an explicit query, not on every status
push) but is declared by the same tuya-local schema as an ``hvac_action``
enum where wire value 1 means "defrosting" — this is the defrost flag asked
about at the start of issue #19. Wired in below as ``defrosting``; unconfirmed
against a live diagnostics dump since no reporter's has carried it yet.

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
    DP 115  defrosting         hvac_action enum, 1 = defrosting (tuya-local
                                schema; not yet seen on the wire — issue #19)
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
    DP 124  heating_time       config setpoint, minutes (tuya-local schema,
                                issue #19) — writable `number.heating_time`
    DP 125  defrost_time_limit config setpoint, minutes — writable `number`
    DP 126  defrost_cutout_temp config setpoint, °C — writable `number`
    DP 127  heating_start_hysteresis config setpoint, °C delta — writable
    DP 128  heating_end_hysteresis config setpoint, °C delta — writable
    DP 130  cooling_start_hysteresis config setpoint, °C delta — writable
    DP 131  cooling_end_hysteresis config setpoint, °C delta — writable
    DP 132  defrost_temp       config setpoint, °C (tuya-local schema,
                                issue #19) — writable `number.defrost_temperature`
    DP 142  max_temp_limit     config setpoint, °C (tuya-local schema,
                                issue #19) — writable `number.maximum_temperature_limit`
    DP 145  min_temp_limit     config setpoint, °C — writable `number`

Everything not listed above (condensing/evaporating temp, superheat,
compressor load, EEV steps) is not exposed by this firmware as telemetry.
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
    condensing_temp=None,
    evaporating_temp=None,
    superheat=None,
    compressor_load=None,
    total_hours=None,
    target_superheat=None,
    target_condensing=None,
    ac_voltage=120,
    ac_current=121,
    # UNCONFIRMED, same open question as LAYOUT_V34_WFZEIYN: the Tuya schema
    # declares DP 121 with scale=1 (tenths of an amp), but no one has checked
    # this firmware against a clamp meter. Kept at 1 so both AC layouts behave
    # alike; set to 10 here if a reporter confirms tenths on this unit.
    ac_current_divisor=1,
    defrosting=115,
    heating_time=124,
    defrost_time_limit=125,
    defrost_cutout_temp=126,
    heating_start_hysteresis=127,
    heating_end_hysteresis=128,
    cooling_start_hysteresis=130,
    cooling_end_hysteresis=131,
    defrost_temp=132,
    max_temp_limit=142,
    min_temp_limit=145,
)
