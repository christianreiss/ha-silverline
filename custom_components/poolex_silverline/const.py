"""Constants for the Poolex Silverline integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "poolex_silverline"
MANUFACTURER: Final = "Poolex"
MODEL: Final = "Silverline Inverter (PC-SLP090N)"

CONF_DEVICE_ID: Final = "device_id"
CONF_LOCAL_KEY: Final = "local_key"

DEFAULT_PORT: Final = 6668
DEFAULT_SCAN_INTERVAL: Final = 30  # seconds; WBR3 reboots if polled <8s

PRESET_NONE: Final = "none"
PRESET_BOOST: Final = "boost"
PRESET_ECO: Final = "eco"

# DP-4 enum suffix mapping helpers used by the climate state machine.
HEAT_PREFIX_TO_PRESET: Final = {
    "Heat": PRESET_NONE,
    "BoostHeat": PRESET_BOOST,
    "SilentHeat": PRESET_ECO,
}
COOL_PREFIX_TO_PRESET: Final = {
    "Cool": PRESET_NONE,
    "BoostCool": PRESET_BOOST,
    "SilentCool": PRESET_ECO,
}
PRESET_TO_HEAT_DP: Final = {
    PRESET_NONE: "Heat",
    PRESET_BOOST: "BoostHeat",
    PRESET_ECO: "SilentHeat",
}
PRESET_TO_COOL_DP: Final = {
    PRESET_NONE: "Cool",
    PRESET_BOOST: "BoostCool",
    PRESET_ECO: "SilentCool",
}

# Mode-specific setpoint ranges, verified live against a PC-SLP090N.
# Writing outside the per-mode range is server-side clamped — we reject
# up-front so the UI's target_temperature can't silently move.
HEAT_TEMP_MIN: Final = 15
HEAT_TEMP_MAX: Final = 40
COOL_TEMP_MIN: Final = 8
COOL_TEMP_MAX: Final = 28
AUTO_TEMP_MIN: Final = 8
AUTO_TEMP_MAX: Final = 40

# Entering a non-OFF mode triggers a device-side per-mode setpoint
# restore push ~430-500 ms later, so callers that chain set_temperature
# after a mode change block briefly to avoid racing the restore.
MODE_TRANSITION_SETTLE: Final = 0.7
