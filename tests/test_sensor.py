"""Sensor tests — value_fn results, fault decoding, availability."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pysilverline import DeviceState
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from syrupy.assertion import SnapshotAssertion


async def test_diagnostic_sensors_populate(
    hass: HomeAssistant, init_integration
) -> None:
    state = hass.states.get("sensor.pool_heatpump_water_inlet_temperature")
    assert state is not None
    assert state.state == "26"

    state = hass.states.get("sensor.pool_heatpump_water_outlet_temperature")
    assert state is not None
    assert state.state == "28"

    state = hass.states.get("sensor.pool_heatpump_compressor_actual_frequency")
    assert state is not None
    assert state.state == "63"


async def test_fault_code_zero_mask_is_unavailable(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Zero mask returns None → the entity flips to `unavailable` (the
    SENSOR availability rule pins on a non-None value_fn). Owners care
    about *which* bit is set; "no fault" is the absence of a state."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"13": 0}))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pool_heatpump_fault_code").state == STATE_UNAVAILABLE


async def test_fault_code_single_bit(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Bit 0 → "water_flow" (symbolic short name from FAULT_BIT_NAMES)."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"13": 1}))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pool_heatpump_fault_code").state == "water_flow"


async def test_fault_code_multi_bit_joined(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Multiple bits collapse into a single comma-joined state in bit order."""
    coordinator = init_integration.runtime_data
    # bits 0 (water_flow) and 3 (low_pressure)
    coordinator.async_set_updated_data(DeviceState.from_dps({"13": 0b1001}))
    await hass.async_block_till_done()
    assert (
        hass.states.get("sensor.pool_heatpump_fault_code").state
        == "water_flow, low_pressure"
    )


async def test_fault_code_unknown_bit_surfaces_as_bit_n(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """A set bit with no symbolic name in FAULT_BIT_NAMES still surfaces
    so a new firmware variant doesn't silently swallow faults."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(DeviceState.from_dps({"13": 1 << 12}))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pool_heatpump_fault_code").state == "bit12"


async def test_sensor_unavailable_when_dp_missing(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """If the firmware doesn't expose DPs 101–110, those sensors must
    surface as unavailable rather than blowing up the integration."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 25, "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get("sensor.pool_heatpump_water_inlet_temperature")
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


async def test_temperature_delta_positive(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """target > current → positive delta."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 30, "3": 28, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get("sensor.pool_heatpump_temperature_delta")
    assert state is not None
    assert state.state == "2"


async def test_temperature_delta_negative(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """target < current → negative delta."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 24, "3": 28, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get("sensor.pool_heatpump_temperature_delta")
    assert state is not None
    assert state.state == "-4"


async def test_temperature_delta_zero(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """target == current → 0 delta."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 27, "3": 27, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get("sensor.pool_heatpump_temperature_delta")
    assert state is not None
    assert state.state == "0"


async def test_temperature_delta_has_no_temperature_device_class(
    hass: HomeAssistant, init_integration
) -> None:
    """HA applies the absolute-temperature conversion F = C*9/5 + 32 to
    every sensor with device_class=TEMPERATURE. For a *delta*, that
    offset is wrong (5 °C delta should be 9 °F delta, not 41 °F), and
    HA has no TEMPERATURE_DELTA class today. So the delta sensor must
    not carry the temperature device_class — verify here so a future
    edit cannot quietly reintroduce the imperial-units bug."""
    state = hass.states.get("sensor.pool_heatpump_temperature_delta")
    assert state is not None
    assert "device_class" not in state.attributes


async def test_temperature_delta_unavailable_when_dp_missing(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """If DP 2 or DP 3 is missing, the delta sensor reports unavailable."""
    coordinator = init_integration.runtime_data
    # Missing DP 2 (target).
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "3": 28, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get("sensor.pool_heatpump_temperature_delta")
    assert state is not None
    assert state.state == STATE_UNAVAILABLE

    # Missing DP 3 (current).
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "2": 28, "4": "Heat", "13": 0})
    )
    await hass.async_block_till_done()
    state = hass.states.get("sensor.pool_heatpump_temperature_delta")
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


def _heating_state(target: int = 28, current: int = 26) -> DeviceState:
    """Build a DeviceState that compute_hvac_action resolves to HEATING:
    power on, Heat mode, current<target, no DP 108 (so the temp-delta
    fallback path decides — keeps this test independent of frequency)."""
    return DeviceState.from_dps(
        {"1": True, "2": target, "3": current, "4": "Heat", "13": 0}
    )


def _idle_state() -> DeviceState:
    """Same Heat mode but current>=target -> compute_hvac_action == IDLE."""
    return DeviceState.from_dps({"1": True, "2": 26, "3": 28, "4": "Heat", "13": 0})


def _off_state() -> DeviceState:
    return DeviceState.from_dps({"1": False, "4": "Heat", "13": 0})


def _reset_runtime(coordinator, anchor: datetime) -> None:
    """Re-anchor the runtime accumulator to a known instant.

    The init_integration fixture runs a real first refresh which already
    sets _runtime_last_tick to wall-clock now; each runtime test needs
    a hermetic starting point uncoupled from real time.
    """
    coordinator._runtime_today_seconds = 0.0
    coordinator._runtime_last_tick = anchor
    coordinator._runtime_local_date = dt_util.as_local(anchor).date()


async def test_runtime_today_accumulates_while_heating(
    hass: HomeAssistant, init_integration
) -> None:
    """Two ticks 60s apart with hvac_action=HEATING add ~60s to the
    accumulator."""
    coordinator = init_integration.runtime_data
    # Local-midnight-safe baseline: pick a time well away from a day
    # boundary so the reset-on-midnight branch doesn't fire here.
    t0 = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    _reset_runtime(coordinator, t0)

    with patch(
        "custom_components.poolex_silverline.coordinator.dt_util.utcnow",
        return_value=t0 + timedelta(seconds=60),
    ):
        coordinator.async_set_updated_data(_heating_state())
    await hass.async_block_till_done()
    assert coordinator._runtime_today_seconds == 60.0

    # Verify the sensor surface picks up the accumulated value.
    state = hass.states.get("sensor.pool_heatpump_runtime_today")
    assert state is not None
    assert float(state.state) == 60.0


async def test_runtime_today_does_not_grow_when_idle(
    hass: HomeAssistant, init_integration
) -> None:
    """IDLE and OFF must not contribute to the accumulator regardless
    of how much wall time passes between ticks."""
    coordinator = init_integration.runtime_data
    t0 = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    _reset_runtime(coordinator, t0)

    with patch(
        "custom_components.poolex_silverline.coordinator.dt_util.utcnow",
        return_value=t0 + timedelta(seconds=120),
    ):
        coordinator.async_set_updated_data(_idle_state())
    await hass.async_block_till_done()
    assert coordinator._runtime_today_seconds == 0.0

    with patch(
        "custom_components.poolex_silverline.coordinator.dt_util.utcnow",
        return_value=t0 + timedelta(seconds=300),
    ):
        coordinator.async_set_updated_data(_off_state())
    await hass.async_block_till_done()
    assert coordinator._runtime_today_seconds == 0.0


async def test_runtime_today_resets_at_local_midnight(
    hass: HomeAssistant, init_integration
) -> None:
    """When a tick lands on a calendar day different from the previous
    tick's local date, the accumulator resets to 0 — owners want today's
    runtime to mean exactly today, not a 24h rolling window."""
    coordinator = init_integration.runtime_data
    # HA's test config uses US/Pacific (UTC-7 in May), so the local
    # midnight rolling May 22 -> May 23 happens at 07:00 UTC on May 23.
    # 06:00 UTC May 23 and 08:00 UTC May 23 straddle that boundary.
    t_late = datetime(2026, 5, 23, 6, 0, 0, tzinfo=timezone.utc)
    _reset_runtime(coordinator, t_late)

    with patch(
        "custom_components.poolex_silverline.coordinator.dt_util.utcnow",
        return_value=t_late + timedelta(seconds=60),
    ):
        coordinator.async_set_updated_data(_heating_state())
    await hass.async_block_till_done()
    assert coordinator._runtime_today_seconds == 60.0

    # Now cross local midnight by jumping past 07:00 UTC.
    t_next = datetime(2026, 5, 23, 8, 0, 0, tzinfo=timezone.utc)
    with patch(
        "custom_components.poolex_silverline.coordinator.dt_util.utcnow",
        return_value=t_next,
    ):
        coordinator.async_set_updated_data(_heating_state())
    await hass.async_block_till_done()
    # Reset branch: counter zeroed, clock re-anchored on the new day.
    assert coordinator._runtime_today_seconds == 0.0

    # And the new day starts accumulating fresh from the re-anchor.
    with patch(
        "custom_components.poolex_silverline.coordinator.dt_util.utcnow",
        return_value=t_next + timedelta(seconds=45),
    ):
        coordinator.async_set_updated_data(_heating_state())
    await hass.async_block_till_done()
    assert coordinator._runtime_today_seconds == 45.0


async def test_runtime_today_accumulates_on_poll(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Runtime tick must also fire on the periodic poll path.

    The DataUpdateCoordinator base assigns _async_update_data's return
    to self.data directly — it never routes the poll result through
    async_set_updated_data. If the tick ran *only* in that override,
    a device whose firmware emits no spontaneous DP pushes (or whose
    pushes were silently being dropped) would have runtime_today
    pinned at zero forever.
    """
    coordinator = init_integration.runtime_data
    # init_integration's first refresh already anchored _runtime_last_tick;
    # capture that so we can verify the next poll re-anchored it. Can't
    # patch dt_util.utcnow inside the tick logic here — HA's scheduler
    # uses the same symbol for fire-time accounting and the patch would
    # stop async_fire_time_changed from running the next poll at all.
    anchor = coordinator._runtime_last_tick
    assert anchor is not None
    before_polls = mock_client_factory.get_status.await_count

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()

    # Sanity: a poll actually ran (otherwise we'd be testing nothing).
    assert mock_client_factory.get_status.await_count > before_polls
    # The side effect we care about: _tick_runtime fired on the poll
    # path and re-anchored the clock. Without Bug 1's fix, the anchor
    # would be unchanged and runtime_today would never accumulate on
    # devices that only respond to polls.
    assert coordinator._runtime_last_tick is not None
    assert coordinator._runtime_last_tick > anchor
    # And because state_pool_running resolves to HEATING, any positive
    # delta between the two ticks must accumulate. The exact number
    # depends on wall-clock timing in the test environment, but it
    # must be non-zero.
    assert coordinator._runtime_today_seconds > 0.0


async def test_entity_inventory_snapshot(
    hass: HomeAssistant,
    init_integration,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot the entity registry + canonical "powered + heating" states.

    Catches regressions where: an entity is renamed/removed, a default
    state changes shape, a unit/device_class is altered, or an attribute
    appears/disappears unexpectedly. Update with --snapshot-update if the
    change is intentional."""
    registry = er.async_get(hass)
    entries = sorted(
        (
            e
            for e in registry.entities.values()
            if e.config_entry_id == init_integration.entry_id
        ),
        key=lambda e: e.entity_id,
    )
    assert {e.entity_id: registry.async_get(e.entity_id) for e in entries} == snapshot(
        name="entity_registry"
    )
    # Only entities that are actually enabled produce a state; some
    # diagnostic DPs are disabled-by-default.
    states = sorted(
        (s for e in entries if (s := hass.states.get(e.entity_id)) is not None),
        key=lambda s: s.entity_id,
    )
    assert states == snapshot(name="entity_states")


async def test_v34_model_selects_v34_sensor_catalog(hass: HomeAssistant) -> None:
    """A `silverline_v34` entry builds the v3.4 sensor catalog (fan on DP 114,
    pump rpm, coil/valve sensors keyed to the v3.4 numbering) and omits the
    legacy-only sensors. Exercises the layout → DeviceState → catalog wiring."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from homeassistant.const import CONF_HOST, CONF_PORT
    from pysilverline.layouts import LAYOUT_V34_WFZEIYN
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.poolex_silverline.const import (
        CONF_DEVICE_ID,
        CONF_LOCAL_KEY,
        CONF_MODEL,
        DOMAIN,
    )

    device_id = "bf12345678abcdefghijkl"
    v34_dps = {
        "1": True,
        "2": 28,
        "3": 26,
        "4": "Heat",
        "13": 0,
        "101": 28,
        "102": 18,
        "103": 26,
        "105": 12,
        "106": 70,
        "108": 40,
        "109": 240,
        "110": 130,
        "111": 850,
        "114": 600,
        "124": 45,
        "133": -8,
        "132": 3,
        "140": 80,
        "120": 1234,
        "137": 5,
        "142": 50,
    }
    state = DeviceState.from_dps(v34_dps, layout=LAYOUT_V34_WFZEIYN)

    client = MagicMock()
    client.host = "10.0.0.50"
    client.port = 6668
    client.device_id = device_id
    client.connected = True
    client.state = state
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
            CONF_HOST: "10.0.0.50",
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
    sensor_keys = {
        e.unique_id.removeprefix(f"{device_id}_")
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "sensor"
    }
    # v3.4-specific sensors are present (gated on the v3.4 DP numbers).
    assert {
        "fan_speed",
        "water_pump_rpm",
        "outdoor_coil_temperature",
        "indoor_coil_temperature",
        "aux_valve_opening",
        "main_valve_opening",
        "outlet_temperature",
    } <= sensor_keys
    # Legacy-only sensors are absent from the v3.4 catalog.
    assert "inlet_temperature" not in sensor_keys
    assert "ambient_temperature" not in sensor_keys
    assert "actual_frequency" not in sensor_keys

    # An enabled v3.4 sensor reads the right value via the layout (DP 105 →
    # outdoor coil temperature == 12).
    outdoor_coil = next(
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.unique_id == f"{device_id}_outdoor_coil_temperature"
    )
    assert hass.states.get(outdoor_coil).state == "12"


async def test_v34_entity_inventory_snapshot(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot the v3.4 (silverline_v34) entity registry + states.

    test_entity_inventory_snapshot only covers the legacy/standard catalog;
    this pins the v3.4 per-firmware catalog byte-for-byte (every key,
    device_class, unit, entity_category and enabled-default), so the
    sensor-description dedup cannot silently alter a v3.4 entity."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from homeassistant.const import CONF_HOST, CONF_PORT
    from pysilverline.layouts import LAYOUT_V34_WFZEIYN
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.poolex_silverline.const import (
        CONF_DEVICE_ID,
        CONF_LOCAL_KEY,
        CONF_MODEL,
        DOMAIN,
    )

    device_id = "bf12345678abcdefghijkl"
    v34_dps = {
        "1": True,
        "2": 28,
        "3": 26,
        "4": "Heat",
        "13": 0,
        "101": 28,
        "102": 18,
        "103": 26,
        "105": 12,
        "106": 70,
        "108": 40,
        "109": 240,
        "110": 130,
        "111": 850,
        "114": 600,
        "124": 45,
        "133": -8,
        "132": 3,
        "140": 80,
        "120": 1234,
        "137": 5,
        "142": 50,
    }
    state = DeviceState.from_dps(v34_dps, layout=LAYOUT_V34_WFZEIYN)

    client = MagicMock()
    client.host = "10.0.0.50"
    client.port = 6668
    client.device_id = device_id
    client.connected = True
    client.state = state
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
            CONF_HOST: "10.0.0.50",
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
    entries = sorted(
        (e for e in registry.entities.values() if e.config_entry_id == entry.entry_id),
        key=lambda e: e.entity_id,
    )
    assert {e.entity_id: registry.async_get(e.entity_id) for e in entries} == snapshot(
        name="v34_entity_registry"
    )
    states = sorted(
        (s for e in entries if (s := hass.states.get(e.entity_id)) is not None),
        key=lambda s: s.entity_id,
    )
    assert states == snapshot(name="v34_entity_states")
