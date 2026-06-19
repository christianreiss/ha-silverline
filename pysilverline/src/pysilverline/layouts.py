"""Per-firmware DP layouts — maps semantic fields to Tuya DP numbers.

The ``wfzeiyn1ed3axxde`` / Tuya v3.4 pool firmware uses different DP numbering
than the legacy JetLine / PC-SLP090N layout (e.g. fan speed lives on DP 114, not
DP 110, and the suction/outlet sensors are swapped). Names verified against the
Tuya IoT Platform UI and cross-checked with live LAN dumps (2026-06).

The v3.4 layout and its DP numbering were contributed by Martin Čarek
(@olomouckyorel, PR #3) from a real Poolex Silverline v3.4 device — the only
v3.4 hardware these mappings have been validated against.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DpLayout:
    """Wire DP id for each semantic field; ``None`` = not exposed on this firmware."""

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


#: Legacy JetLine / Brustec / PC-SLP090N numbering (matches the ``DP_*``
#: constants in :mod:`pysilverline.const`).
LAYOUT_STANDARD = DpLayout()

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

LAYOUT_BY_NAME: dict[str, DpLayout] = {
    "standard": LAYOUT_STANDARD,
    "v34_wfzeiyn": LAYOUT_V34_WFZEIYN,
}


def layout_for_model(model_key: str) -> DpLayout:
    """Return the DP layout for a config-entry model key (default: standard)."""
    if model_key == "silverline_v34":
        return LAYOUT_V34_WFZEIYN
    return LAYOUT_STANDARD
