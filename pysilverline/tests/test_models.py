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


def test_from_dps_accepts_nonzero_int_for_water_pump() -> None:
    """FI 150 sends DP 111 as an integer (e.g. 320) not a bool.
    Non-zero should map to True so the binary sensor shows 'on'."""
    state = DeviceState.from_dps({"111": 320})
    assert state.water_pump is True
    assert state.raw == {"111": 320}


def test_from_dps_accepts_zero_int_for_water_pump_as_false() -> None:
    """Integer 0 on DP 111 should map to False (pump off), not None."""
    state = DeviceState.from_dps({"111": 0})
    assert state.water_pump is False


def test_from_dps_extracts_fi150_refrigerant_dps() -> None:
    """Extended diagnostic DPs observed on FI 150 firmware map correctly,
    including negative values for evaporating temp and superheat."""
    state = DeviceState.from_dps(
        {"124": 45, "133": -8, "132": -1, "140": 80}
    )
    assert state.condensing_temp == 45
    assert state.evaporating_temp == -8
    assert state.superheat == -1
    assert state.compressor_load == 80


def test_from_dps_rejects_bool_for_int_field() -> None:
    """bool is a subclass of int in Python: isinstance(True, int) is True.
    The coercion must filter that explicitly so a DP that flips type
    can't silently land in a numeric field as 0 or 1."""
    state = DeviceState.from_dps({"108": True})
    assert state.actual_frequency is None


def test_from_dps_v34_layout_remaps_dp_numbers() -> None:
    """The wfzeiyn v3.4 firmware renumbers DPs: a custom layout reads each
    semantic field from the right wire DP instead of the legacy number."""
    from pysilverline.layouts import LAYOUT_V34_WFZEIYN

    # Wire values keyed by the v3.4 DP numbers (fan on 114, suction on 106,
    # outlet on 101, aux valve on 110, indoor coil on 108, etc.).
    dps = {
        "101": 28,  # outlet water temp
        "102": 18,  # ambient
        "103": 26,  # pool
        "105": 12,  # outdoor coil
        "106": 70,  # return gas / suction
        "108": 40,  # indoor coil
        "109": 240,  # main valve / eev
        "110": 130,  # aux valve
        "111": 850,  # circulation pump rpm
        "114": 600,  # fan speed
    }
    state = DeviceState.from_dps(dps, layout=LAYOUT_V34_WFZEIYN)

    assert state.outlet_temp == 28
    assert state.suction_temp == 70  # DP 106, not 101
    assert state.outdoor_coil_temp == 12  # DP 105 (None on legacy layout)
    assert state.indoor_coil_temp == 40  # DP 108
    assert state.eev_steps == 240  # DP 109 (main valve)
    assert state.aux_valve_opening == 130  # DP 110 (None on legacy layout)
    assert state.water_pump_rpm == 850  # DP 111 as an int
    assert state.fan_speed == 600  # DP 114, not DP 110
    # DPs this firmware does not expose stay None even though legacy DP
    # numbers (104/105/107/108) carry data on other firmwares.
    assert state.discharge_temp is None
    assert state.inlet_temp is None
    assert state.target_frequency is None
    assert state.actual_frequency is None


def test_from_dps_default_layout_unchanged_by_v34_fields() -> None:
    """The legacy default layout leaves the new v3.4-only fields None and keeps
    fan on DP 110 — backward compatibility for existing devices."""
    state = DeviceState.from_dps({"110": 500, "111": True})
    assert state.fan_speed == 500  # legacy fan DP
    assert state.aux_valve_opening is None
    assert state.outdoor_coil_temp is None
    assert state.indoor_coil_temp is None
    assert state.water_pump is True
