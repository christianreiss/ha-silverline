"""End-to-end regression against the Silverline FI 150 field dump in issue #20.

The library tests pin the DP map; this replays the reporter's actual
``DP_QUERY`` payload through a full config entry and checks the entities an
owner ends up with. Every assertion here is one of the readings the model
produced while it had no layout of its own and fell back to LAYOUT_STANDARD:
water inlet 10 °C / outlet 6 °C on a 28 °C pool, "total operating hours" =
220 and decreasing, "compressor actual frequency" = 43 Hz with the compressor
stopped, and a boolean "water pump" fed from a DP reading 208-340.

Provenance: issue #20 (@squitel, 2026-09-09), Tuya pid b4zr9ugt1q8xn9af,
protocol v3.5, HA 2026.5.1, integration 0.11.13, pysilverline 0.5.10. The
reporter ran a controlled load transition (setpoint 29 → 26 °C to stop the
compressor, then back) and logged every poll, which is what separates the
IPM temperature from a frequency and the EEV opening from a pump — see
``pysilverline/devices/fi_150.py`` for that log.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pysilverline.devices import MODEL_SILVERLINE_FI_150
from pysilverline.layouts import LAYOUT_SILVERLINE_FI_150
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.poolex_silverline.const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MODEL,
    DOMAIN,
)
from custom_components.poolex_silverline.sensor_descriptions import FI_150_SENSORS
from pysilverline import DeviceState

# Verbatim from the issue #20 diagnostic report, unit heating at 80 Hz.
RAW = {
    "1": True,
    "2": 29,
    "3": 28,
    "4": "Heat",
    "13": 0,
    "101": 30,
    "102": 21,
    "103": 28,
    "104": 69,
    "105": 10,
    "106": 6,
    "108": 35,
    "109": 80,
    "110": 80,
    "111": 305,
    "114": 817,
    "115": 0,
    "120": 220,
    "121": 10,
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


async def _setup(hass: HomeAssistant, device_id: str) -> MockConfigEntry:
    state = DeviceState.from_dps(RAW, layout=LAYOUT_SILVERLINE_FI_150)

    client = MagicMock()
    client.host, client.port, client.device_id = "10.0.0.70", 6668, device_id
    client.connected, client.state = True, state
    client.detected_version = "3.5"
    client.dp_layout = LAYOUT_SILVERLINE_FI_150
    for method in ("connect", "disconnect", "set_dp", "set_multiple"):
        setattr(client, method, AsyncMock(return_value=None))
    client.get_status = AsyncMock(return_value=state)
    client.add_listener = MagicMock(return_value=lambda: None)
    client.add_connection_listener = MagicMock(return_value=lambda: None)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=device_id,
        data={
            CONF_HOST: "10.0.0.70",
            CONF_PORT: 6668,
            CONF_DEVICE_ID: device_id,
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_MODEL: MODEL_SILVERLINE_FI_150,
        },
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.poolex_silverline.SilverlineClient", return_value=client
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _entities(hass: HomeAssistant, entry: MockConfigEntry, domain: str) -> dict:
    device_id = entry.data[CONF_DEVICE_ID]
    registry = er.async_get(hass)
    return {
        e.unique_id.removeprefix(f"{device_id}_"): e
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == domain
    }


async def test_issue20_water_temperatures_are_physically_possible(
    hass: HomeAssistant,
) -> None:
    """Inlet/outlet must bracket the pool temperature, not sit 18 °C below it.

    The standard layout read them off DP 105/106 — the suction gas and the
    outdoor coil — and told the reporter their 28 °C pool was flowing in at
    10 °C and out at 6 °C while the unit was heating.
    """
    entry = await _setup(hass, "bf1122334455fi150dump")
    sensors = _entities(hass, entry, "sensor")

    def _state(key: str) -> str | None:
        s = hass.states.get(sensors[key].entity_id)
        return s.state if s is not None else None

    assert _state("inlet_temperature") == "28"
    assert _state("outlet_temperature") == "30"
    assert _state("coil_temperature") == "28"  # reads d.pool_temp
    assert _state("return_temperature") == "21"  # reads d.ambient_temp
    assert _state("ambient_temperature") == "69"  # reads d.discharge_temp
    assert _state("exhaust_temperature") == "10"  # reads d.suction_temp
    assert _state("outdoor_coil_temperature") == "6"


async def test_issue20_dp108_is_the_ipm_temperature_not_a_frequency(
    hass: HomeAssistant,
) -> None:
    """DP 108 rose while the compressor was stopped: it is the heatsink.

    Under the standard layout it was ``actual_frequency`` and showed 43 Hz on
    a stopped compressor drawing 0 A. The real frequency is DP 110.
    """
    entry = await _setup(hass, "bf1122334455fi150ipm0")
    sensors = _entities(hass, entry, "sensor")

    assert hass.states.get(sensors["ipm_temperature"].entity_id).state == "35"
    assert hass.states.get(sensors["actual_frequency"].entity_id).state == "80"
    # Not the indoor coil: on the Nano Fi, DP 108 genuinely is that probe, so
    # the two must stay distinct entities rather than one being reused.
    assert "indoor_coil_temperature" not in sensors


async def test_issue20_no_phantom_hours_pump_or_refrigeration_entities(
    hass: HomeAssistant,
) -> None:
    """Every entity the fallback layout invented must be gone.

    DP 120 is mains voltage (the "hours" counter decreased), DP 111 is the
    main EEV opening (the boolean pump was fed from a 208-340 integer), and
    DP 124/132/133/137/140/142 are installer setpoints held constant across a
    full load transition, not circuit telemetry.
    """
    entry = await _setup(hass, "bf1122334455fi150ghost")
    sensors = _entities(hass, entry, "sensor")
    binary_sensors = _entities(hass, entry, "binary_sensor")

    assert "total_operating_hours" not in sensors
    assert "water_pump_rpm" not in sensors
    assert "water_pump" not in binary_sensors
    for key in (
        "condensing_temperature",
        "evaporating_temperature",
        "superheat",
        "compressor_load",
        "target_superheat",
        "target_condensing_temperature",
    ):
        assert key not in sensors, key
    # DP 111 is published as the main EEV opening in steps instead. eev_steps
    # would gate on DP 109, which is target_frequency on this firmware.
    assert "main_valve_opening" in sensors
    assert "eev_steps" not in sensors
    assert hass.states.get(sensors["ac_voltage"].entity_id).state == "220"
    # 220 V x 10 A ~ 2.2 kW at 80 Hz — the derived power sensor only works
    # because DP 120/121 are mapped as electrical, and it is the sanity check
    # on that reading.
    assert hass.states.get(sensors["electrical_power"].entity_id).state == "2200"


async def test_issue20_installer_block_and_fault_table(hass: HomeAssistant) -> None:
    """The 124-145 block registers as read-only installer sensors, and the
    fault decode follows the Full Inverter table (DP 13, bit 8 = water flow).
    """
    entry = await _setup(hass, "bf1122334455fi150cfg0")
    sensors = _entities(hass, entry, "sensor")
    binary_sensors = _entities(hass, entry, "binary_sensor")

    # Disabled by default (ten commissioning-only entities), so assert the
    # registration and read the values through the descriptions instead.
    state = DeviceState.from_dps(RAW, layout=LAYOUT_SILVERLINE_FI_150)
    for key, expected in (
        ("heating_time", 45),
        ("defrost_time_limit", 12),
        ("defrost_cutout_temperature", 12),
        ("heating_start_hysteresis", 0),
        ("heating_end_hysteresis", 2),
        ("cooling_start_hysteresis", 0),
        ("cooling_end_hysteresis", 2),
        ("defrost_temperature", -1),
        ("maximum_temperature_limit", 40),
        ("minimum_temperature_limit", 8),
    ):
        assert key in sensors, key
        assert sensors[key].disabled_by is not None, key
        description = next(d for d in FI_150_SENSORS if d.key == key)
        assert description.value_fn(state) == expected, key

    # Full Inverter fault table: bit 8 is the water-flow switch, so no
    # "defrost sensor" entity exists to mislead an owner whose pump is off.
    assert "fault_water_flow" in binary_sensors
    assert "fault_defrost_sensor" not in binary_sensors
    assert hass.states.get(sensors["fault_code"].entity_id).state == "ok"


async def test_issue20_climate_reads_the_dump(hass: HomeAssistant) -> None:
    """The control surface is unaffected by the DP remap: DP 1/2/3/4 are
    identical across every layout, and this firmware speaks the standard
    "Heat" mode vocabulary."""
    entry = await _setup(hass, "bf1122334455fi150clim")
    climate = _entities(hass, entry, "climate")
    assert len(climate) == 1
    state = hass.states.get(next(iter(climate.values())).entity_id)
    assert state is not None
    assert state.state == "heat"
    assert state.attributes["current_temperature"] == 28
    assert state.attributes["temperature"] == 29
