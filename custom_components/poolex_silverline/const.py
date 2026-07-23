"""Constants for the Poolex Silverline integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from pysilverline.devices import (
    MODEL_NANO_FI_3KW,
    MODEL_PC_INV_120,
    MODEL_SILVERLINE_V34,
)

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
    # Per-model setpoint clamp bounds (None → fall back to global constants).
    # Empirically determined by live sweep on real hardware; update only when
    # confirmed on the specific model — do not assume from manuals (manuals
    # give a single universal range, not per-mode clamping).
    heat_temp_min: int | None = None
    heat_temp_max: int | None = None
    cool_temp_min: int | None = None
    cool_temp_max: int | None = None
    auto_temp_min: int | None = None
    auto_temp_max: int | None = None


# Setpoint clamp bounds confirmed live on PC-SLP090N by 21-value sweep (see
# silverline-fe-specs.md §11).  All other models in the same Poolstar OEM
# family share this firmware and are assumed to have identical per-mode
# bounds.  Models with unverified bounds (Nulite, Other) leave these None so
# they fall back to the global constants.
_STD_HEAT_MIN: Final = 15
_STD_HEAT_MAX: Final = 40
_STD_COOL_MIN: Final = 8
_STD_COOL_MAX: Final = 28
_STD_AUTO_MIN: Final = 8
_STD_AUTO_MAX: Final = 40

DEVICE_PROFILES: Final[dict[str, DeviceProfile]] = {
    "pc_slp090n": DeviceProfile(
        display_name="Poolex PC-SLP090N",
        known_dps=frozenset({1, 2, 3, 4, 13}),  # confirmed live
        heat_temp_min=_STD_HEAT_MIN,
        heat_temp_max=_STD_HEAT_MAX,
        cool_temp_min=_STD_COOL_MIN,
        cool_temp_max=_STD_COOL_MAX,
        auto_temp_min=_STD_AUTO_MIN,
        auto_temp_max=_STD_AUTO_MAX,
    ),
    "jetline_fi": DeviceProfile(
        display_name="Poolex JetLine Selection FI",
        # Some JetLine units expose only {1,2,3,4,13} (5-DP firmware) while
        # others ship the full 101-111 diagnostic set. Live-detect on first
        # poll so entities match what the actual firmware reports.
        known_dps=None,
        heat_temp_min=_STD_HEAT_MIN,
        heat_temp_max=_STD_HEAT_MAX,
        cool_temp_min=_STD_COOL_MIN,
        cool_temp_max=_STD_COOL_MAX,
        auto_temp_min=_STD_AUTO_MIN,
        auto_temp_max=_STD_AUTO_MAX,
    ),
    "brustec_br80": DeviceProfile(
        display_name="Brustec BR-80",
        known_dps=None,
        heat_temp_min=_STD_HEAT_MIN,
        heat_temp_max=_STD_HEAT_MAX,
        cool_temp_min=_STD_COOL_MIN,
        cool_temp_max=_STD_COOL_MAX,
        auto_temp_min=_STD_AUTO_MIN,
        auto_temp_max=_STD_AUTO_MAX,
    ),
    "phalen_calidi": DeviceProfile(
        display_name="Phalén Calidi XP",
        known_dps=None,
        heat_temp_min=_STD_HEAT_MIN,
        heat_temp_max=_STD_HEAT_MAX,
        cool_temp_min=_STD_COOL_MIN,
        cool_temp_max=_STD_COOL_MAX,
        auto_temp_min=_STD_AUTO_MIN,
        auto_temp_max=_STD_AUTO_MAX,
    ),
    "nulite": DeviceProfile(
        # House-heating firmware variant; per-mode clamp bounds unverified
        # (cloud spec shows DP 2 range 8-60 for domestic hot water use).
        # Leave bounds as None so the global defaults are used until confirmed.
        display_name="Nulite",
        known_dps=None,
    ),
    "fi_150": DeviceProfile(
        display_name="Poolex Silverline FI 150",
        known_dps=None,  # live-detect; full DP set TBD once mapping is verified
        heat_temp_min=_STD_HEAT_MIN,
        heat_temp_max=_STD_HEAT_MAX,
        cool_temp_min=_STD_COOL_MIN,
        cool_temp_max=_STD_COOL_MAX,
        auto_temp_min=_STD_AUTO_MIN,
        auto_temp_max=_STD_AUTO_MAX,
    ),
    "steinbach_silent_mini": DeviceProfile(
        # productKey xiusqryqukyqkq3w (issue #10). Standard DP numbering
        # (confirmed live: 101 suction, 102 ambient, 103 pool, 104 discharge,
        # 106 outlet, 107/108 target/actual frequency — matches LAYOUT_STANDARD),
        # but DP 4 uses full-word mode strings ("Heating"/"Cooling") instead of
        # the standard family's "Heat"/"Cool". Writing "Cool" left the device
        # stuck reporting Heating regardless of the requested mode. Boost/eco
        # variants unconfirmed on this firmware; falls back to the plain
        # Heating/Cooling string for those presets too.
        display_name="Steinbach Silent Mini",
        known_dps=frozenset({1, 2, 3, 4, 101, 102, 103, 104, 106, 107, 108}),
        preset_to_heat_dp={"none": "Heating"},
        preset_to_cool_dp={"none": "Cooling"},
        heat_temp_min=_STD_HEAT_MIN,
        heat_temp_max=_STD_HEAT_MAX,
        cool_temp_min=_STD_COOL_MIN,
        cool_temp_max=_STD_COOL_MAX,
        auto_temp_min=_STD_AUTO_MIN,
        auto_temp_max=_STD_AUTO_MAX,
    ),
    MODEL_PC_INV_120: DeviceProfile(
        # OEM Poolstar PC-INV-120V2 (Poolex Silverline FI 120 V2 sibling),
        # issue #5. Reports DP 3 (current temp) in tenths of a degree — the
        # ÷10 scaling lives in LAYOUT_PC_INV_120. Minimal-DP firmware
        # (1, 2, 3, 4, 9 observed), so live-detect the entity set.
        # Uses a different DP-4 mode vocabulary than standard firmware:
        # heat/h_powerful/h_silent/cool/c_powerful/c_silent/auto/a_powerful/a_silent.
        # Per-mode clamp bounds assumed same as standard family; unverified on
        # this specific firmware variant.
        display_name="Poolex Silverline FI 120 V2 / PC-INV-120V2 (tenths °C)",
        known_dps=None,
        preset_to_heat_dp={"none": "heat", "boost": "h_powerful", "eco": "h_silent"},
        preset_to_cool_dp={"none": "cool", "boost": "c_powerful", "eco": "c_silent"},
        auto_dp="auto",
        heat_temp_min=_STD_HEAT_MIN,
        heat_temp_max=_STD_HEAT_MAX,
        cool_temp_min=_STD_COOL_MIN,
        cool_temp_max=_STD_COOL_MAX,
        auto_temp_min=_STD_AUTO_MIN,
        auto_temp_max=_STD_AUTO_MAX,
    ),
    MODEL_SILVERLINE_V34: DeviceProfile(
        # Tuya v3.4 firmware (productKey wfzeiyn1ed3axxde). Distinct DP numbering
        # — fan on 114, suction/outlet swapped — handled by LAYOUT_V34_WFZEIYN.
        # Contributed by Martin Čarek (@olomouckyorel) from real hardware.
        # Per-mode clamp bounds assumed same as standard family; unverified on
        # this specific firmware variant.
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
        heat_temp_min=_STD_HEAT_MIN,
        heat_temp_max=_STD_HEAT_MAX,
        cool_temp_min=_STD_COOL_MIN,
        cool_temp_max=_STD_COOL_MAX,
        auto_temp_min=_STD_AUTO_MIN,
        auto_temp_max=_STD_AUTO_MAX,
    ),
    MODEL_NANO_FI_3KW: DeviceProfile(
        # Poolex Nano Fi 3kW (ref. PC-NANO-B3N), distributed by Poolstar SAS.
        # Tuya pid am4nomaadnhwvekq, protocol v3.5. Cross-checked DP-by-DP
        # against the official Tuya cloud product schema (pulled via the
        # xtend_tuya integration's diagnostics) — see LAYOUT_NANO_FI_3KW for
        # the full per-DP notes. DP-4 mode vocabulary matches the standard
        # family (Heat/Cool/Auto/BoostHeat/SilentHeat/BoostCool/SilentCool),
        # so no preset/auto DP overrides are needed. Live-detect the DP set:
        # confirmed present {1,2,3,4,13,103,104,105,106,108,110,111,117,120,121}
        # on one unit, but DP 109/112/114 are declared in the generic product
        # schema and may exist on other units in this line. Per-mode setpoint
        # clamp bounds are unverified on this specific firmware (only the raw
        # DP 2 range 0-40°C is confirmed) — left as None to fall back to the
        # global defaults rather than assume.
        display_name="Poolex Nano Fi 3kW (PC-NANO-B3N)",
        known_dps=None,
    ),
    "nano_5kw": DeviceProfile(
        # Poolex Nano 5kW WiFi, Tuya pid yk3bytlujz2xshuy, protocol v3.5
        # (issue #16). Cross-checked against the public tuya-local product
        # schema for this exact pid (same OEM hardware, catalogued there as
        # "Varpoolfaye Pool Mini") — confirms the firmware genuinely exposes
        # only {1,2,3,4,21}: no inlet/outlet/suction/discharge temp,
        # frequency, fan, pump, or voltage/current DPs exist on this
        # hardware tier at all. Unlike issue #15 (FI 120), this isn't
        # cloud-gating — those DPs are simply absent from the product's
        # schema. DP 21 is a status/fault bitfield, not a runtime counter;
        # that schema decodes bit 8 (value 256) as a water-flow problem with
        # the remaining bits an undifferentiated "fault present" catch-all,
        # which isn't enough to safely wire up a binary_sensor yet pending
        # hardware confirmation from the reporter. The tuya-local config
        # only models Heat/Cool/Auto for DP 4 — but it maps HA's climate
        # hvac_mode, not this integration's boost/eco presets, so it does
        # NOT prove the firmware rejects BoostHeat/SilentHeat/etc. Unconfirmed
        # either way, so boost/eco presets fall back to the plain string (same
        # defensive pattern as steinbach_silent_mini) rather than risk writing
        # an enum value the device doesn't understand. Per-mode setpoint
        # clamp bounds are
        # unverified (only the raw DP 2 range 5-40°C is confirmed) — left as
        # None to fall back to the global defaults rather than assume.
        display_name="Poolex Nano 5kW WiFi",
        known_dps=None,
        preset_to_heat_dp={"none": "Heat"},
        preset_to_cool_dp={"none": "Cool"},
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
    # Steinbach Silent Mini / productKey xiusqryqukyqkq3w (issue #10)
    "Heating": PRESET_NONE,
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
    # Steinbach Silent Mini / productKey xiusqryqukyqkq3w (issue #10)
    "Cooling": PRESET_NONE,
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
