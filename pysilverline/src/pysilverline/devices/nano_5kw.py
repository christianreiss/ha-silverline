"""Poolex Nano 5kW WiFi, Tuya productId ``yk3bytlujz2xshuy`` (issue #16).

Also seen rebranded on the same OEM hardware as the "Poolex Spawler o'spa
Flow 5kW" (issue #18, protocol 3.4) and, per the Tuya developer portal, as
"固德热-PS-mini" / "Pool Mini". Cross-checked against the public tuya-local
product schema for this pid (catalogued there as "Varpoolfaye Pool Mini").

Confirmed live on real hardware:

    supported_dps: {1, 2, 3, 4, 21}          (issue #16, protocol 3.5)
    supported_dps: {1, 2, 3, 4, 21, 101}     (issue #18, protocol 3.4)

This firmware genuinely exposes nothing beyond power/setpoint/current-temp/
mode/fault: no inlet/outlet/suction/discharge temp, frequency, fan, pump,
or voltage/current DPs exist on this hardware tier at all, confirmed by the
cross-referenced schema — this isn't cloud-gating (contrast issue #15),
the DPs are simply absent from the product definition.

DP 21 carries this family's status/fault bitmap instead of the standard
DP 13 — see ``NANO_5KW_FAULT_BIT_NAMES`` in ``const.py`` for the (partial,
hardware-confirmed) bit decode.

DP 101, when present (issue #18), is a boolean — NOT suction/exhaust
temperature as it is on the standard layout's DP 101. Its actual meaning is
unconfirmed (possibly an aux-heater or manual-mode switch, as DP 101 is on
the unrelated Nano Fi 3kW schema), so it is left unmapped rather than
guessed; the integration must not register a temperature sensor against it.
"""

from __future__ import annotations

from .base import DpLayout

#: Poolex Nano 5kW WiFi / OEM siblings, Tuya pid yk3bytlujz2xshuy.
LAYOUT_NANO_5KW = DpLayout(
    fault=21,
    outlet_temp=None,
    ambient_temp=None,
    pool_temp=None,
    discharge_temp=None,
    inlet_temp=None,
    suction_temp=None,
    outdoor_coil_temp=None,
    indoor_coil_temp=None,
    target_frequency=None,
    actual_frequency=None,
    eev_steps=None,
    fan_speed=None,
    aux_valve_opening=None,
    water_pump=None,
    condensing_temp=None,
    evaporating_temp=None,
    superheat=None,
    compressor_load=None,
    total_hours=None,
    target_superheat=None,
    target_condensing=None,
    ac_voltage=None,
    ac_current=None,
)
