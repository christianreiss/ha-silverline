"""Coordinator -> issue_registry: fault bits surface as auto-clearing
Repair issues. Covers the Gold rule `repair-issues`."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pysilverline import DeviceState

from custom_components.poolex_silverline.const import DOMAIN


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
    """DP 13 = 1 (bit 0 = E03) creates an ERROR-severity issue."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
    )
    await hass.async_block_till_done()
    issue = _issue(hass, "fault_E03")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_key == "fault_E03"
    assert issue.is_fixable is False


async def test_fault_clearing_deletes_issue(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """When the device clears DP 13, the issue is auto-removed."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
    )
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E03") is not None

    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0})
    )
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E03") is None


async def test_multiple_simultaneous_faults(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """DP 13 = 0b101 (bits 0 and 2) creates two issues independently."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0b101})
    )
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E03") is not None  # bit 0
    assert _issue(hass, "fault_E05") is not None  # bit 2
    assert _issue(hass, "fault_E04") is None  # bit 1 not set


async def test_partial_clear_keeps_remaining_issue(
    hass: HomeAssistant, mock_client_factory, init_integration
) -> None:
    """If two bits are active and one clears, only that bit's issue
    disappears. The other stays."""
    coordinator = init_integration.runtime_data
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0b101})
    )
    await hass.async_block_till_done()
    # Clear bit 0 (E03) but keep bit 2 (E05).
    coordinator.async_set_updated_data(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 0b100})
    )
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E03") is None
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
    coordinator = init_integration.runtime_data
    # The mock's push listeners list is in mock_client_factory.listeners;
    # the coordinator registered itself in async_setup. Invoke directly.
    push_listener = mock_client_factory.listeners[0]
    push_listener(
        DeviceState.from_dps({"1": True, "4": "Heat", "3": 26, "13": 1})
    )
    await hass.async_block_till_done()
    assert _issue(hass, "fault_E03") is not None
