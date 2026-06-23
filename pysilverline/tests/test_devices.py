"""Pin the layout-registry collapse: canonical key, defaults, object identity."""

from __future__ import annotations

from pysilverline.devices import get_layout
from pysilverline.layouts import (
    LAYOUT_BY_NAME,
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
