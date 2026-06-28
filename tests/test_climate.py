"""Climate state machine: DP-1/DP-4 ↔ HVAC mode + preset."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pysilverline import DeviceState

ENTITY_ID = "climate.pool_heatpump"


@pytest.mark.parametrize(
    "dps,expected_hvac,expected_preset",
    [
        ({"1": False, "4": "Heat"}, HVACMode.OFF, "none"),
        ({"1": True, "4": "Heat"}, HVACMode.HEAT, "none"),
        ({"1": True, "4": "BoostHeat"}, HVACMode.HEAT, "boost"),
        ({"1": True, "4": "SilentHeat"}, HVACMode.HEAT, "eco"),
        ({"1": True, "4": "Cool"}, HVACMode.COOL, "none"),
        ({"1": True, "4": "BoostCool"}, HVACMode.COOL, "boost"),
        ({"1": True, "4": "SilentCool"}, HVACMode.COOL, "eco"),
        ({"1": True, "4": "Auto"}, HVACMode.HEAT_COOL, "none"),
    ],
)
async def test_dp4_enum_decoded_to_hvac_and_preset(
    hass: HomeAssistant,
    mock_client_factory,
    init_integration,
    dps: dict[str, str | bool],
    expected_hvac: HVACMode,
    expected_preset: str,
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"3": 25, **dps}))
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == expected_hvac
    assert state.attributes[ATTR_PRESET_MODE] == expected_preset


async def test_set_hvac_off_writes_dp1_false(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.OFF},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({1: False})


async def test_set_hvac_heat_writes_dp1_true_and_mode(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.HEAT},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({1: True, 4: "Heat"})


async def test_set_hvac_heat_cool_writes_auto(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.HEAT_COOL},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({1: True, 4: "Auto"})


async def test_preset_boost_during_heat_writes_boostheat(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"1": True, "4": "Heat"}))
    await hass.async_block_till_done()

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_PRESET_MODE: "boost"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: "BoostHeat"})


async def test_preset_eco_during_cool_writes_silentcool(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"1": True, "4": "Cool"}))
    await hass.async_block_till_done()

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_PRESET_MODE: "eco"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: "SilentCool"})


async def test_preset_during_auto_raises(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"1": True, "4": "Auto"}))
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as exc:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_PRESET_MODE,
            {ATTR_ENTITY_ID: ENTITY_ID, ATTR_PRESET_MODE: "boost"},
            blocking=True,
        )
    assert exc.value.translation_key == "preset_not_available_in_auto"


async def test_set_temperature_rounds_to_int(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """The entity rounds floats to an int before writing DP 2. Boundary
    behavior (mode-specific min/max) lives in its own test below."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 25.7},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({2: 26})

    # Both endpoints of the active mode (Heat: 15..40) are accepted.
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 15},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({2: 15})

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 40},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({2: 40})


async def test_off_to_heat_preserves_last_preset(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    coordinator = init_integration.runtime_data

    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "BoostHeat"})
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.OFF},
        blocking=True,
    )
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": False, "4": "BoostHeat"})
    )
    await hass.async_block_till_done()

    mock_client_factory.set_multiple.reset_mock()
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.HEAT},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({1: True, 4: "BoostHeat"})


# ---------------------------------------------------------------------------
# Mode-aware setpoint range (Heat 15-40, Cool 8-28, Auto 8-40)
# ---------------------------------------------------------------------------


async def test_min_max_temp_for_heat_mode(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "2": 27, "3": 28})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["min_temp"] == 15
    assert state.attributes["max_temp"] == 40


async def test_min_max_temp_for_cool_mode(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Cool", "2": 18, "3": 22})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["min_temp"] == 8
    assert state.attributes["max_temp"] == 28


async def test_min_max_temp_for_auto_mode(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Auto", "2": 26, "3": 27})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["min_temp"] == 8
    assert state.attributes["max_temp"] == 40


async def test_min_max_temp_when_off_uses_last_direction(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """When OFF, the slider bounds come from _last_direction so the UI
    still shows a sensible range matching the user's last active mode."""
    coordinator = init_integration.runtime_data
    # Start in Cool, then power off — _last_direction should be Cool.
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Cool", "2": 20, "3": 22})
    )
    await hass.async_block_till_done()
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": False, "4": "Cool", "3": 22})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == HVACMode.OFF
    assert state.attributes["min_temp"] == 8
    assert state.attributes["max_temp"] == 28


async def test_set_temperature_out_of_range_in_cool_blocked_by_ha(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """HA's climate service validates target against our mode-aware
    min_temp/max_temp BEFORE we run, so a 35°C write while in Cool
    (max 28) is rejected at the service layer — DP 2 stays untouched."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Cool", "2": 25, "3": 26})
    )
    await hass.async_block_till_done()
    mock_client_factory.set_multiple.reset_mock()

    with pytest.raises(ServiceValidationError) as exc:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 35},
            blocking=True,
        )
    # HA uses its own translation key for the standard temp range check.
    assert exc.value.translation_key == "temp_out_of_range"
    mock_client_factory.set_multiple.assert_not_called()


async def test_set_temperature_below_heat_min_blocked_by_ha(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Writing 10°C while in Heat (min 15) is blocked at HA's service
    validator, again driven by our mode-aware min_temp."""
    # init_integration starts in Heat mode
    mock_client_factory.set_multiple.reset_mock()
    with pytest.raises(ServiceValidationError) as exc:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 10},
            blocking=True,
        )
    assert exc.value.translation_key == "temp_out_of_range"
    mock_client_factory.set_multiple.assert_not_called()


# ---------------------------------------------------------------------------
# Mode-transition settle: the 0.7s sleep after non-OFF set_hvac_mode
# ---------------------------------------------------------------------------


async def test_set_hvac_mode_sleeps_after_non_off_write(
    hass: HomeAssistant, mock_client_factory, init_integration, monkeypatch
) -> None:
    """async_set_hvac_mode should sleep _MODE_TRANSITION_SETTLE after
    a non-OFF write so the device's per-mode-memory restore push
    lands before any chained set_temperature races with it."""
    import custom_components.poolex_silverline.climate as climate_mod

    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(climate_mod.asyncio, "sleep", fake_sleep)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )
    assert climate_mod.MODE_TRANSITION_SETTLE in recorded


async def test_set_temperature_with_hvac_mode_kwarg_switches_mode_first(
    hass: HomeAssistant, mock_client_factory, init_integration, monkeypatch
) -> None:
    """Bundled climate.set_temperature (hvac_mode + temperature in one call)
    must sequence the mode change before the setpoint write — otherwise the
    device's per-mode-memory restore push lands ~500ms later and clobbers
    the setpoint."""
    import custom_components.poolex_silverline.climate as climate_mod

    recorded_sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        recorded_sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(climate_mod.asyncio, "sleep", fake_sleep)

    # init_integration starts in Heat; bundle Heat→Cool with target 25.
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {
            ATTR_ENTITY_ID: ENTITY_ID,
            ATTR_HVAC_MODE: HVACMode.COOL,
            ATTR_TEMPERATURE: 25,
        },
        blocking=True,
    )

    calls = mock_client_factory.set_multiple.await_args_list
    assert len(calls) == 2
    assert calls[0].args[0] == {1: True, 4: "Cool"}
    assert calls[1].args[0] == {2: 25}
    assert climate_mod.MODE_TRANSITION_SETTLE in recorded_sleeps


# ---------------------------------------------------------------------------
# hvac_action — what HA uses to colorize the climate icon per operation state
# ---------------------------------------------------------------------------


async def test_hvac_action_off_when_power_off(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    from homeassistant.components.climate import HVACAction

    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": False, "4": "Heat", "2": 27, "3": 26})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state.attributes["hvac_action"] == HVACAction.OFF


async def test_hvac_action_heating_when_under_target(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Minimal-firmware (no DP 108): infer heating from temp_current<target."""
    from homeassistant.components.climate import HVACAction

    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "2": 30, "3": 26, "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state.attributes["hvac_action"] == HVACAction.HEATING


async def test_hvac_action_idle_when_at_target(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Heat mode, target reached → IDLE so the icon goes greyish, not orange."""
    from homeassistant.components.climate import HVACAction

    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "2": 27, "3": 27, "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state.attributes["hvac_action"] == HVACAction.IDLE


async def test_hvac_action_cooling_when_over_target(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    from homeassistant.components.climate import HVACAction

    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Cool", "2": 22, "3": 25, "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state.attributes["hvac_action"] == HVACAction.COOLING


async def test_hvac_action_uses_compressor_freq_when_available(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Authoritative path: DP 108 actual_frequency > 0 means heating
    regardless of the temp delta (compressor may be spinning even after
    target was met, ramping down)."""
    from homeassistant.components.climate import HVACAction

    coordinator = init_integration.runtime_data
    # current >= target, but compressor still spinning → HEATING
    coordinator.async_set_updated_data(
        DeviceState.from_dps(
            {"1": True, "4": "Heat", "2": 27, "3": 27, "108": 35, "13": 0}
        )
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state.attributes["hvac_action"] == HVACAction.HEATING

    # compressor parked, in Heat mode → IDLE
    coordinator.async_set_updated_data(
        DeviceState.from_dps(
            {"1": True, "4": "Heat", "2": 27, "3": 25, "108": 0, "13": 0}
        )
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state.attributes["hvac_action"] == HVACAction.IDLE


async def test_hvac_action_auto_picks_direction_from_temp(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """In Auto/HEAT_COOL the temp delta picks the action direction."""
    from homeassistant.components.climate import HVACAction

    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Auto", "2": 27, "3": 25, "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state.attributes["hvac_action"] == HVACAction.HEATING

    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Auto", "2": 25, "3": 27, "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state.attributes["hvac_action"] == HVACAction.COOLING


async def test_set_hvac_off_does_not_sleep(
    hass: HomeAssistant, mock_client_factory, init_integration, monkeypatch
) -> None:
    """async_set_hvac_mode(OFF) doesn't trigger the per-mode-memory restore
    so no settle wait is needed — keep the call snappy."""
    import custom_components.poolex_silverline.climate as climate_mod

    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(climate_mod.asyncio, "sleep", fake_sleep)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.OFF},
        blocking=True,
    )
    assert climate_mod.MODE_TRANSITION_SETTLE not in recorded


# ---------------------------------------------------------------------------
# Coverage fill-ins: low-cost branches that the existing tests didn't reach.
# ---------------------------------------------------------------------------


async def test_async_turn_on_off_dispatch(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """turn_on/turn_off thin wrappers route through set_hvac_mode."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "turn_off",
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({1: False})

    mock_client_factory.set_multiple.reset_mock()
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "turn_on",
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )
    # The device still reports DP 4 (mode "Heat") while off, so turn_on
    # restores power only and lets the unit resume its own mode — it must NOT
    # bundle a DP-4 write, which issue #7 firmware rejects with a power revert.
    mock_client_factory.set_multiple.assert_awaited_with({1: True})


async def test_async_turn_on_falls_back_to_mode_when_dp4_absent(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """When the firmware drops DP 4 while off (no mode to resume), turn_on
    falls back to writing power + the remembered direction so the unit still
    starts in a defined mode."""
    coordinator = init_integration.runtime_data
    # Simulate firmware that reports no mode (DP 4 absent) while off.
    coordinator.async_set_updated_data(DeviceState.from_dps({"1": False, "3": 22}))

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "turn_on",
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )
    # _last_direction starts at HVACMode.HEAT, so the fallback restores Heat.
    mock_client_factory.set_multiple.assert_awaited_with({1: True, 4: "Heat"})


async def test_write_surfaces_cannot_connect_as_homeassistant_error(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """A failed write must surface as HomeAssistantError so HA's
    service-call layer reports it cleanly rather than 500ing."""
    from homeassistant.exceptions import HomeAssistantError
    from pysilverline import CannotConnect

    mock_client_factory.set_multiple.side_effect = CannotConnect("network down")
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 27},
            blocking=True,
        )
    assert exc.value.translation_key == "set_failed"


async def test_write_surfaces_invalid_auth_as_homeassistant_error(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """A write rejected by the device for invalid auth surfaces as
    HomeAssistantError with the auth_failed translation."""
    from homeassistant.exceptions import HomeAssistantError
    from pysilverline import InvalidAuth

    mock_client_factory.set_multiple.side_effect = InvalidAuth("key rotated")
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 27},
            blocking=True,
        )
    assert exc.value.translation_key == "auth_failed"


async def test_restore_state_recovers_last_direction_and_preset(
    hass: HomeAssistant, mock_client_factory, config_entry
) -> None:
    """When HA reloads with the device powered off AND the firmware drops
    DP 4 while off (so the device can't tell us the last mode),
    async_added_to_hass restores _last_direction and _last_preset from the
    previous state attributes so a subsequent turn_on starts in the right
    preset+direction. (When the firmware retains DP 4 while off, the device's
    own mode is authoritative and turn_on is power-only — see
    test_async_turn_on_off_dispatch.)
    """
    from unittest.mock import AsyncMock
    from pytest_homeassistant_custom_component.common import mock_restore_cache
    from homeassistant.core import State

    mock_restore_cache(
        hass,
        [
            State(
                ENTITY_ID,
                HVACMode.OFF,
                attributes={
                    "last_direction": HVACMode.COOL.value,
                    "last_preset": "boost",
                },
            )
        ],
    )
    # Firmware that does not report DP 4 while off → mode is None → turn_on
    # falls back to the restored direction/preset.
    state_off_no_mode = DeviceState.from_dps({"1": False, "3": 22, "13": 0})
    mock_client_factory.get_status = AsyncMock(return_value=state_off_no_mode)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client_factory.set_multiple.reset_mock()
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "turn_on",
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({1: True, 4: "BoostCool"})


async def test_unknown_dp4_string_maps_to_hvac_none(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """If the device's DP 4 emits a string we don't recognize, hvac_mode
    returns None rather than crashing — the entity surfaces as 'unknown'."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "TotallyMadeUpMode", "3": 25})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "unknown"


async def test_unknown_dp4_string_maps_to_hvac_action_none(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """When hvac_mode is None due to an unrecognised DP 4 string,
    hvac_action must also be None so the icon doesn't render as 'Idle'
    against an 'unknown' state."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "TotallyMadeUpMode", "3": 25})
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes.get("hvac_action") is None


async def test_pc_inv_120_divisor_preserved_after_mode_write(
    hass: HomeAssistant,
    mock_client_factory,
    init_integration,
) -> None:
    """Regression: optimistic merge in _write_dps must use the client's dp_layout.

    PC-INV-120V2 reports DP 3 in tenths of a degree (raw 277 = 27.7 °C).
    Before the fix, entity.py called DeviceState.merge() with the default
    LAYOUT_STANDARD (divisor=1), so after any write temp_current jumped to
    277 until the next 30-second poll restored it.

    Note: _attr_precision=PRECISION_WHOLE rounds 27.7→28 in HA state attributes,
    so we check coordinator.data.temp_current directly (the unrounded source of
    truth) rather than the displayed attribute.
    """
    from pysilverline.layouts import LAYOUT_PC_INV_120

    # Wire the client to use the ÷10 layout (as __init__.py does for real entries)
    mock_client_factory.dp_layout = LAYOUT_PC_INV_120

    # Inject a state where DP 3 raw = 277 → 27.7 °C with the ÷10 layout
    coordinator = init_integration.runtime_data
    initial_state = coordinator.data.merge(
        {"3": 277},
        layout=LAYOUT_PC_INV_120,
    )
    coordinator.async_set_updated_data(initial_state)
    await hass.async_block_till_done()

    # Sanity: coordinator holds the scaled value before any write
    assert coordinator.data.temp_current == pytest.approx(27.7)

    # Trigger a mode change write — this is the scenario that previously corrupted
    # temp_current by merging with LAYOUT_STANDARD (divisor=1), producing 277.
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.HEAT},
        blocking=True,
    )
    await hass.async_block_till_done()

    # After the write, coordinator.data.temp_current must still be 27.7, not 277.
    assert coordinator.data.temp_current == pytest.approx(27.7)


# ---------------------------------------------------------------------------
# Setpoint clamping on mode switch
# ---------------------------------------------------------------------------


async def test_mode_switch_clamps_setpoint_above_cool_max(
    hass: HomeAssistant, mock_client_factory, init_integration, monkeypatch
) -> None:
    """Switching from Heat (35 °C) to Cool must write 28 °C (Cool max) because
    the carried-over setpoint exceeds the Cool upper bound."""
    import custom_components.poolex_silverline.climate as climate_mod
    from unittest.mock import AsyncMock

    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "2": 35, "3": 30})
    )
    await hass.async_block_till_done()
    mock_client_factory.set_multiple.reset_mock()

    monkeypatch.setattr(climate_mod.asyncio, "sleep", AsyncMock())

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )

    calls = mock_client_factory.set_multiple.await_args_list
    assert len(calls) == 2
    assert calls[0].args[0] == {1: True, 4: "Cool"}
    assert calls[1].args[0] == {2: 28}  # clamped to Cool max


async def test_mode_switch_clamps_setpoint_below_heat_min(
    hass: HomeAssistant, mock_client_factory, init_integration, monkeypatch
) -> None:
    """Switching from Cool (8 °C) to Heat must write 15 °C (Heat min)."""
    import custom_components.poolex_silverline.climate as climate_mod
    from unittest.mock import AsyncMock

    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Cool", "2": 8, "3": 10})
    )
    await hass.async_block_till_done()
    mock_client_factory.set_multiple.reset_mock()

    monkeypatch.setattr(climate_mod.asyncio, "sleep", AsyncMock())

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.HEAT},
        blocking=True,
    )

    calls = mock_client_factory.set_multiple.await_args_list
    assert len(calls) == 2
    assert calls[0].args[0] == {1: True, 4: "Heat"}
    assert calls[1].args[0] == {2: 15}  # clamped to Heat min


async def test_mode_switch_no_clamp_when_temp_in_new_range(
    hass: HomeAssistant, mock_client_factory, init_integration, monkeypatch
) -> None:
    """Switching from Heat (25 °C) to Cool must NOT write a second setpoint
    because 25 is within Cool range (8-28)."""
    import custom_components.poolex_silverline.climate as climate_mod
    from unittest.mock import AsyncMock

    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "2": 25, "3": 24})
    )
    await hass.async_block_till_done()
    mock_client_factory.set_multiple.reset_mock()

    monkeypatch.setattr(climate_mod.asyncio, "sleep", AsyncMock())

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )

    calls = mock_client_factory.set_multiple.await_args_list
    assert len(calls) == 1  # only mode write, no clamp
    assert calls[0].args[0] == {1: True, 4: "Cool"}


# ---------------------------------------------------------------------------
# Model-specific DP-4 vocabulary: coordinator.profile must reach the wire
# ---------------------------------------------------------------------------


async def test_pc_inv_120_model_threads_profile_to_dp4_writes(
    hass: HomeAssistant, mock_client_factory, monkeypatch
) -> None:
    """End-to-end: with CONF_MODEL set to PC-INV-120, the model's DP-4
    vocabulary ('h_powerful', lowercase 'auto') must reach the wire — not
    the global strings ('BoostHeat'/'Auto').

    Every other climate test runs with CONF_MODEL unset (profile is None),
    so they'd stay green even if a regression passed None instead of
    self.coordinator.profile into resolve_*(). This locks the threading down:
    resolve_heat_map(None) yields 'BoostHeat' and resolve_auto_dp(None) yields
    'Auto', so asserting the lowercase PC-INV-120 strings fails under exactly
    that regression (the device silently ignores unknown strings otherwise).
    """
    from unittest.mock import AsyncMock

    from homeassistant.const import CONF_HOST, CONF_PORT
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    import custom_components.poolex_silverline.climate as climate_mod
    from custom_components.poolex_silverline.const import (
        CONF_DEVICE_ID,
        CONF_LOCAL_KEY,
        CONF_MODEL,
        DEFAULT_PORT,
        DOMAIN,
    )
    from pysilverline.devices import MODEL_PC_INV_120

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="bf12345678abcdefghijkl",
        data={
            CONF_HOST: "10.0.0.50",
            CONF_PORT: DEFAULT_PORT,
            CONF_DEVICE_ID: "bf12345678abcdefghijkl",
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_MODEL: MODEL_PC_INV_120,
        },
        version=1,
        minor_version=3,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    monkeypatch.setattr(climate_mod.asyncio, "sleep", AsyncMock())

    coordinator = entry.runtime_data
    # PC-INV-120 firmware reports the lowercase 'heat' enum on DP 4.
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "heat", "2": 28, "3": 26})
    )
    await hass.async_block_till_done()

    # Preset boost while heating → PC-INV-120 'h_powerful', not 'BoostHeat'.
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_PRESET_MODE: "boost"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: "h_powerful"})

    # HEAT_COOL → lowercase 'auto', not 'Auto'. Setpoint 28 stays inside the
    # auto range (8-40), so no clamp write trails the mode write.
    mock_client_factory.set_multiple.reset_mock()
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.HEAT_COOL},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({1: True, 4: "auto"})
