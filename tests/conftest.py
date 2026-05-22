"""Shared fixtures for the Poolex Silverline test suite."""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from pysilverline import DeviceInfo, DeviceState
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import (
    HomeAssistantSnapshotExtension,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.poolex_silverline.const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    DEFAULT_PORT,
    DOMAIN,
)

DEVICE_ID = "bf12345678abcdefghijkl"
LOCAL_KEY = "0123456789abcdef"
HOST = "10.0.0.50"

ENTRY_DATA: dict[str, Any] = {
    CONF_HOST: HOST,
    CONF_PORT: DEFAULT_PORT,
    CONF_DEVICE_ID: DEVICE_ID,
    CONF_LOCAL_KEY: LOCAL_KEY,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: Any,
) -> Generator[None]:
    yield


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """syrupy snapshot fixture with HA's entity-state serializer wired in."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture
def state_pool_running() -> DeviceState:
    """A populated state matching a Silverline FI in heating mode."""
    return DeviceState.from_dps(
        {
            "1": True,
            "2": 28,
            "3": 26,
            "4": "Heat",
            "13": 0,
            "101": 65,
            "102": 12,
            "103": 18,
            "104": 22,
            "105": 26,
            "106": 28,
            "107": 65,
            "108": 63,
            "109": 320,
            "110": 850,
            "111": True,
        }
    )


@pytest.fixture
def state_pool_off() -> DeviceState:
    return DeviceState.from_dps({"1": False, "4": "Heat", "3": 22, "13": 0})


@pytest.fixture
def mock_client(state_pool_running: DeviceState) -> MagicMock:
    """A mock SilverlineClient instance whose calls succeed by default."""
    client = MagicMock()
    client.host = HOST
    client.port = DEFAULT_PORT
    client.device_id = DEVICE_ID
    client.connected = True
    client.state = state_pool_running
    client.connect = AsyncMock(return_value=None)
    client.disconnect = AsyncMock(return_value=None)
    client.get_device_info = AsyncMock(return_value=DeviceInfo(device_id=DEVICE_ID))
    client.get_status = AsyncMock(return_value=state_pool_running)
    client.set_dp = AsyncMock(return_value=None)
    client.set_multiple = AsyncMock(return_value=None)
    listeners: list[Callable[[DeviceState], None]] = []

    def _add_listener(callback: Callable[[DeviceState], None]) -> Callable[[], None]:
        listeners.append(callback)
        return lambda: listeners.remove(callback) if callback in listeners else None

    client.add_listener = MagicMock(side_effect=_add_listener)
    client.listeners = listeners

    connection_listeners: list[Callable[[bool], None]] = []

    def _add_connection_listener(
        callback: Callable[[bool], None],
    ) -> Callable[[], None]:
        connection_listeners.append(callback)
        return (
            lambda: connection_listeners.remove(callback)
            if callback in connection_listeners
            else None
        )

    client.add_connection_listener = MagicMock(side_effect=_add_connection_listener)
    client.connection_listeners = connection_listeners
    return client


@pytest.fixture
def mock_client_factory(mock_client: MagicMock) -> Generator[MagicMock]:
    """Patch SilverlineClient everywhere it is constructed."""
    with (
        patch(
            "custom_components.poolex_silverline.SilverlineClient",
            return_value=mock_client,
        ),
        patch(
            "custom_components.poolex_silverline.config_flow.SilverlineClient",
            return_value=mock_client,
        ),
    ):
        yield mock_client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Pool Heatpump ({HOST})",
        unique_id=DEVICE_ID,
        data=ENTRY_DATA,
        version=1,
        minor_version=1,
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_client_factory: MagicMock,
    config_entry: MockConfigEntry,
) -> MockConfigEntry:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
