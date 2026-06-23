"""Constants for the Poolex Silverline integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from pysilverline.devices import MODEL_PC_INV_120, MODEL_SILVERLINE_V34

DOMAIN: Final = "poolex_silverline"
MANUFACTURER: Final = "Poolex"
MODEL: Final = "Silverline Inverter (PC-SLP090N)"  # legacy fallback

CONF_DEVICE_ID: Final = "device_id"
CONF_LOCAL_KEY: Final = "local_key"
CONF_PROTOCOL_VERSION: Final = "protocol_version"
CONF_MODEL: Final = "model"


@dataclass(frozen=True)
class DeviceProfile:
    """Static descriptor for a supported heat-pump model."""

    display_name: str
    known_dps: frozenset[int] | None  # None → live-detect from first poll
    # Per-model DP-4 write strings (None → fall back to global PRESET_TO_*_DP).
    # Different OEM firmware variants use different enum vocabularies on the wire.
    preset_to_heat_dp: dict[str, str] | None = None
    preset_to_cool_dp: dict[str, str] | None = None
    auto_dp: str | None = None  # DP-4 string to write for HEAT_COOL mode


DEVICE_PROFILES: Final[dict[str, DeviceProfile]] = {
    "pc_slp090n": DeviceProfile(
        display_name="Poolex PC-SLP090N",
        known_dps=frozenset({1, 2, 3, 4, 13}),  # confirmed live
    ),
    "jetline_fi": DeviceProfile(
        display_name="Poolex JetLine Selection FI",
        known_dps=frozenset(
            {1, 2, 3, 4, 13, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111}
        ),
    ),
    "brustec_br80": DeviceProfile(
        display_name="Brustec BR-80",
        known_dps=None,
    ),
    "phalen_calidi": DeviceProfile(
        display_name="Phalén Calidi XP",
        known_dps=None,
    ),
    "nulite": DeviceProfile(
        display_name="Nulite",
        known_dps=None,
    ),
    "fi_150": DeviceProfile(
        display_name="Poolex Silverline FI 150",
        known_dps=None,  # live-detect; full DP set TBD once mapping is verified
    ),
    MODEL_PC_INV_120: DeviceProfile(
        # OEM Poolstar PC-INV-120V2 (Poolex Silverline FI 120 V2 sibling),
        # issue #5. Reports DP 3 (current temp) in tenths of a degree — the
        # ÷10 scaling lives in LAYOUT_PC_INV_120. Minimal-DP firmware
        # (1, 2, 3, 4, 9 observed), so live-detect the entity set.
        # Uses a different DP-4 mode vocabulary than standard firmware:
        # heat/h_powerful/h_silent/cool/c_powerful/c_silent/auto/a_powerful/a_silent.
        display_name="Poolex Silverline FI 120 V2 / PC-INV-120V2 (tenths °C)",
        known_dps=None,
        preset_to_heat_dp={"none": "heat", "boost": "h_powerful", "eco": "h_silent"},
        preset_to_cool_dp={"none": "cool", "boost": "c_powerful", "eco": "c_silent"},
        auto_dp="auto",
    ),
    MODEL_SILVERLINE_V34: DeviceProfile(
        # Tuya v3.4 firmware (productKey wfzeiyn1ed3axxde). Distinct DP numbering
        # — fan on 114, suction/outlet swapped — handled by LAYOUT_V34_WFZEIYN.
        # Contributed by Martin Čarek (@olomouckyorel) from real hardware.
        display_name="Poolex Silverline (Tuya v3.4 / wfzeiyn1ed3axxde)",
        known_dps=frozenset(
            {
                1,
                2,
                3,
                4,
                13,
                101,
                102,
                103,
                105,
                106,
                108,
                109,
                110,
                111,
                114,
                120,
                124,
                132,
                133,
                137,
                140,
                142,
            }
        ),
    ),
    "other": DeviceProfile(
        display_name="Other / Unknown",
        known_dps=None,
    ),
}

DEFAULT_PORT: Final = 6668
DEFAULT_SCAN_INTERVAL: Final = 30  # seconds; WBR3 reboots if polled <8s

PRESET_NONE: Final = "none"
PRESET_BOOST: Final = "boost"
PRESET_ECO: Final = "eco"

# DP-4 enum suffix mapping helpers used by the climate state machine.
# Read direction (device → HA): maps every known DP-4 string to a preset.
# Multiple firmware vocabularies share this table; keys are the raw wire strings.
HEAT_PREFIX_TO_PRESET: Final = {
    # Standard firmware (PC-SLP090N, JetLine, …)
    "Heat": PRESET_NONE,
    "BoostHeat": PRESET_BOOST,
    "SilentHeat": PRESET_ECO,
    # PC-INV-120V2 / OEM firmware variants (issue #5)
    "heat": PRESET_NONE,
    "h_powerful": PRESET_BOOST,
    "h_silent": PRESET_ECO,
}
COOL_PREFIX_TO_PRESET: Final = {
    # Standard firmware
    "Cool": PRESET_NONE,
    "BoostCool": PRESET_BOOST,
    "SilentCool": PRESET_ECO,
    # PC-INV-120V2 / OEM firmware variants (issue #5)
    "cool": PRESET_NONE,
    "c_powerful": PRESET_BOOST,
    "c_silent": PRESET_ECO,
}
# All DP-4 strings that map to HVACMode.HEAT_COOL across firmware variants.
AUTO_MODE_STRINGS: Final = frozenset({"Auto", "auto", "a_powerful", "a_silent"})

# Write direction (HA → device): default strings for standard firmware.
# Devices with a different vocabulary override these via DeviceProfile fields.
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

# DP 13 bit 0 (E03 water flow) self-trips for a few seconds during
# startup before the filter pump primes, so the Repair-issue raise is
# debounced: the bit must stay set continuously for this many seconds
# before a Repair card surfaces. Other bits raise immediately.
E03_DEBOUNCE_SECONDS: Final = 60.0
