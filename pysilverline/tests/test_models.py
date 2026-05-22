"""DeviceState construction + type coercion."""

from __future__ import annotations

from pysilverline.models import DeviceState


def test_from_dps_extracts_well_typed_values() -> None:
    state = DeviceState.from_dps(
        {
            "1": True,
            "2": 28,
            "3": 26,
            "4": "Heat",
            "13": 0,
            "108": 65,
            "111": True,
        }
    )
    assert state.power is True
    assert state.temp_set == 28
    assert state.temp_current == 26
    assert state.mode == "Heat"
    assert state.fault == 0
    assert state.actual_frequency == 65
    assert state.water_pump is True
    # raw is preserved verbatim for diagnostics regardless of type checks.
    assert state.raw == {
        "1": True,
        "2": 28,
        "3": 26,
        "4": "Heat",
        "13": 0,
        "108": 65,
        "111": True,
    }


def test_from_dps_coerces_int_for_power_to_none_not_true() -> None:
    """A firmware (or a malformed frame) that ships DP 1 as 0/1 instead
    of bool must NOT be silently coerced into the bool field — that
    would let a Python `0` look like power-off, which is correct, but
    the downstream entity machinery distinguishes "off" from "missing"
    via the None case. Return None for type-mismatched DPs and leave
    the raw payload available for diagnostics."""
    state = DeviceState.from_dps({"1": 1})
    assert state.power is None
    # raw still has the original wire value so a diagnostics download
    # can reveal the type issue.
    assert state.raw == {"1": 1}


def test_from_dps_rejects_string_for_int_field() -> None:
    """A DP that arrives as a JSON string when the schema says int —
    for example, a flaky Tuya firmware returning 'temp_set': '28' —
    must not propagate into the typed field, because downstream
    arithmetic (`d.temp_set - d.temp_current`) would raise TypeError
    deep inside an entity update."""
    state = DeviceState.from_dps({"2": "28", "3": 26})
    assert state.temp_set is None
    assert state.temp_current == 26


def test_from_dps_rejects_bool_for_int_field() -> None:
    """bool is a subclass of int in Python: isinstance(True, int) is True.
    The coercion must filter that explicitly so a DP that flips type
    can't silently land in a numeric field as 0 or 1."""
    state = DeviceState.from_dps({"108": True})
    assert state.actual_frequency is None
