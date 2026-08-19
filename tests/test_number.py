"""Number tests — standalone target_temperature entity with mode-aware min/max."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.components.number import (
    ATTR_MAX,
    ATTR_MIN,
    ATTR_VALUE,
    SERVICE_SET_VALUE,
)
from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from pysilverline import DeviceState

ENTITY_ID = "number.pool_heatpump_target_temperature"


async def test_entity_registers_when_dp2_present(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """state_pool_running has DP 2, so the number entity must register."""
    registry = er.async_get(hass)
    entry = registry.async_get(ENTITY_ID)
    assert entry is not None
    assert entry.config_entry_id == init_integration.entry_id


async def test_entity_skipped_when_dp2_absent(
    hass: HomeAssistant, mock_client_factory, config_entry: MockConfigEntry
) -> None:
    """A firmware variant that never reports DP 2 should not register the
    number entity at all (rather than landing it as a permanent
    `unavailable` ghost in the registry)."""
    mock_client_factory.get_status = AsyncMock(
        return_value=DeviceState.from_dps({"1": True, "3": 25, "4": "Heat", "13": 0})
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get(ENTITY_ID) is None


async def test_native_value_reads_temp_set(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """state_pool_running sets DP 2 = 28."""
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert float(state.state) == 28.0


async def test_native_value_updates_on_push(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 32, "3": 26, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    assert float(hass.states.get(ENTITY_ID).state) == 32.0


async def test_min_max_in_heat_mode(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """Heat: 15..40."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 26, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_MIN] == 15
    assert state.attributes[ATTR_MAX] == 40


async def test_min_max_in_cool_mode(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """Cool: 8..28."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 20, "4": "Cool", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_MIN] == 8
    assert state.attributes[ATTR_MAX] == 28


async def test_min_max_in_auto_mode(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """Auto: 8..40 (union of heat/cool)."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 22, "4": "Auto", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_MIN] == 8
    assert state.attributes[ATTR_MAX] == 40


async def test_min_max_defaults_to_heat_when_off(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """OFF: default to Heat range so the slider remains usable."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": False, "2": 26, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_MIN] == 15
    assert state.attributes[ATTR_MAX] == 40


async def test_min_max_defaults_to_heat_on_unknown_mode(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """A mode string the integration doesn't know about (firmware
    extension, partial push) should fall back to Heat rather than
    leaving the slider in an unbounded state."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 26, "4": "Mystery", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_MIN] == 15
    assert state.attributes[ATTR_MAX] == 40


async def test_set_value_rounds_to_int_and_writes_dp2(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """async_set_native_value rounds float → int and writes DP 2."""
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_VALUE: 25.7},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({2: 26})

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_VALUE: 25.4},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({2: 25})


async def test_set_value_integer_passes_through(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_VALUE: 30},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({2: 30})


async def test_unavailable_when_temp_set_none(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """When the coordinator's state omits DP 2 (push of a partial frame),
    the entity must surface as unavailable rather than rendering ``None``
    or 0 as a real setpoint."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


async def test_native_value_returns_none_when_coordinator_data_none(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """Direct property read when coordinator.data is None must return
    None rather than crashing — covers the early-return guard before a
    real first push has landed."""
    from homeassistant.helpers.entity_component import EntityComponent

    coordinator = init_integration.runtime_data
    coordinator.data = None
    component: EntityComponent = hass.data["number"]
    entity = next(e for e in component.entities if e.entity_id == ENTITY_ID)
    assert entity.native_value is None


async def test_set_value_surfaces_invalid_auth_as_homeassistant_error(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """When the device rejects the write because the key rotated, the
    number entity must surface HomeAssistantError with the auth_failed
    translation key — matches what climate/switch do."""
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    from pysilverline import InvalidAuth

    mock_client_factory.set_multiple.side_effect = InvalidAuth("rotated")
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: ENTITY_ID, ATTR_VALUE: 26},
            blocking=True,
        )
    assert exc.value.translation_key == "auth_failed"


async def test_set_value_surfaces_cannot_connect_as_homeassistant_error(
    hass: HomeAssistant, mock_client_factory, init_integration: MockConfigEntry
) -> None:
    """A network drop during a slider write becomes a translated
    HomeAssistantError, not a 500 from the service layer."""
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    from pysilverline import CannotConnect

    mock_client_factory.set_multiple.side_effect = CannotConnect("network down")
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: ENTITY_ID, ATTR_VALUE: 26},
            blocking=True,
        )
    assert exc.value.translation_key == "set_failed"


async def test_nano_fi_config_numbers_register_disabled_with_correct_bounds(
    hass: HomeAssistant,
) -> None:
    """Nano Fi 3kW config setpoints (DP 124-145, issue #19 follow-up) register
    as CONFIG-category, disabled-by-default number entities with the
    tuya-local-derived range as their min/max, and read the right
    DeviceState field. Writing goes to the DP named by the description's
    own dp_keys, not a shared constant."""
    from unittest.mock import MagicMock, patch

    from homeassistant.const import CONF_HOST, CONF_PORT
    from pysilverline.layouts import LAYOUT_NANO_FI_3KW
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.poolex_silverline.const import (
        CONF_DEVICE_ID,
        CONF_LOCAL_KEY,
        CONF_MODEL,
        DOMAIN,
    )
    from custom_components.poolex_silverline.number import (
        NANO_FI_CONFIG_NUMBERS,
        SilverlineNumber,
    )

    device_id = "bf11223344nanoficonf"
    nano_fi_dps = {
        "1": True,
        "2": 30,
        "3": 30,
        "4": "Heat",
        "13": 0,
        "124": 45,
        "125": 8,
        "126": 18,
        "127": 3,
        "128": 1,
        "130": 2,
        "131": 1,
        "132": -5,
        "142": 40,
        "145": 7,
    }
    state = DeviceState.from_dps(nano_fi_dps, layout=LAYOUT_NANO_FI_3KW)

    client = MagicMock()
    client.host = "10.0.0.61"
    client.port = 6668
    client.device_id = device_id
    client.connected = True
    client.state = state
    client.dp_layout = LAYOUT_NANO_FI_3KW
    client.detected_version = "3.5"
    client.connect = AsyncMock(return_value=None)
    client.disconnect = AsyncMock(return_value=None)
    client.get_status = AsyncMock(return_value=state)
    client.set_dp = AsyncMock(return_value=None)
    client.set_multiple = AsyncMock(return_value=None)
    client.add_listener = MagicMock(return_value=lambda: None)
    client.add_connection_listener = MagicMock(return_value=lambda: None)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=device_id,
        data={
            CONF_HOST: "10.0.0.61",
            CONF_PORT: 6668,
            CONF_DEVICE_ID: device_id,
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_MODEL: "nano_fi_3kw",
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

    registry = er.async_get(hass)
    number_keys = {
        e.unique_id.removeprefix(f"{device_id}_"): e
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "number"
    }
    expected = {d.key: d for d in NANO_FI_CONFIG_NUMBERS}
    assert expected.keys() <= number_keys.keys()
    for key, desc in expected.items():
        registry_entry = number_keys[key]
        assert registry_entry.disabled_by is not None, (
            f"{key} must be disabled by default"
        )
        assert desc.value_fn(state) is not None

    # Spot-check the range + value pulled from tuya-local's declared setpoints.
    assert expected["heating_time"].native_min_value == 30
    assert expected["heating_time"].native_max_value == 120
    assert expected["heating_time"].value_fn(state) == 45
    assert expected["defrost_temperature"].native_min_value == -20
    assert expected["defrost_temperature"].native_max_value == 20
    assert expected["defrost_temperature"].value_fn(state) == -5
    assert expected["maximum_temperature_limit"].value_fn(state) == 40
    assert expected["minimum_temperature_limit"].value_fn(state) == 7

    coordinator = entry.runtime_data
    heating_time_entity = SilverlineNumber(coordinator, expected["heating_time"])
    await heating_time_entity.async_set_native_value(60)
    client.set_multiple.assert_awaited_with({124: 60})


async def test_v34_wfzeiyn_model_does_not_get_nano_fi_config_numbers(
    hass: HomeAssistant,
) -> None:
    """DP 124/132/142 are legitimate condensing_temp/superheat/
    target_condensing telemetry on the v3.4 wfzeiyn firmware — a raw
    dp_keys gate would otherwise attach the Nano Fi 3kW's "Heating time"
    config entity to this firmware's condensing-temp DP. numbers_for_model
    must keep the config-setpoint block Nano Fi 3kW-only."""
    from unittest.mock import MagicMock, patch

    from homeassistant.const import CONF_HOST, CONF_PORT
    from pysilverline.layouts import LAYOUT_V34_WFZEIYN
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.poolex_silverline.const import (
        CONF_DEVICE_ID,
        CONF_LOCAL_KEY,
        CONF_MODEL,
        DOMAIN,
    )

    device_id = "bf99001122v34collide"
    v34_dps = {
        "1": True,
        "2": 28,
        "3": 26,
        "4": "Heat",
        "13": 0,
        "124": 45,  # condensing_temp on this firmware, not heating_time
        "132": -5,  # superheat
        "142": 40,  # target_condensing
    }
    state = DeviceState.from_dps(v34_dps, layout=LAYOUT_V34_WFZEIYN)

    client = MagicMock()
    client.host = "10.0.0.62"
    client.port = 6668
    client.device_id = device_id
    client.connected = True
    client.state = state
    client.dp_layout = LAYOUT_V34_WFZEIYN
    client.detected_version = "3.4"
    client.connect = AsyncMock(return_value=None)
    client.disconnect = AsyncMock(return_value=None)
    client.get_status = AsyncMock(return_value=state)
    client.set_dp = AsyncMock(return_value=None)
    client.set_multiple = AsyncMock(return_value=None)
    client.add_listener = MagicMock(return_value=lambda: None)
    client.add_connection_listener = MagicMock(return_value=lambda: None)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=device_id,
        data={
            CONF_HOST: "10.0.0.62",
            CONF_PORT: 6668,
            CONF_DEVICE_ID: device_id,
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_MODEL: "silverline_v34",
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

    registry = er.async_get(hass)
    number_keys = {
        e.unique_id.removeprefix(f"{device_id}_")
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "number"
    }
    # DP 2 registers target_temperature as normal; none of the Nano Fi
    # config-setpoint keys should appear even though DP 124/132/142 are
    # present in supported_dps.
    assert number_keys == {"target_temperature"}
