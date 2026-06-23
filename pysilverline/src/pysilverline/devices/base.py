"""Per-firmware DP layout dataclass.

A :class:`DpLayout` maps each semantic field to its wire DP id for one
firmware variant; ``None`` marks a field that firmware does not expose.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DpLayout:
    """Wire DP id for each semantic field; ``None`` = not exposed on this firmware.

    ``temp_current_divisor`` scales the core current-water-temperature DP (DP 3):
    most firmware reports whole °C (divisor 1), but some OEM siblings report
    tenths of a degree (divisor 10, e.g. raw 277 = 27.7 °C). It is the only
    scaled field — DP 2 (setpoint) stays whole °C on every variant seen so far.
    """

    temp_current_divisor: int = 1
    outlet_temp: int | None = 106
    ambient_temp: int | None = 102
    pool_temp: int | None = 103
    discharge_temp: int | None = 104
    inlet_temp: int | None = 105
    suction_temp: int | None = 101
    outdoor_coil_temp: int | None = None
    indoor_coil_temp: int | None = None
    target_frequency: int | None = 107
    actual_frequency: int | None = 108
    eev_steps: int | None = 109
    fan_speed: int | None = 110
    aux_valve_opening: int | None = None
    water_pump: int | None = 111
    condensing_temp: int | None = 124
    evaporating_temp: int | None = 133
    superheat: int | None = 132
    compressor_load: int | None = 140
    total_hours: int | None = 120
    target_superheat: int | None = 137
    target_condensing: int | None = 142
