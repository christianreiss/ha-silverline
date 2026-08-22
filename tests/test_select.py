"""Standalone select entities: preset_mode and operating_mode."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.components.select import (
    ATTR_OPTION,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from pysilverline import DeviceState

PRESET_ENTITY = "select.pool_heatpump_preset"
OPMODE_ENTITY = "select.pool_heatpump_operating_mode"


async def test_select_entities_register_when_dps_present(
    hass: HomeAssistant, init_integration
) -> None:
    """Both selects show up in the registry on the full-firmware fixture
    (DPs 1 + 4 are present in state_pool_running)."""
    registry = er.async_get(hass)
    entity_ids = {
        e.entity_id
        for e in registry.entities.values()
        if e.config_entry_id == init_integration.entry_id and e.domain == "select"
    }
    assert entity_ids == {PRESET_ENTITY, OPMODE_ENTITY}


# ---------------------------------------------------------------------------
# operating_mode: current_option mirrors hvac_mode across every combination
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dps,expected",
    [
        ({"1": False, "4": "Heat"}, "off"),
        ({"1": True, "4": "Heat"}, "heat"),
        ({"1": True, "4": "BoostHeat"}, "heat"),
        ({"1": True, "4": "SilentHeat"}, "heat"),
        ({"1": True, "4": "Cool"}, "cool"),
        ({"1": True, "4": "BoostCool"}, "cool"),
        ({"1": True, "4": "SilentCool"}, "cool"),
        ({"1": True, "4": "Auto"}, "heat_cool"),
    ],
)
async def test_operating_mode_current_option(
    hass: HomeAssistant,
    mock_client_factory,
    init_integration,
    dps: dict[str, str | bool],
    expected: str,
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"3": 25, **dps}))
    await hass.async_block_till_done()
    state = hass.states.get(OPMODE_ENTITY)
    assert state is not None
    assert state.state == expected


# ---------------------------------------------------------------------------
# preset_mode: current_option follows DP-4 prefix; "none" when Auto/OFF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dps,expected",
    [
        ({"1": False, "4": "Heat"}, "none"),
        ({"1": True, "4": "Heat"}, "none"),
        ({"1": True, "4": "BoostHeat"}, "boost"),
        ({"1": True, "4": "SilentHeat"}, "eco"),
        ({"1": True, "4": "Cool"}, "none"),
        ({"1": True, "4": "BoostCool"}, "boost"),
        ({"1": True, "4": "SilentCool"}, "eco"),
        ({"1": True, "4": "Auto"}, "none"),
    ],
)
async def test_preset_mode_current_option(
    hass: HomeAssistant,
    mock_client_factory,
    init_integration,
    dps: dict[str, str | bool],
    expected: str,
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"3": 25, **dps}))
    await hass.async_block_till_done()
    state = hass.states.get(PRESET_ENTITY)
    assert state is not None
    assert state.state == expected


# ---------------------------------------------------------------------------
# operating_mode: async_select_option writes the right DPs
# ---------------------------------------------------------------------------


async def test_operating_mode_select_off_writes_dp1_false(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "off"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({1: False})


async def test_operating_mode_select_heat_writes_mode_and_sleeps(
    hass: HomeAssistant, mock_client_factory, init_integration, monkeypatch
) -> None:
    """async_select_option('heat') writes {4:'Heat'} and then awaits the
    _MODE_TRANSITION_SETTLE sleep so a chained call doesn't race the
    device's per-mode-memory restore push.

    The unit is already running here, so no DP-1 write goes out at all —
    the Tuya app doesn't send one either, and a redundant power write is one
    more chance for the firmware to take exception (issue #19)."""
    import custom_components.poolex_silverline.select as select_mod

    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(select_mod.asyncio, "sleep", fake_sleep)

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "heat"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: "Heat"})
    assert select_mod.MODE_TRANSITION_SETTLE in recorded


async def test_operating_mode_select_cool_writes_cool(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "cool"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: "Cool"})


async def test_operating_mode_select_heat_cool_writes_auto(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "heat_cool"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: "Auto"})


async def test_operating_mode_select_off_does_not_sleep(
    hass: HomeAssistant, mock_client_factory, init_integration, monkeypatch
) -> None:
    """The OFF path doesn't trigger the device's per-mode restore, so
    skip the settle sleep — keep the call snappy."""
    import custom_components.poolex_silverline.select as select_mod

    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(select_mod.asyncio, "sleep", fake_sleep)

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "off"},
        blocking=True,
    )
    assert select_mod.MODE_TRANSITION_SETTLE not in recorded


# ---------------------------------------------------------------------------
# preset_mode: async_select_option respects the current direction
# ---------------------------------------------------------------------------


async def test_preset_boost_during_heat_writes_boostheat(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """While the device is in Heat, picking 'boost' writes the
    BoostHeat enum on DP 4 — exact same DP write as the climate entity."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"1": True, "4": "Heat"}))
    await hass.async_block_till_done()

    mock_client_factory.set_multiple.reset_mock()
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: PRESET_ENTITY, ATTR_OPTION: "boost"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: "BoostHeat"})


async def test_preset_eco_during_cool_writes_silentcool(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"1": True, "4": "Cool"}))
    await hass.async_block_till_done()

    mock_client_factory.set_multiple.reset_mock()
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: PRESET_ENTITY, ATTR_OPTION: "eco"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: "SilentCool"})


async def test_preset_during_auto_raises(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """In Auto, both presets are device-meaningless; the select rejects
    so the UI surfaces a clear error rather than swallowing the click."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"1": True, "4": "Auto"}))
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as exc:
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: PRESET_ENTITY, ATTR_OPTION: "boost"},
            blocking=True,
        )
    assert exc.value.translation_key == "preset_not_available_in_auto"


async def test_preset_while_off_is_noop(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Picking a preset while OFF stores no pending state on the select
    (the climate entity owns OFF→ON memory). The select just no-ops."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"1": False, "4": "Heat"}))
    await hass.async_block_till_done()
    mock_client_factory.set_multiple.reset_mock()

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: PRESET_ENTITY, ATTR_OPTION: "boost"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_not_called()


# ---------------------------------------------------------------------------
# _write error handling: InvalidAuth and CannotConnect translation
# ---------------------------------------------------------------------------


async def test_operating_mode_write_invalid_auth_surfaces(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """A rejected write on the operating_mode select surfaces as a
    translated HomeAssistantError with the auth_failed key."""
    from homeassistant.exceptions import HomeAssistantError

    from pysilverline import InvalidAuth

    mock_client_factory.set_multiple.side_effect = InvalidAuth("rotated")
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "cool"},
            blocking=True,
        )
    assert exc.value.translation_key == "auth_failed"


async def test_operating_mode_write_cannot_connect_surfaces(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """A network drop during an operating_mode write surfaces as
    HomeAssistantError with the set_failed translation."""
    from homeassistant.exceptions import HomeAssistantError

    from pysilverline import CannotConnect

    mock_client_factory.set_multiple.side_effect = CannotConnect("offline")
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "heat"},
            blocking=True,
        )
    assert exc.value.translation_key == "set_failed"


# ---------------------------------------------------------------------------
# Defensive raises that HA's service layer normally filters out before
# the entity sees them — exercised by direct method calls so the entity's
# own invariants (not just HA's framework) are covered.
# ---------------------------------------------------------------------------


def _preset_entity(hass: HomeAssistant):
    component = hass.data["select"]
    return next(e for e in component.entities if e.entity_id == PRESET_ENTITY)


def _opmode_entity(hass: HomeAssistant):
    component = hass.data["select"]
    return next(e for e in component.entities if e.entity_id == OPMODE_ENTITY)


async def test_preset_select_rejects_unknown_option_directly(
    hass: HomeAssistant, init_integration
) -> None:
    """HA's framework normally rejects options not in `_attr_options`,
    but the entity must also enforce this on its own — a programmatic
    or buggy framework path must not silently send junk to the device."""
    entity = _preset_entity(hass)
    with pytest.raises(ServiceValidationError):
        await entity.async_select_option("bogus")


async def test_preset_select_unknown_dp4_string_is_noop(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """When DP 4 carries a string the integration doesn't recognise as
    Heat-prefix or Cool-prefix (and isn't "Auto"), picking a preset must
    fall through silently rather than guess a direction — guessing could
    flip the unit between heating and cooling unexpectedly."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Mystery"})
    )
    await hass.async_block_till_done()
    mock_client_factory.set_multiple.reset_mock()
    entity = _preset_entity(hass)
    await entity.async_select_option("boost")
    mock_client_factory.set_multiple.assert_not_called()


async def test_operating_mode_select_rejects_unknown_option_directly(
    hass: HomeAssistant, init_integration
) -> None:
    """Direct call path: the operating_mode select must raise on a
    value not in OPMODE_OPTIONS rather than fall through to _write."""
    entity = _opmode_entity(hass)
    with pytest.raises(ServiceValidationError):
        await entity.async_select_option("invalid")


# ---------------------------------------------------------------------------
# Model-specific DP-4 vocabulary: coordinator.profile must reach the wire
# ---------------------------------------------------------------------------


async def test_pc_inv_120_model_threads_profile_to_dp4_writes(
    hass: HomeAssistant, mock_client_factory, monkeypatch
) -> None:
    """End-to-end mirror of the climate guard: with CONF_MODEL set to
    PC-INV-120, both selects must write the model's DP-4 vocabulary
    ('h_powerful', lowercase 'auto') instead of the global strings.

    The other select tests run with CONF_MODEL unset (profile is None), so a
    regression passing None instead of self.coordinator.profile into
    resolve_*() would leave them green while PC-INV-120 users got 'BoostHeat'/
    'Auto' — which the device silently ignores. This test fails under exactly
    that regression.
    """
    from unittest.mock import AsyncMock

    from homeassistant.components.select import (
        ATTR_OPTION,
        SERVICE_SELECT_OPTION,
    )
    from homeassistant.components.select import (
        DOMAIN as SELECT_DOMAIN,
    )
    from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, CONF_PORT
    from pysilverline.devices import MODEL_PC_INV_120
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    import custom_components.poolex_silverline.select as select_mod
    from custom_components.poolex_silverline.const import (
        CONF_DEVICE_ID,
        CONF_LOCAL_KEY,
        CONF_MODEL,
        DEFAULT_PORT,
        DOMAIN,
    )

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

    monkeypatch.setattr(select_mod.asyncio, "sleep", AsyncMock())

    coordinator = entry.runtime_data
    # PC-INV-120 firmware reports the lowercase 'heat' enum on DP 4.
    coordinator.async_set_updated_data(DeviceState.from_dps({"1": True, "4": "heat"}))
    await hass.async_block_till_done()

    # Preset boost while heating → PC-INV-120 'h_powerful', not 'BoostHeat'.
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: PRESET_ENTITY, ATTR_OPTION: "boost"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: "h_powerful"})

    # Operating mode heat_cool → lowercase 'auto', not 'Auto'.
    mock_client_factory.set_multiple.reset_mock()
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "heat_cool"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: "auto"})


@pytest.mark.parametrize(
    ("wire_mode", "option", "expected"),
    [
        # Silent/boost survive a heat↔cool change, and survive re-selecting
        # the direction the unit is already in.
        ("SilentCool", "cool", "SilentCool"),
        ("SilentCool", "heat", "SilentHeat"),
        ("BoostCool", "heat", "BoostHeat"),
        ("BoostHeat", "cool", "BoostCool"),
        ("SilentHeat", "heat", "SilentHeat"),
        # No preset active → plain direction, unchanged behaviour.
        ("Heat", "cool", "Cool"),
        ("Cool", "heat", "Heat"),
    ],
)
async def test_operating_mode_select_preserves_preset(
    hass: HomeAssistant,
    mock_client_factory,
    init_integration,
    wire_mode: str,
    option: str,
    expected: str,
) -> None:
    """The operating-mode select must carry the active preset across a
    direction change, exactly as climate.async_set_hvac_mode does with its
    remembered _last_preset (issue #19).

    It used to hard-code PRESET_NONE, so a unit running SilentCool was
    silently downgraded to plain Cool the moment anyone touched this
    dropdown — the boost/silent dimension just vanished while the device
    kept running. This entity is deliberately stateless, so the preset is
    read back off the live wire state rather than remembered.
    """
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": wire_mode, "3": 26, "13": 0})
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: option},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: expected})


async def test_operating_mode_select_heat_cool_drops_preset(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Auto is the one direction with no preset dimension — the device
    treats presets as meaningless there (derive_preset collapses Auto to
    none), so selecting heat_cool from SilentCool writes a bare Auto rather
    than trying to carry Silent into a mode that has no such string."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "SilentCool", "3": 26, "13": 0})
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "heat_cool"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({4: "Auto"})


async def test_operating_mode_select_from_off_writes_power_then_mode(
    hass: HomeAssistant, mock_client_factory, init_integration, monkeypatch
) -> None:
    """Issue #19's reproduction, step for step.

    The reporter set the operating mode to "stopped", then to "cooling", and
    the unit never came back on: HA showed ON, the vendor app stayed OFF, and
    a few minutes later the unit reverted to off on its own. That is the same
    signature climate.async_turn_on has carried a workaround for since issue
    #7 — Full Inverter firmware acknowledges a bundled {1: True, 4: mode}
    frame and then powers itself back down. This dropdown was still bundling.

    Power and mode must go out as two frames, power first, and the two must
    never share a frame.
    """
    from unittest.mock import AsyncMock

    import custom_components.poolex_silverline.entity as entity_mod

    monkeypatch.setattr(entity_mod.asyncio, "sleep", AsyncMock())
    coordinator = init_integration.runtime_data

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "off"},
        blocking=True,
    )
    mock_client_factory.set_multiple.assert_awaited_with({1: False})

    # The device confirms the power-off, exactly as the reporter's did.
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": False, "4": "BoostHeat", "3": 26, "13": 0})
    )
    await hass.async_block_till_done()
    mock_client_factory.set_multiple.reset_mock()

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: OPMODE_ENTITY, ATTR_OPTION: "cool"},
        blocking=True,
    )
    # Plain "Cool", not "BoostCool": derive_preset collapses to none while the
    # unit is off, by design — a preset is device-meaningless there, and this
    # entity is stateless. Carrying a preset across an OFF→ON transition is
    # the climate entity's job, via its remembered _last_preset.
    calls = [c.args[0] for c in mock_client_factory.set_multiple.await_args_list]
    assert calls == [{1: True}, {4: "Cool"}], (
        "power and mode must be separate frames, power first"
    )
    assert not any(1 in dps and 4 in dps for dps in calls), (
        "a bundled power+mode frame is what the firmware reverts on"
    )
