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

Independently corroborated a second time on *different* hardware: a Poolex
F-Spa 7kW paired via tuya-local on Homey (HA community thread 1011340,
post 23, @omerobbie, 2026-07-17) reports the same block at 124:45, 125:8,
127:1, 128:1, 130:2, 131:1, 132:-5, 142:40, 145:7 — near-identical factory
defaults on a product that is not a Nano Fi. A live refrigeration-circuit
reading would not land on the same numbers across two unrelated units;
identical config defaults would.

Both halves are now settled on real hardware (issue #19, @richardc1983,
2026-08-20). **Read: confirmed.** With the ten entities enabled, every
value matched what the unit's own code-locked installer menu displayed —
the decode above is no longer schema-only. **Write: rejected.** Writing
any of them is accepted without an error, without a fault code and
without a log entry, and the controller re-asserts its previous value a
few seconds later. Coherent with the menu itself: these parameters sit
behind an access code on the physical panel, so the firmware takes the
remote write and simply declines to keep it. No protocol change fixes
that, so an earlier revision's writable ``number`` entities were the
wrong shape — they offered a control that silently snaps back. They are
read-only DIAGNOSTIC sensors from 0.5.7 on (still disabled by default;
ten extra entities is a lot to hand everyone unasked).

The block is wired onto dedicated ``DpLayout`` fields (``heating_time``,
``defrost_time_limit``, ``defrost_cutout_temp``,
``heating_start_hysteresis``, ``heating_end_hysteresis``,
``cooling_start_hysteresis``, ``cooling_end_hysteresis``, ``defrost_temp``,
``max_temp_limit``, ``min_temp_limit``) — the fields stay, only the
platform that publishes them changed.

DP 115 was not in the issue #19 diagnostics dump, but it *is* on the wire:
a Nano Fi 5kW dump posted to the HA community thread 1011340 (post 25,
2026-08-12, integration 0.11.4) lists 115 in ``supported_dps`` with
``raw["115"] = 0``. So 115 arrives on ordinary status pushes, while the
124-145 config block does not — that split is what the earlier "typically
only sent on an explicit query" note was groping at. What is still
schema-only is the *decode*: tuya-local declares 115 an ``hvac_action``
enum where wire value 1 means "defrosting" (the defrost flag asked about at
the start of issue #19), and every capture so far reads 0, so the 1 ⇒
defrosting half is uncorroborated. Wired in below as ``defrosting``.

Cross-referenced field meanings (official Tuya schema for this pid):

    DP 101  aux_manual         (bool, electric-aux-heater manual switch — NOT a temp probe)
    DP 102  pump_manual        (bool, water-pump manual switch — NOT ambient
                                temp). Seen on the wire reading false on one
                                Fi 5kW; unmapped, so no entity yet.
    DP 103  inlet_temp         real water inlet temperature
    DP 104  outlet_temp        real water outlet temperature
    DP 105  outdoor_coil_temp  outdoor coil (evaporator) temperature
    DP 106  outdoor_ambient_temp  outdoor ambient air temperature
    DP 108  indoor_coil_temp   indoor coil temperature
    DP 109  target_frequency   requested compressor frequency (0 = idle;
                                confirmed on the Fi 5kW, issue #19)
    DP 110  actual_frequency   real compressor frequency
    DP 111  main_valve         RESOLVED — main (electronic) expansion valve
                                opening, in steps. Mapped to ``eev_steps``.
                                A Fi 5kW owner read the live value off the
                                unit's own parameter display and matched it
                                to parameter "1F Main EEV opening" (issue
                                #19, @patrickpetos, 2026-08-21) — a direct
                                hardware label, stronger than the
                                heating-cycle correlation this entry used to
                                ask for. tuya-local names the same DP "Main
                                valve", and the three samples in hand (143
                                and 394 on Fi 5kW units, 480 on an F-Spa
                                7kW) are ordinary EEV step counts while
                                being impossible circulation-pump speeds.
                                Until 0.5.6 this was routed through
                                ``water_pump``/``water_pump_rpm`` and
                                published as "Circulation pump speed" in
                                RPM — wrong on both the quantity and the
                                unit. This firmware exposes no pump DP at
                                all, so ``water_pump`` is now unmapped and
                                the "Water pump" binary sensor is gone here.
    DP 112  aux_valve          (not observed on the wire on this unit)
    DP 115  defrosting         hvac_action enum, 1 = defrosting —
                                hardware-confirmed against a real defrost
                                cycle with frost visible on the coil (issue
                                #19, 2026-08-21). Optional: absent from the
                                wire entirely on some units/states.
    DP 116  exhaust_temp       compressor discharge temperature. Declared in
                                the pid's schema (tuya-local "Exhaust
                                temperature", optional) but has never
                                appeared on the wire in any dump from either
                                Fi unit in issue #19 — left unmapped. An
                                early note here claimed it "always reports
                                -30"; that traces to the initial spec upload,
                                not to any observation on this hardware.
    DP 117  return_temp        compressor return/suction gas temperature
    DP 120  ac_voltage         AC line voltage — **not** a runtime-hours
                                counter. The generic "other" fallback profile
                                (which assumes the standard family's DP 120 =
                                total_hours) misreads this as total operating
                                hours; this device has no exposed lifetime
                                runtime counter at all.
    DP 121  ac_current         AC line current — mapped below via
                                ``ac_current``; see ``ac_current_divisor``
                                for the open whole-amps-vs-tenths question
    DP 124  heating_time       installer setpoint, minutes — read-only
    DP 125  defrost_time_limit installer setpoint, minutes — read-only
    DP 126  defrost_cutout_temp installer setpoint, °C — read-only
    DP 127  heating_start_hysteresis installer setpoint, °C delta — read-only
    DP 128  heating_end_hysteresis installer setpoint, °C delta — read-only
    DP 130  cooling_start_hysteresis installer setpoint, °C delta — read-only
    DP 131  cooling_end_hysteresis installer setpoint, °C delta — read-only
    DP 132  defrost_temp       installer setpoint, °C — read-only
    DP 142  max_temp_limit     installer setpoint, °C — read-only
    DP 145  min_temp_limit     installer setpoint, °C — read-only
                               (all ten hardware-confirmed on read and
                                hardware-confirmed to reject writes —
                                issue #19)

Everything not listed above (condensing/evaporating temp, superheat,
compressor load, fan speed, aux valve) is not exposed by this firmware as
telemetry.
"""

from __future__ import annotations

from ..const import NANO_FI_FAULT_TABLE
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
    eev_steps=111,  # main EEV opening, in steps — see DP 111 above
    fan_speed=None,
    aux_valve_opening=None,
    water_pump=None,  # no pump *telemetry* DP on this firmware (DP 102 is a
    # manual pump switch — a control, not a running/speed reading)
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
    # DP 13 like the classic family, but bit 8 is water flow, not the
    # defrost sensor — hardware-confirmed, see NANO_FI_FAULT_BIT_NAMES.
    fault_table=NANO_FI_FAULT_TABLE,
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
