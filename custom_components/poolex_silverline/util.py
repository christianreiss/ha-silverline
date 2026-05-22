"""Pure helpers shared across platforms.

These functions are deliberately UI- and HA-state-free so platforms can
share a single source of truth for derived semantics (currently: what
the heat pump's compressor is actually doing right now).
"""

from __future__ import annotations

from homeassistant.components.climate.const import HVACAction, HVACMode
from pysilverline import DeviceState

from .const import COOL_PREFIX_TO_PRESET, HEAT_PREFIX_TO_PRESET


def compute_hvac_action(
    state: DeviceState, last_direction: HVACMode | None
) -> HVACAction | None:
    """Derive HVACAction from a DeviceState snapshot.

    `last_direction` lets a caller (currently the climate entity) tell us
    which direction the user last asked for, so that even while the unit
    is OFF we *could* in principle synthesize a meaningful action. We
    don't — OFF always maps to HVACAction.OFF — but the parameter is kept
    so the signature is platform-friendly.

    The compressor-running heuristic:
    - DP 108 (actual_frequency), when exposed, is authoritative: 0 = parked,
      non-zero = the compressor is pulling power.
    - On minimal firmware (no DP 108) we fall back to the temperature delta
      sign vs the setpoint. This matches what the OEM controller's LED
      shows users.
    """

    if state.power is None:
        return None
    if not state.power:
        return HVACAction.OFF

    mode_string = state.mode or ""
    if mode_string == "Auto":
        mode: HVACMode | None = HVACMode.HEAT_COOL
    elif mode_string in HEAT_PREFIX_TO_PRESET:
        mode = HVACMode.HEAT
    elif mode_string in COOL_PREFIX_TO_PRESET:
        mode = HVACMode.COOL
    else:
        mode = None

    freq = state.actual_frequency
    active = freq > 0 if isinstance(freq, int) else None
    current = state.temp_current
    target = state.temp_set

    def _heat_or_idle() -> HVACAction:
        if active is True:
            return HVACAction.HEATING
        if active is False:
            return HVACAction.IDLE
        if current is not None and target is not None:
            return HVACAction.HEATING if current < target else HVACAction.IDLE
        return HVACAction.IDLE

    def _cool_or_idle() -> HVACAction:
        if active is True:
            return HVACAction.COOLING
        if active is False:
            return HVACAction.IDLE
        if current is not None and target is not None:
            return HVACAction.COOLING if current > target else HVACAction.IDLE
        return HVACAction.IDLE

    if mode == HVACMode.HEAT:
        return _heat_or_idle()
    if mode == HVACMode.COOL:
        return _cool_or_idle()
    if mode == HVACMode.HEAT_COOL:
        if active is False:
            return HVACAction.IDLE
        if current is None or target is None:
            return HVACAction.IDLE
        if current < target:
            return HVACAction.HEATING
        if current > target:
            return HVACAction.COOLING
        return HVACAction.IDLE
    # `last_direction` reserved for future OFF-aware callers; intentionally
    # unused today so OFF stays OFF.
    del last_direction
    return HVACAction.IDLE
