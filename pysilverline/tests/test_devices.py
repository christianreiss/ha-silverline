"""Pin the layout-registry collapse: canonical key, defaults, object identity."""

from __future__ import annotations

from pysilverline.devices import get_layout
from pysilverline.layouts import (
    LAYOUT_BY_NAME,
    LAYOUT_NANO_5KW,
    LAYOUT_NANO_FI_3KW,
    LAYOUT_PC_INV_120,
    LAYOUT_STANDARD,
    LAYOUT_V34_WFZEIYN,
    layout_for_model,
)


def test_layout_for_model_canonical_key() -> None:
    assert layout_for_model("silverline_v34") is LAYOUT_V34_WFZEIYN


def test_layout_for_model_pc_inv_120v2_key() -> None:
    layout = layout_for_model("pc_inv_120v2")
    assert layout is LAYOUT_PC_INV_120
    assert layout.temp_current_divisor == 10


def test_standard_layout_has_no_temp_scaling() -> None:
    assert LAYOUT_STANDARD.temp_current_divisor == 1


def test_layout_for_model_standard_key() -> None:
    assert layout_for_model("standard") is LAYOUT_STANDARD


def test_layout_for_model_empty_defaults_to_standard() -> None:
    assert layout_for_model("") is LAYOUT_STANDARD


def test_layout_for_model_unknown_defaults_to_standard() -> None:
    assert layout_for_model("pc_slp090n") is LAYOUT_STANDARD


def test_layout_by_name_legacy_aliases() -> None:
    assert LAYOUT_BY_NAME["standard"] is LAYOUT_STANDARD
    assert LAYOUT_BY_NAME["v34_wfzeiyn"] is LAYOUT_V34_WFZEIYN


def test_get_layout_canonical_and_default() -> None:
    assert get_layout("silverline_v34") is LAYOUT_V34_WFZEIYN
    assert get_layout("anything-else") is LAYOUT_STANDARD


def test_layout_for_model_nano_fi_3kw_key() -> None:
    assert layout_for_model("nano_fi_3kw") is LAYOUT_NANO_FI_3KW


def test_v34_wfzeiyn_dp_mapping() -> None:
    """Pin the v3.4 wfzeiyn DP mapping from live hardware: DP 120/121 are
    electrical diagnostics, not a runtime-hours counter."""
    layout = LAYOUT_V34_WFZEIYN
    assert layout.total_hours is None
    assert layout.ac_voltage == 120
    assert layout.ac_current == 121
    # This layout's condensing_temp/superheat/target_condensing (124/132/142)
    # collide numerically with the Nano Fi 3kW's config-setpoint DPs
    # (issue #19). heating_time etc. must stay unmapped here — a
    # dp_keys-only gate on the number platform would otherwise attach a
    # "Heating time" entity to this firmware's condensing-temp DP.
    assert layout.heating_time is None
    assert layout.defrost_temp is None
    assert layout.max_temp_limit is None


def test_ac_current_divisor_defaults_to_whole_amps() -> None:
    """Every AC-capable layout currently reads DP 121 as whole amps. The
    scale is unconfirmed (see DpLayout) — this pins the current choice so
    flipping one variant to tenths is a deliberate, visible change rather
    than a silent 10x shift in everyone's derived power and energy."""
    assert LAYOUT_STANDARD.ac_current_divisor == 1
    for layout in (LAYOUT_V34_WFZEIYN, LAYOUT_NANO_FI_3KW):
        assert layout.ac_current == 121
        assert layout.ac_current_divisor == 1


def test_ac_current_divisor_scales_tenths_of_an_amp() -> None:
    """A layout that does report tenths yields real amps as a float."""
    from dataclasses import replace

    from pysilverline import DeviceState

    tenths = replace(LAYOUT_V34_WFZEIYN, ac_current_divisor=10)
    state = DeviceState.from_dps({"120": 232, "121": 43}, layout=tenths)
    assert state.ac_current == 4.3


def test_layout_by_name_nano_fi_3kw_alias() -> None:
    assert LAYOUT_BY_NAME["nano_fi_3kw"] is LAYOUT_NANO_FI_3KW


def test_nano_fi_3kw_dp_mapping() -> None:
    """Pin the DP mapping cross-checked against the official Tuya schema
    for pid am4nomaadnhwvekq — regression guard against re-introducing the
    "other"-fallback bug (DP 120 read as total_hours instead of ac_voltage,
    inlet/outlet/ambient temps swapped with the outdoor-coil DPs)."""
    layout = LAYOUT_NANO_FI_3KW
    assert layout.inlet_temp == 103
    assert layout.outlet_temp == 104
    assert layout.outdoor_coil_temp == 105
    assert layout.ambient_temp == 106
    assert layout.indoor_coil_temp == 108
    assert layout.actual_frequency == 110
    assert layout.water_pump == 111
    assert layout.suction_temp == 117
    # Confirmed on a Nano Fi 5kW (same pid, larger sibling) via issue #19.
    assert layout.target_frequency == 109
    assert layout.defrosting == 115
    # DP 124/132/142 are configuration setpoints on this pid (heating time,
    # defrost temp, max temp — tuya-local schema, issue #19), not
    # condensing_temp/superheat/target_condensing telemetry. Must stay
    # unmapped rather than reintroduce the earlier mislabeling.
    assert layout.condensing_temp is None
    assert layout.superheat is None
    assert layout.target_condensing is None
    # DP 120 on this firmware is AC line voltage, not a runtime-hours
    # counter — must stay unmapped rather than reused for total_hours.
    assert layout.total_hours is None
    # No distinct pool-water probe — aliased to the core DP 3 reading
    # (same value already used for temp_current) instead of left unmapped.
    assert layout.pool_temp == 3
    assert layout.temp_current_divisor == 1
    assert layout.ac_voltage == 120
    assert layout.ac_current == 121
    # Installer-menu config setpoints (issue #19 follow-up, richardc1983) —
    # tuya-local models the whole 124-145 block as writable `number`
    # entities; wired onto dedicated fields distinct from the reverted
    # condensing_temp/superheat/target_condensing above.
    assert layout.heating_time == 124
    assert layout.defrost_time_limit == 125
    assert layout.defrost_cutout_temp == 126
    assert layout.heating_start_hysteresis == 127
    assert layout.heating_end_hysteresis == 128
    assert layout.cooling_start_hysteresis == 130
    assert layout.cooling_end_hysteresis == 131
    assert layout.defrost_temp == 132
    assert layout.max_temp_limit == 142
    assert layout.min_temp_limit == 145


def test_layout_for_model_nano_5kw_key() -> None:
    assert layout_for_model("nano_5kw") is LAYOUT_NANO_5KW


def test_layout_by_name_nano_5kw_alias() -> None:
    assert LAYOUT_BY_NAME["nano_5kw"] is LAYOUT_NANO_5KW


def test_nano_5kw_dp_mapping() -> None:
    """Pin the Nano 5kW family's minimal DP mapping (issue #16 / #18):
    fault lives on DP 21 instead of the standard DP 13, and every
    diagnostic field this hardware doesn't expose stays unmapped rather
    than inheriting a standard-layout default that would coincide with an
    unrelated DP (e.g. DP 101 being a boolean, not suction_temp, on this
    firmware — issue #18)."""
    layout = LAYOUT_NANO_5KW
    assert layout.fault == 21
    assert layout.suction_temp is None
    assert layout.outlet_temp is None
    assert layout.ambient_temp is None
    assert layout.pool_temp is None
    assert layout.discharge_temp is None
    assert layout.inlet_temp is None
    assert layout.actual_frequency is None
    assert layout.target_frequency is None
    assert layout.water_pump is None
    assert layout.total_hours is None
    assert layout.ac_voltage is None
    assert layout.ac_current is None


def test_default_layouts_still_map_fault_to_dp13() -> None:
    """Every pre-existing layout must default to `fault=13` — the Nano 5kW
    override must not silently change what any other model reads."""
    for layout in (
        LAYOUT_STANDARD,
        LAYOUT_V34_WFZEIYN,
        LAYOUT_PC_INV_120,
        LAYOUT_NANO_FI_3KW,
    ):
        assert layout.fault == 13
