"""Unit tests for util.compute_hvac_action — covers branches not
naturally exercised by the climate-entity integration tests, especially
the COOL and HEAT_COOL paths gated on DP-108 actual_frequency."""

from __future__ import annotations

from homeassistant.components.climate.const import HVACAction
from pysilverline import DeviceState

from homeassistant.components.climate.const import HVACMode

from custom_components.poolex_silverline.util import (
    compute_hvac_action,
    derive_hvac_mode,
    derive_preset,
    mask_device_id,
)
from custom_components.poolex_silverline.const import PRESET_BOOST, PRESET_ECO, PRESET_NONE


def test_compute_hvac_action_cool_idle_when_actual_frequency_zero() -> None:
    """Cool mode + DP 108 == 0 is authoritative: compressor parked → IDLE,
    independent of the temp delta. Without this branch the temp-delta
    fallback would say COOLING any time the pool is over setpoint."""
    state = DeviceState.from_dps({"1": True, "4": "Cool", "2": 22, "3": 25, "108": 0})
    assert compute_hvac_action(state) is HVACAction.IDLE


def test_compute_hvac_action_heat_cool_idle_when_actual_frequency_zero() -> None:
    """HEAT_COOL/Auto + DP 108 == 0 → IDLE regardless of temp delta.
    Mirrors the COOL branch above so the climate icon doesn't claim
    HEATING/COOLING when the compressor is parked."""
    state = DeviceState.from_dps({"1": True, "4": "Auto", "2": 27, "3": 25, "108": 0})
    assert compute_hvac_action(state) is HVACAction.IDLE


def test_compute_hvac_action_heat_cool_idle_when_at_target() -> None:
    """HEAT_COOL with no DP 108 and current==target falls through to the
    IDLE return at the end of the HEAT_COOL block — neither heating nor
    cooling is needed."""
    state = DeviceState.from_dps({"1": True, "4": "Auto", "2": 27, "3": 27})
    assert compute_hvac_action(state) is HVACAction.IDLE


# --- PC-INV-120V2 mode vocabulary (issue #5) ---


def test_derive_hvac_mode_pc_inv_120_heat_strings() -> None:
    """heat / h_powerful / h_silent all decode to HVACMode.HEAT."""
    for mode_str in ("heat", "h_powerful", "h_silent"):
        state = DeviceState.from_dps({"1": True, "4": mode_str})
        assert derive_hvac_mode(state) is HVACMode.HEAT, f"failed for {mode_str!r}"


def test_derive_hvac_mode_pc_inv_120_cool_strings() -> None:
    """cool / c_powerful / c_silent all decode to HVACMode.COOL."""
    for mode_str in ("cool", "c_powerful", "c_silent"):
        state = DeviceState.from_dps({"1": True, "4": mode_str})
        assert derive_hvac_mode(state) is HVACMode.COOL, f"failed for {mode_str!r}"


def test_derive_hvac_mode_pc_inv_120_auto_strings() -> None:
    """auto / a_powerful / a_silent all decode to HVACMode.HEAT_COOL."""
    for mode_str in ("auto", "a_powerful", "a_silent"):
        state = DeviceState.from_dps({"1": True, "4": mode_str})
        assert derive_hvac_mode(state) is HVACMode.HEAT_COOL, f"failed for {mode_str!r}"


def test_derive_preset_pc_inv_120_heat_presets() -> None:
    """h_powerful → boost, h_silent → eco, heat → none."""
    assert derive_preset(DeviceState.from_dps({"1": True, "4": "heat"})) == PRESET_NONE
    assert derive_preset(DeviceState.from_dps({"1": True, "4": "h_powerful"})) == PRESET_BOOST
    assert derive_preset(DeviceState.from_dps({"1": True, "4": "h_silent"})) == PRESET_ECO


def test_derive_preset_pc_inv_120_cool_presets() -> None:
    """c_powerful → boost, c_silent → eco, cool → none."""
    assert derive_preset(DeviceState.from_dps({"1": True, "4": "cool"})) == PRESET_NONE
    assert derive_preset(DeviceState.from_dps({"1": True, "4": "c_powerful"})) == PRESET_BOOST
    assert derive_preset(DeviceState.from_dps({"1": True, "4": "c_silent"})) == PRESET_ECO


def test_derive_hvac_mode_off_overrides_mode_string() -> None:
    """DP 1 = False → HVACMode.OFF regardless of what DP 4 carries."""
    state = DeviceState.from_dps({"1": False, "4": "heat"})
    assert derive_hvac_mode(state) is HVACMode.OFF


def test_derive_hvac_mode_auto_variants_idle() -> None:
    """auto/a_powerful/a_silent each still produce IDLE when DP 108 == 0."""
    for mode_str in ("auto", "a_powerful", "a_silent"):
        state = DeviceState.from_dps({"1": True, "4": mode_str, "2": 27, "3": 25, "108": 0})
        assert compute_hvac_action(state) is HVACAction.IDLE, f"failed for {mode_str!r}"


def test_mask_device_id_truncates_long_id() -> None:
    """A real 22-char Tuya device_id collapses to first 6 chars + ellipsis."""
    assert mask_device_id("bf12345678abcdefghijkl") == "bf1234..."


def test_mask_device_id_passes_through_short_id() -> None:
    """Short strings (<= 6 chars) are returned verbatim — there's nothing
    to mask, and trimming further would surrender the only correlator a
    log reader has."""
    assert mask_device_id("abc") == "abc"
    assert mask_device_id("abcdef") == "abcdef"
