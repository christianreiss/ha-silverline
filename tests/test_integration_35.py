"""Home Assistant setup against a fake Tuya v3.5 device."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.poolex_silverline.const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MODEL,
    CONF_PROTOCOL_VERSION,
    DOMAIN,
)

from .fake_tuya35 import DEVICE_ID, KEY, FakeTuya35Server


async def test_setup_entry_against_fake_v35_device(
    hass: HomeAssistant, socket_enabled: None
) -> None:
    async with FakeTuya35Server(
        dps={"1": True, "2": 28, "3": 26, "4": "Heat", "13": 0}
    ) as server:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Pool Heatpump (fake v3.5)",
            unique_id=DEVICE_ID,
            data={
                CONF_HOST: "127.0.0.1",
                CONF_PORT: server.port,
                CONF_DEVICE_ID: DEVICE_ID,
                CONF_LOCAL_KEY: KEY,
                CONF_PROTOCOL_VERSION: "3.5",
                CONF_MODEL: "other",
            },
            version=1,
            minor_version=3,
        )
        entry.add_to_hass(hass)

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.client.detected_version == "3.5"
        assert server.finish_decoded_with_real_key is True
        assert server.finish_hmac_ok is True
        assert server.queries >= 1

        registry = er.async_get(hass)
        entity_ids = {
            entity.entity_id
            for entity in registry.entities.values()
            if entity.config_entry_id == entry.entry_id
        }
        assert "climate.pool_heatpump" in entity_ids
        assert "switch.pool_heatpump_power" in entity_ids
        assert "sensor.pool_heatpump_temperature_delta" in entity_ids

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
