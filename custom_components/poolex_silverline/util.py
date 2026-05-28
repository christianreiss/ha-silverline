"""Pure helpers shared across platforms.

These functions are deliberately UI- and HA-state-free so platforms can
share a single source of truth for derived semantics (currently: what
the heat pump's compressor is actually doing right now, plus the
mode/preset derivations that climate and select would otherwise
duplicate).
"""

from __future__ import annotations

from homeassistant.components.climate.const import HVACAction, HVACMode
from pysilverline import DeviceState

from .const import (
    AUTO_TEMP_MAX,
    AUTO_TEMP_MIN,
    COOL_PREFIX_TO_PRESET,
    COOL_TEMP_MAX,
    COOL_TEMP_MIN,
    HEAT_PREFIX_TO_PRESET,
    HEAT_TEMP_MAX,
    HEAT_TEMP_MIN,
    PRESET_NONE,
)


def mask_device_id(device_id: str) -> str:
    """Return a shortened device_id safe for INFO/WARNING logs.

    Tuya device IDs are 22-char identifiers that uniquely name the unit;
    leaking them at INFO into a shared journal is mildly sensitive. We
    keep the first six characters (enough to correlate log lines for the
    same device) and drop the rest, marking the truncation with `...`.
    Full IDs stay available at DEBUG level for owners actively
    troubleshooting their own device.
    """
    if len(device_id) <= 6:
        return device_id
    return f"{device_id[:6]}..."


def derive_hvac_mode(state: DeviceState) -> HVACMode | None:
    """Map DP 1 (power) + DP 4 (mode enum) onto HVACMode.

    Returns ``HVACMode.OFF`` when the device is powered down, the matching
    direction for known mode strings, and ``None`` when the firmware reports
    a string we don't decode (caller surfaces the entity as ``unknown``).
    """
    if state.power is None:
        return None
    if not state.power:
        return HVACMode.OFF
    mode = state.mode or ""
    if mode == "Auto":
        return HVACMode.HEAT_COOL
    if mode in HEAT_PREFIX_TO_PRESET:
        return HVACMode.HEAT
    if mode in COOL_PREFIX_TO_PRESET:
        return HVACMode.COOL
    return None


def derive_preset(state: DeviceState) -> str:
    """Map DP 4 onto the inverter preset (``none`` / ``boost`` / ``eco``).

    Collapses to ``none`` whenever the device is off, the mode string is
    missing, or the mode is ``Auto`` (where presets are device-meaningless).
    """
    if state.power is None or not state.power or not state.mode:
        return PRESET_NONE
    if state.mode in HEAT_PREFIX_TO_PRESET:
        return HEAT_PREFIX_TO_PRESET[state.mode]
    if state.mode in COOL_PREFIX_TO_PRESET:
        return COOL_PREFIX_TO_PRESET[state.mode]
    return PRESET_NONE


def mode_temp_range(mode: HVACMode | None) -> tuple[int, int]:
    """Per-mode setpoint clamp (Heat 15-40, Cool 8-28, Auto 8-40).

    Caller resolves their own OFF policy (climate uses its persisted
    _last_direction; number falls back to Heat) and passes in the
    already-resolved mode. Unknown modes also fall back to the Heat
    range — the most common operating mode for a pool heat pump.
    """
    if mode == HVACMode.COOL:
        return COOL_TEMP_MIN, COOL_TEMP_MAX
    if mode == HVACMode.HEAT_COOL:
        return AUTO_TEMP_MIN, AUTO_TEMP_MAX
    return HEAT_TEMP_MIN, HEAT_TEMP_MAX


def compute_hvac_action(state: DeviceState) -> HVACAction | None:
    """Derive HVACAction from a DeviceState snapshot.

    The compressor-running heuristic:
    - DP 108 (actual_frequency), when exposed, is authoritative: 0 = parked,
      non-zero = the compressor is pulling power.
    - On minimal firmware (no DP 108) we fall back to the temperature delta
      sign vs the setpoint. This matches what the OEM controller's LED
      shows users.

    Returns None only when power state is unknown; an unknown DP 4 string
    (mode resolved to None) collapses to IDLE, mirroring the OEM
    controller's "neither heating nor cooling right now" indicator.
    """

    if state.power is None:
        return None
    if not state.power:
        return HVACAction.OFF
    mode = derive_hvac_mode(state)

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
    return HVACAction.IDLE
