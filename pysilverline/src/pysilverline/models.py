"""Typed data models for device state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import const
from .layouts import LAYOUT_STANDARD, DpLayout


@dataclass(slots=True, kw_only=True, frozen=True)
class DeviceState:
    """Snapshot of all known DPs at a point in time. Missing DPs are None."""

    power: bool | None = None
    temp_set: int | None = None
    # float because some OEM firmware reports DP 3 in tenths of a degree (scaled
    # by the layout's temp_current_divisor → e.g. 27.7); whole-°C firmware keeps
    # an int here (divisor 1, no division applied). See DpLayout.
    temp_current: float | None = None
    mode: str | None = None
    fault: int | None = None
    suction_temp: int | None = None
    ambient_temp: int | None = None
    pool_temp: int | None = None
    discharge_temp: int | None = None
    inlet_temp: int | None = None
    outlet_temp: int | None = None
    outdoor_coil_temp: int | None = None
    indoor_coil_temp: int | None = None
    target_frequency: int | None = None
    actual_frequency: int | None = None
    eev_steps: int | None = None
    fan_speed: int | None = None
    aux_valve_opening: int | None = None
    water_pump: bool | None = None
    water_pump_rpm: int | None = None
    condensing_temp: int | None = None
    evaporating_temp: int | None = None
    superheat: int | None = None
    compressor_load: int | None = None
    total_hours: int | None = None
    target_superheat: int | None = None
    target_condensing: int | None = None
    ac_voltage: int | None = None
    ac_current: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dps(
        cls, dps: dict[str, Any], *, layout: DpLayout = LAYOUT_STANDARD
    ) -> DeviceState:
        """Build a DeviceState from a Tuya `dps` mapping (string keys).

        ``layout`` maps the diagnostic/sensor fields onto wire DP numbers; it
        defaults to the legacy layout so existing callers are unaffected. A
        firmware that does not expose a field sets it to ``None`` in the layout,
        which yields ``None`` here regardless of what the wire carries.

        Coerces each DP through a type filter rather than trusting the wire
        payload — a malformed frame or a firmware that ships a string where we
        expect an int would otherwise propagate into entity arithmetic and break
        consumers in surprising ways. The defensive choice for a DP whose value
        does not match its declared type is to expose it as None and keep the
        raw dict intact for diagnostics.
        """

        def _bool(dp: int | None) -> bool | None:
            if dp is None:
                return None
            value = dps.get(str(dp))
            return value if isinstance(value, bool) else None

        def _pump(dp: int | None) -> bool | None:
            # Some firmware variants (e.g. FI 150) send DP 111 as an integer
            # (e.g. 320 = pump running) instead of a bool. Accept both: treat
            # non-zero int as True, zero as False.
            if dp is None:
                return None
            value = dps.get(str(dp))
            if isinstance(value, bool):
                return value
            if isinstance(value, int):
                return value != 0
            return None

        def _pump_rpm(dp: int | None) -> int | None:
            # Integer view of the same DP for firmwares that report pump speed
            # (rpm) rather than a simple on/off flag.
            if dp is None:
                return None
            value = dps.get(str(dp))
            if isinstance(value, bool):
                return None
            return value if isinstance(value, int) else None

        def _int(dp: int | None) -> int | None:
            if dp is None:
                return None
            value = dps.get(str(dp))
            # bool is a subclass of int in Python; reject it explicitly
            # so a power-style DP doesn't accidentally satisfy an int DP.
            if isinstance(value, bool):
                return None
            return value if isinstance(value, int) else None

        def _str(dp: int | None) -> str | None:
            if dp is None:
                return None
            value = dps.get(str(dp))
            return value if isinstance(value, str) else None

        def _temp_current() -> float | None:
            # DP 3 carries the current water temperature. Most firmware reports
            # whole °C, but some OEM siblings (e.g. PC-INV-120V2, issue #5) send
            # tenths of a degree; the per-firmware divisor scales those down.
            # Divisor 1 leaves the value an int so unaffected devices and their
            # diagnostic dumps are byte-for-byte unchanged.
            raw = _int(const.DP_TEMP_CURRENT)
            if raw is None or layout.temp_current_divisor == 1:
                return raw
            return raw / layout.temp_current_divisor

        return cls(
            power=_bool(const.DP_POWER),
            temp_set=_int(const.DP_TEMP_SET),
            temp_current=_temp_current(),
            mode=_str(const.DP_MODE),
            fault=_int(const.DP_FAULT),
            suction_temp=_int(layout.suction_temp),
            ambient_temp=_int(layout.ambient_temp),
            pool_temp=_int(layout.pool_temp),
            discharge_temp=_int(layout.discharge_temp),
            inlet_temp=_int(layout.inlet_temp),
            outlet_temp=_int(layout.outlet_temp),
            outdoor_coil_temp=_int(layout.outdoor_coil_temp),
            indoor_coil_temp=_int(layout.indoor_coil_temp),
            target_frequency=_int(layout.target_frequency),
            actual_frequency=_int(layout.actual_frequency),
            eev_steps=_int(layout.eev_steps),
            fan_speed=_int(layout.fan_speed),
            aux_valve_opening=_int(layout.aux_valve_opening),
            water_pump=_pump(layout.water_pump),
            water_pump_rpm=_pump_rpm(layout.water_pump),
            condensing_temp=_int(layout.condensing_temp),
            evaporating_temp=_int(layout.evaporating_temp),
            superheat=_int(layout.superheat),
            compressor_load=_int(layout.compressor_load),
            total_hours=_int(layout.total_hours),
            target_superheat=_int(layout.target_superheat),
            target_condensing=_int(layout.target_condensing),
            ac_voltage=_int(layout.ac_voltage),
            ac_current=_int(layout.ac_current),
            raw=dict(dps),
        )

    def merge(
        self, dps: dict[str, Any], *, layout: DpLayout = LAYOUT_STANDARD
    ) -> DeviceState:
        """Return a new state with `dps` overlaid onto the current `raw` dict."""

        merged = {**self.raw, **dps}
        return DeviceState.from_dps(merged, layout=layout)
