"""Replay Issue #22's FI 120 v3.5 heating report through the new profile."""

from homeassistant.core import HomeAssistant
from pysilverline.devices import MODEL_SILVERLINE_FI_120_V35, get_layout

from custom_components.poolex_silverline.sensor_descriptions import (
    descriptions_for_model,
)
from pysilverline import DeviceState

from .test_issue20_field_dump import _entities, _setup

RAW = {
    "1": True,
    "2": 28,
    "3": 20,
    "4": "Heat",
    "13": 0,
    "101": 23,
    "102": 22,
    "103": 20,
    "104": 58,
    "105": 10,
    "106": 8,
    "108": 31,
    "109": 87,
    "110": 87,
    "111": 296,
    "114": 872,
    "115": 0,
    "120": 229,
    "121": 7,
    "124": 45,
    "125": 12,
    "126": 12,
    "127": 0,
    "128": 2,
    "130": 0,
    "131": 2,
    "132": -1,
    "133": -8,
    "137": 10,
    "138": 0,
    "140": 80,
    "141": 0,
    "142": 40,
    "145": 8,
}


async def test_issue22_telemetry_and_climate(hass: HomeAssistant) -> None:
    entry = await _setup(
        hass, "issue22fi120", model=MODEL_SILVERLINE_FI_120_V35, raw=RAW
    )
    sensors = _entities(hass, entry, "sensor")
    for key, expected in {
        "inlet_temperature": "20",
        "outlet_temperature": "23",
        "coil_temperature": "20",
        "return_temperature": "22",
        "ambient_temperature": "58",
        "exhaust_temperature": "10",
        "outdoor_coil_temperature": "8",
        "ipm_temperature": "31",
        "target_frequency": "87",
        "actual_frequency": "87",
        "main_valve_opening": "296",
        "fan_speed": "872",
        "ac_voltage": "229",
        "ac_current": "7",
        "electrical_power": "1603",
        "fault_code": "ok",
    }.items():
        entity = sensors[key]
        if entity.disabled_by is not None:
            description = next(
                d
                for d in descriptions_for_model(MODEL_SILVERLINE_FI_120_V35)
                if d.key == key
            )
            decoded = DeviceState.from_dps(
                RAW, layout=get_layout(MODEL_SILVERLINE_FI_120_V35)
            )
            assert str(description.value_fn(decoded)) == expected, key
        else:
            state = hass.states.get(entity.entity_id)
            assert state is not None, key
            assert state.state == expected, key
    for key in ("total_operating_hours", "water_pump_rpm", "indoor_coil_temperature"):
        assert key not in sensors
    binary = _entities(hass, entry, "binary_sensor")
    assert "water_pump" not in binary
    assert "fault_water_flow" in binary
    climate = _entities(hass, entry, "climate")
    state = hass.states.get(next(iter(climate.values())).entity_id)
    assert state.state == "heat"
    assert state.attributes["current_temperature"] == 20
    assert state.attributes["temperature"] == 28


async def test_issue22_partial_first_poll_keeps_sensor_inventory(
    hass: HomeAssistant,
) -> None:
    entry = await _setup(
        hass,
        "issue22partial",
        model=MODEL_SILVERLINE_FI_120_V35,
        raw={"1": True, "3": 20, "4": "Heat"},
    )
    sensors = _entities(hass, entry, "sensor")
    assert "ipm_temperature" in sensors
    assert "main_valve_opening" in sensors
    assert "maximum_temperature_limit" in sensors
