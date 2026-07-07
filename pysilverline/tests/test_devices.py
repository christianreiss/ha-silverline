"""Pin the layout-registry collapse: canonical key, defaults, object identity."""

from __future__ import annotations

from pysilverline.devices import get_layout
from pysilverline.layouts import (
    LAYOUT_BY_NAME,
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
    # DP 120 on this firmware is AC line voltage, not a runtime-hours
    # counter — must stay unmapped rather than reused for total_hours.
    assert layout.total_hours is None
    # No dedicated pool-water probe distinct from the core DP 3 reading.
    assert layout.pool_temp is None
    assert layout.temp_current_divisor == 1
