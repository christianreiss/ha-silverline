"""Poolex pool heat pump, productKey ``wfzeiyn1ed3axxde``, protocol v3.4."""

from __future__ import annotations

from .base import DpLayout

#: Poolex pool heat pump, productKey ``wfzeiyn1ed3axxde``, protocol v3.4.
#: Tuya IoT field names (CZ):
#:   101 outlet water temp, 102 ambient, 105 outdoor coil, 106 return gas,
#:   108 indoor coil, 109 main valve, 110 aux valve, 114 fan speed (rpm),
#:   120 AC voltage, 121 AC current.
#:
#: DP 120 is line voltage on this firmware, **not** the standard family's
#: lifetime runtime counter — same trap as ``LAYOUT_NANO_FI_3KW``. This unit
#: exposes no lifetime runtime counter at all, so ``total_hours`` stays None.
#: Corrected from live hardware by Andre Gross (@cococheaf fork).
LAYOUT_V34_WFZEIYN = DpLayout(
    outlet_temp=101,
    ambient_temp=102,
    pool_temp=103,
    discharge_temp=None,
    inlet_temp=None,
    suction_temp=106,
    outdoor_coil_temp=105,
    indoor_coil_temp=108,
    target_frequency=None,
    actual_frequency=None,
    eev_steps=109,
    fan_speed=114,
    aux_valve_opening=110,
    water_pump=111,
    condensing_temp=124,
    evaporating_temp=133,
    superheat=132,
    compressor_load=140,
    total_hours=None,
    target_superheat=137,
    target_condensing=142,
    ac_voltage=120,
    # Same open question as LAYOUT_NANO_FI_3KW: the Tuya schema declares DP 121
    # with scale=1 (tenths of an amp), but DpLayout/DeviceState have no
    # per-field divisor apart from temp_current_divisor, so this surfaces the
    # RAW wire integer. Confirm against a clamp meter before trusting it.
    ac_current=121,
)
