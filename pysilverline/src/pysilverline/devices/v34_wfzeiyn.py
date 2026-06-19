"""Poolex pool heat pump, productKey ``wfzeiyn1ed3axxde``, protocol v3.4."""

from __future__ import annotations

from .base import DpLayout

#: Poolex pool heat pump, productKey ``wfzeiyn1ed3axxde``, protocol v3.4.
#: Tuya IoT field names (CZ):
#:   101 outlet water temp, 102 ambient, 105 outdoor coil, 106 return gas,
#:   108 indoor coil, 109 main valve, 110 aux valve, 114 fan speed (rpm).
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
    total_hours=120,
    target_superheat=137,
    target_condensing=142,
)
