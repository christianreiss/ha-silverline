"""Coordinator -> issue_registry: fault bits surface as auto-clearing
Repair issues. Covers the Gold rule `repair-issues`."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.poolex_silverline.const import (
    DOMAIN,
    E03_DEBOUNCE_SECONDS,
)
from pysilverline import DeviceState


def _issue(hass: HomeAssistant, key: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, key)


async def test_no_issues_when_fault_clear(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """state_pool_running has DP 13 = 0 → no Repair issues created."""
    assert _issue(hass, "fault_E03") is None
    assert _issue(hass, "fault_E04") is None


async def test_fault_bit_creates_repair_issue(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """DP 13 = bit 2 (E05 high pressure) creates an ERROR-severity issue
    immediately. Non-bit-0 codes don't go through the E03 debounce."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1 << 2})
    )
    await hass.async_block_till_done()
    issue = _issue(hass, "fault_E05")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_key == "fault_E05"
    assert issue.is_fixable is False


async def test_fault_clearing_deletes_issue(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """When the device clears DP 13, the issue is auto-removed."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1 << 2})
    )
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E05") is not None

    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0})
    )
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E05") is None


async def test_multiple_simultaneous_faults(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """DP 13 = 0b110 (bits 1 and 2) creates two issues independently.
    Picks the non-debounced bits so the test stays synchronous."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0b110})
    )
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E04") is not None  # bit 1
    assert _issue(hass, "fault_E05") is not None  # bit 2
    assert _issue(hass, "fault_E03") is None  # bit 0 not set


async def test_partial_clear_keeps_remaining_issue(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """If two bits are active and one clears, only that bit's issue
    disappears. The other stays."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0b110})
    )
    await hass.async_block_till_done()
    # Clear bit 1 (E04) but keep bit 2 (E05).
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0b100})
    )
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E04") is None
    assert _issue(hass, "fault_E05") is not None


async def test_warning_severity_for_sensor_faults(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """P-series sensor faults are WARNING severity, not ERROR — the unit
    keeps running, just with degraded readings."""
    coordinator = init_integration.runtime_data
    # bit 6 = P3 (inlet sensor fault)
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1 << 6})
    )
    await hass.async_block_till_done()
    issue = _issue(hass, "fault_P3")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING


async def test_repair_issue_fires_on_push(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Fault reconcile runs on push-frame state updates too, not just
    on coordinator polls — important because push is the fast path."""
    # The mock's push listeners list is in mock_client_factory.listeners;
    # the coordinator registered itself in async_setup. Invoke directly.
    push_listener = mock_client_factory.listeners[0]
    push_listener(DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1 << 2}))
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E05") is not None


async def test_repair_issue_fires_on_poll(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Fault reconcile must also run on the periodic poll path.

    The DataUpdateCoordinator base class assigns _async_update_data's
    return value to self.data directly — it never routes the poll
    result through async_set_updated_data. If reconcile lived only in
    that override, a device that boots with a fault bit set would
    surface no Repair issue until the first push frame arrived.
    """
    mock_client_factory.get_status = AsyncMock(
        return_value=DeviceState.from_dps(
            {"1": True, "4": "Heat", "3": 26, "13": 1 << 2}
        )
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E05") is not None


async def test_repair_issue_clears_on_poll(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """The mirror case: a fault that clears while we're only polling
    (no pushes arriving) must drop the open Repair issue, not leave it
    stranded until the next push."""
    # Seed an active issue via the push path (mirrors a real boot with
    # a fault bit set).
    push_listener = mock_client_factory.listeners[0]
    push_listener(DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1 << 2}))
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E05") is not None

    # Now switch the poll path to return a clean state and tick the
    # scheduler. The override path is not exercised — only the poll path.
    mock_client_factory.get_status = AsyncMock(
        return_value=DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0})
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E05") is None


async def test_e03_debounce_no_issue_before_window(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """Bit 0 (E03 water flow) must NOT raise a Repair issue immediately.
    The spec wants the issue only after the bit has been continuously
    set for ``E03_DEBOUNCE_SECONDS`` — startup self-trips of E03 should
    not surface a card."""
    coordinator = init_integration.runtime_data
    base = 1_000_000.0
    with patch("custom_components.poolex_silverline.coordinator.time.monotonic") as m:
        m.return_value = base
        # t=0: bit 0 appears
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is None

        # t=30: still within debounce window, still no issue
        m.return_value = base + 30.0
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is None

        # t=debounce+1: window elapsed → issue is raised
        m.return_value = base + E03_DEBOUNCE_SECONDS + 1.0
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is not None


async def test_e03_debounce_resets_when_bit_clears(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """If E03 toggles off before the debounce elapses, the next
    re-activation restarts the window from zero — the previous
    sighting cannot count toward the new window."""
    coordinator = init_integration.runtime_data
    base = 2_000_000.0
    with patch("custom_components.poolex_silverline.coordinator.time.monotonic") as m:
        # First sighting at t=0
        m.return_value = base
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()

        # Bit clears at t=30 — well within window
        m.return_value = base + 30.0
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is None

        # Bit reappears at t=40; the debounce restarts from here.
        m.return_value = base + 40.0
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is None

        # 40 + 30 = 70s elapsed in absolute time, but only 30s since the
        # restart — still no issue.
        m.return_value = base + 70.0
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is None

        # Once 60s since the restart elapses, the issue surfaces.
        m.return_value = base + 40.0 + E03_DEBOUNCE_SECONDS + 1.0
        coordinator.async_set_updated_data(
            DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
        )
        await hass.async_block_till_done()
        assert _issue(hass, "fault_E03") is not None


async def test_nano_5kw_fault_does_not_create_dp13_repair_issue(
    hass: HomeAssistant,
) -> None:
    """Regression guard: the Nano 5kW family (issue #16) reports its fault
    bitmap on DP 21, not DP 13. Repair-issue reconciliation decodes DP 13
    specifically (FAULT_BIT_CODES / the E03 debounce are DP-13 semantics).
    DP 21 = 256 is bit 8 — if reconcile ran against it unconditionally, bit
    8 would resolve through FAULT_BIT_CODES to "P1" (defrost sensor), a
    wrong Repair card for what is actually a water-flow fault. It must
    raise no Repair issue at all: this model's fault decoding is handled
    by its own sensor/binary_sensor entities, not the DP-13 reconciler."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from homeassistant.const import CONF_HOST, CONF_PORT
    from pysilverline.layouts import LAYOUT_NANO_5KW
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.poolex_silverline.const import (
        CONF_DEVICE_ID,
        CONF_LOCAL_KEY,
        CONF_MODEL,
        DOMAIN,
    )

    device_id = "bf99887766nano5kwrpair"
    state = DeviceState.from_dps(
        {"1": True, "2": 30, "3": 24, "4": "Heat", "21": 256},
        layout=LAYOUT_NANO_5KW,
    )

    client = MagicMock()
    client.host = "10.0.0.63"
    client.port = 6668
    client.device_id = device_id
    client.connected = True
    client.state = state
    client.detected_version = "3.4"
    client.dp_layout = LAYOUT_NANO_5KW
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
            CONF_HOST: "10.0.0.63",
            CONF_PORT: 6668,
            CONF_DEVICE_ID: device_id,
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_MODEL: "nano_5kw",
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

    assert _issue(hass, "fault_P1") is None
    assert _issue(hass, "fault_E03") is None
    all_issues = ir.async_get(hass).issues
    assert not any(
        issue_id[0] == DOMAIN and issue_id[1].startswith("fault_")
        for issue_id in all_issues
    )


async def test_nano_fi_water_flow_raises_e25_not_p1(hass: HomeAssistant) -> None:
    """On Full Inverter firmware DP 13 = 256 is a water-flow fault, so the
    Repair card must be the FI panel's water-flow code — never P1 defrost
    sensor (issue #19).

    The code is E25, not the classic family's E03: the reporter photographed
    his wired controller during the fault. Two families, one DP, different
    bit *and* different printed code — which is why both travel together on
    the layout's fault table.

    Both families report on DP 13, so the reconciler is driven by the
    model's own DpLayout.fault_table rather than by the DP number. This also
    pins the debounce to the bit *named* water_flow: on FI firmware that is
    bit 8, and hard-coding bit 0 would both skip the debounce this fault
    needs (the unit self-trips on startup before the filter pump primes) and
    debounce whatever bit 0 turns out to mean here.
    """
    from pysilverline.const import NANO_FI_FAULT_TABLE
    from pysilverline.layouts import LAYOUT_NANO_FI_3KW

    from custom_components.poolex_silverline._faults import FaultReconciler

    reconciler = FaultReconciler()
    state = DeviceState.from_dps(
        {"1": True, "2": 28, "3": 21, "4": "BoostHeat", "13": 256},
        layout=LAYOUT_NANO_FI_3KW,
    )

    # Inside the debounce window: water flow is held back, nothing raised.
    reconciler.reconcile(hass, state, now=0.0, table=NANO_FI_FAULT_TABLE)
    assert _issue(hass, "fault_E25") is None
    assert _issue(hass, "fault_P1") is None

    # Past it: E25, and neither P1 nor the classic family's E03 may appear.
    reconciler.reconcile(
        hass, state, now=E03_DEBOUNCE_SECONDS + 1, table=NANO_FI_FAULT_TABLE
    )
    issue = _issue(hass, "fault_E25")
    assert issue is not None
    assert issue.translation_key == "fault_E25"
    assert _issue(hass, "fault_P1") is None, "bit 8 is water flow on FI firmware"
    assert _issue(hass, "fault_E03") is None, "E03 is the classic family's code"

    # Flow restored → the card clears itself, same as every other fault.
    cleared = DeviceState.from_dps(
        {"1": True, "2": 28, "3": 21, "4": "BoostHeat", "13": 0},
        layout=LAYOUT_NANO_FI_3KW,
    )
    reconciler.reconcile(
        hass, cleared, now=E03_DEBOUNCE_SECONDS + 2, table=NANO_FI_FAULT_TABLE
    )
    assert _issue(hass, "fault_E25") is None
