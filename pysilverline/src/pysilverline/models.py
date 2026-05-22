"""Typed data models for device state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import const


@dataclass(slots=True, kw_only=True, frozen=True)
class DeviceInfo:
    """Static device identity. Tuya v3.3 does not return a model string;
    fields are populated from the config entry plus what we infer."""

    device_id: str
    firmware: str | None = None


@dataclass(slots=True, kw_only=True, frozen=True)
class DeviceState:
    """Snapshot of all known DPs at a point in time. Missing DPs are None."""

    power: bool | None = None
    temp_set: int | None = None
    temp_current: int | None = None
    mode: str | None = None
    fault: int | None = None
    exhaust_temp: int | None = None
    return_temp: int | None = None
    coil_temp: int | None = None
    ambient_temp: int | None = None
    inlet_temp: int | None = None
    outlet_temp: int | None = None
    target_frequency: int | None = None
    actual_frequency: int | None = None
    eev_steps: int | None = None
    fan_speed: int | None = None
    water_pump: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dps(cls, dps: dict[str, Any]) -> DeviceState:
        """Build a DeviceState from a Tuya `dps` mapping (string keys).

        Coerces each DP through a type filter rather than trusting the
        wire payload — a malformed frame or a firmware that ships a
        string where we expect an int would otherwise propagate into
        entity arithmetic (e.g. `d.temp_set - d.temp_current`) and
        break consumers in surprising ways. The defensive choice for a
        DP whose value does not match its declared type is to expose
        it as None and keep the raw dict intact for diagnostics.
        """

        def _bool(dp: int) -> bool | None:
            value = dps.get(str(dp))
            return value if isinstance(value, bool) else None

        def _int(dp: int) -> int | None:
            value = dps.get(str(dp))
            # bool is a subclass of int in Python; reject it explicitly
            # so a power-style DP doesn't accidentally satisfy an int DP.
            if isinstance(value, bool):
                return None
            return value if isinstance(value, int) else None

        def _str(dp: int) -> str | None:
            value = dps.get(str(dp))
            return value if isinstance(value, str) else None

        return cls(
            power=_bool(const.DP_POWER),
            temp_set=_int(const.DP_TEMP_SET),
            temp_current=_int(const.DP_TEMP_CURRENT),
            mode=_str(const.DP_MODE),
            fault=_int(const.DP_FAULT),
            exhaust_temp=_int(const.DP_EXHAUST_TEMP),
            return_temp=_int(const.DP_RETURN_TEMP),
            coil_temp=_int(const.DP_COIL_TEMP),
            ambient_temp=_int(const.DP_AMBIENT_TEMP),
            inlet_temp=_int(const.DP_INLET_TEMP),
            outlet_temp=_int(const.DP_OUTLET_TEMP),
            target_frequency=_int(const.DP_TARGET_FREQUENCY),
            actual_frequency=_int(const.DP_ACTUAL_FREQUENCY),
            eev_steps=_int(const.DP_EEV_STEPS),
            fan_speed=_int(const.DP_FAN_SPEED),
            water_pump=_bool(const.DP_WATER_PUMP),
            raw=dict(dps),
        )

    def merge(self, dps: dict[str, Any]) -> DeviceState:
        """Return a new state with `dps` overlaid onto the current `raw` dict."""

        merged = {**self.raw, **dps}
        return DeviceState.from_dps(merged)
