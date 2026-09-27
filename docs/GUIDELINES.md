# Home Assistant Integration – Best Practices & Framework Guidelines for Claude Code

> **Purpose of this document:** You (Claude Code) are developing a HACS custom component for a heat pump that communicates over local REST/HTTP calls on the LAN (`iot_class: local_polling`, optionally `local_push`). This document is your binding reference for **how** to integrate — not **what** heat-pump domain logic to implement. The goal is an integration that is both **HACS-compliant** and remains **Core-mergeable** (Quality Scale Bronze → ideally Silver/Gold). Status: HA Core 2026.x, Python 3.13+.
>
> When in doubt: **do NOT guess** — when uncertain, consult the corresponding document under `developers.home-assistant.io` via fetch. That is the canonical source. Secondary sources (blogs, community posts) are at best hints.

---

## 0. Guiding principles (TL;DR of the rules)

1. **Async-first, always.** No blocking I/O in the event loop. If a library is synchronous → `hass.async_add_executor_job(...)`. Better: write a fully async library (Platinum prerequisite).
2. **No custom `aiohttp.ClientSession`.** Always use `async_get_clientsession(hass)` from `homeassistant.helpers.aiohttp_client` when no cookies or special settings are needed. Otherwise `async_create_clientsession(hass, ...)`. Source: `homeassistant/helpers/aiohttp_client.py`.
3. **One API client class, cleanly decoupled from HA.** Ideally as a separate PyPI package (e.g. `pyheatpumpxy`) so the integration is just a thin wrapper (HA idiom: "integration is glue code"). Quality scale rule `dependency-transparency`.
4. **`DataUpdateCoordinator` is mandatory** for any polling. Custom polling loops or `async_track_time_interval` for fetching data are anti-patterns.
5. **Config Flow only.** No YAML setup. ADR-0010 states clearly: for devices/services, UI setup is mandatory. Quality scale rule `config-flow`.
6. **`ConfigEntry.runtime_data`** instead of `hass.data[DOMAIN][entry.entry_id]`. Idiomatic since 2024.5, required for Bronze from 2024.11 on.
7. **`EntityDescription` pattern** for all entities, **not** a property salad.
8. **Strict typing**, `from __future__ import annotations`, no `Any` without justification. Platinum rule `strict-typing`.
9. **No plain-text strings in the UI.** Everything via `strings.json` + `translations/<lang>.json` and `translation_key`.
10. **Tests are not optional.** Bronze requires `config-flow-test-coverage` (100% in the config flow), Silver requires `> 95%` overall coverage.

Quick entry point: <https://developers.home-assistant.io/docs/creating_integration_file_structure/>

---

## 1. Project structure

### 1.1 Directory tree (HACS-compliant and Core-compliant)

```
heatpump_xy/                              # GitHub repo root
├── README.md                             # Required for HACS
├── hacs.json                             # Required at the repo root for HACS
├── LICENSE
├── .github/
│   └── workflows/
│       ├── hassfest.yaml                 # home-assistant/actions/hassfest@master
│       ├── hacs.yaml                     # hacs/action@main
│       └── tests.yaml                    # pytest with pytest-homeassistant-custom-component
├── custom_components/
│   └── heatpump_xy/                      # Domain — must match manifest.json:domain exactly
│       ├── __init__.py                   # async_setup_entry / async_unload_entry
│       ├── manifest.json                 # Required
│       ├── const.py                      # DOMAIN, CONF_*, defaults
│       ├── config_flow.py                # ConfigFlow + OptionsFlow
│       ├── coordinator.py                # DataUpdateCoordinator
│       ├── entity.py                     # Shared base class (CoordinatorEntity)
│       ├── api.py                        # API-client wrapper (or external PyPI package)
│       ├── sensor.py                     # Platform: sensors
│       ├── climate.py                    # Platform: climate entity
│       ├── binary_sensor.py              # if needed
│       ├── number.py / select.py / switch.py
│       ├── diagnostics.py                # Required from Gold
│       ├── repairs.py                    # if repair flows exist
│       ├── services.yaml                 # only when custom services are registered
│       ├── strings.json                  # UI strings (source for en.json)
│       ├── translations/
│       │   ├── en.json
│       │   └── de.json
│       ├── icons.json                    # Icon translations (Gold rule)
│       ├── quality_scale.yaml            # Required as soon as a quality-scale level is targeted
│       └── brand/                        # Possible locally from HA 2026.3 on (see §17.3)
│           ├── icon.png
│           ├── icon@2x.png
│           ├── logo.png
│           └── logo@2x.png
└── tests/                                # not included in the HACS download
    ├── __init__.py
    ├── conftest.py
    ├── test_config_flow.py
    ├── test_coordinator.py
    ├── test_init.py
    ├── test_sensor.py
    ├── fixtures/
    │   └── status_response.json
    └── snapshots/                        # syrupy snapshots
```

**Important:**
- HACS expects the integration under `custom_components/<domain>/`. On a later Core merge, exactly this directory moves to `homeassistant/components/<domain>/` — and the `tests/` become `tests/components/<domain>/`.
- The directory name `heatpump_xy` **must** match the `domain` field in `manifest.json` exactly, otherwise hassfest fails.

References:
- <https://developers.home-assistant.io/docs/creating_integration_file_structure/>
- <https://hacs.xyz/docs/publish/integration/>

### 1.2 Recommended starting point

The official **`integration_blueprint`** repo (<https://github.com/home-assistant/integration_blueprint>) is the canonical template. It contains coordinator, config flow, sensor platform, tests, and HACS compliance — build on top of it rather than starting from scratch.

---

## 2. `manifest.json`

### 2.1 Complete schema (Core source: `homeassistant/loader.py`)

```json
{
  "domain": "heatpump_xy",
  "name": "Heatpump XY",
  "version": "0.1.0",
  "codeowners": ["@christian"],
  "config_flow": true,
  "dependencies": [],
  "after_dependencies": ["zeroconf"],
  "documentation": "https://github.com/christian/ha-heatpump-xy",
  "issue_tracker": "https://github.com/christian/ha-heatpump-xy/issues",
  "integration_type": "device",
  "iot_class": "local_polling",
  "loggers": ["pyheatpumpxy"],
  "requirements": ["pyheatpumpxy==0.4.2"],
  "quality_scale": "bronze",
  "zeroconf": [
    { "type": "_heatpump._tcp.local.", "properties": { "vendor": "xy*" } }
  ],
  "dhcp": [
    { "macaddress": "AABBCC*", "hostname": "heatpump-*" }
  ]
}
```

### 2.2 Field-by-field required/optional and HACS vs Core

| Field | Custom (HACS) | Core | Note |
|---|---|---|---|
| `domain` | Required | Required | Lowercase + underscore, == directory name |
| `name` | Required | Required | For "Cloud" variants append "Cloud" too; local has no suffix |
| `version` | **Required** for Custom | **Forbidden** for Core | HACS rejects manifests without `version`; Core is tracked via `pip` |
| `codeowners` | Required (≥ 1 GitHub handle) | Required | Silver: active maintainer (`integration-owner`) |
| `config_flow` | `true` | `true` | YAML setup is dead, ADR-0010 |
| `documentation` | Required | Required (`https://www.home-assistant.io/integrations/<domain>`) | Custom: GitHub README or your own page |
| `issue_tracker` | Required | omit (auto-generated) | |
| `integration_type` | `device` / `hub` / `service` / `helper` / `system` / `hardware` / `entity` / `virtual` | same | Heat pump = `device` (one physical device per entry). A central LAN gateway for multiple devices = `hub`. |
| `iot_class` | Required | Required | For a local heat pump: `local_polling` (or `local_push` if push) |
| `requirements` | Required (PyPI pin) | Required (PyPI pin) | **Always pin exactly**: `package==1.2.3` |
| `dependencies` | optional | optional | Soft via `after_dependencies` (e.g. `zeroconf`) |
| `loggers` | optional | recommended | List of the library's logger names so users can enable debug |
| `quality_scale` | omit or custom value | required from Bronze | Custom default = `custom` |
| `zeroconf` / `dhcp` / `ssdp` / `bluetooth` / `usb` | optional | optional | Trigger discovery (see §4.5) |
| `single_config_entry` | optional | optional | When only one instance makes sense |

**Pitfalls:**
- `version` must be SemVer and must correspond to the GitHub release tag.
- For Core submission, `version` and `issue_tracker` **must not** be included (hassfest rejects them).
- `iot_class` values: `assumed_state | cloud_polling | cloud_push | local_polling | local_push | calculated`.

Sources:
- <https://developers.home-assistant.io/docs/creating_integration_manifest/>
- <https://github.com/home-assistant/core/blob/dev/homeassistant/loader.py>

---

## 3. `hacs.json`

At the **repo root**, not inside the component directory.

```json
{
  "name": "Heatpump XY",
  "homeassistant": "2025.1.0",
  "hacs": "2.0.0",
  "render_readme": true,
  "country": ["DE", "AT", "CH"],
  "zip_release": false
}
```

| Field | Description |
|---|---|
| `name` | Required. Display name in HACS |
| `homeassistant` | Minimum HA Core version — users below this version won't see the integration |
| `hacs` | Minimum HACS version. **Caution:** HACS 2.0.0 broke compatibility with the 1.x line and itself requires HA 2024.4.1+. Don't list `1.34.0` anymore; use `2.0.0` as the minimum. |
| `render_readme` | `true` → display README.md instead of info.md. Otherwise `info.md` must exist |
| `country` | ISO codes; only relevant if regional. For a heat pump usually omit |
| `zip_release` | `true` when the GitHub release contains a ZIP. Default `false` (HACS pulls from the tag) |
| `filename` | only when `zip_release: true` |
| `hide_default_branch` | optional |

Sources:
- <https://hacs.xyz/docs/publish/start/>
- <https://hacs.xyz/docs/publish/integration/>
- <https://github.com/hacs/integration/releases>

---

## 4. Config Flow

### 4.1 Skeleton

```python
# config_flow.py
from __future__ import annotations
from typing import Any
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_API_KEY
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv

from .api import HeatpumpClient, CannotConnect, InvalidAuth
from .const import DOMAIN, DEFAULT_PORT

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): cv.string,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    vol.Required(CONF_API_KEY): cv.string,
})


class HeatpumpConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = HeatpumpClient(
                host=user_input[CONF_HOST],
                port=user_input[CONF_PORT],
                api_key=user_input[CONF_API_KEY],
                session=session,
            )
            try:
                info = await client.async_get_device_info()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info.serial_number)
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: user_input[CONF_HOST]}
                )
                return self.async_create_entry(
                    title=info.model,
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
```

### 4.2 Required building blocks

- **`async_set_unique_id` + `_abort_if_unique_id_configured`** — prevents duplicate setup. On IP changes via DHCP `CONF_HOST` is updated automatically.
- **`unique_id` as a string** (serial number, MAC). From HA 2025.10 non-string is treated as an error.
- **Validate before create**: test the connection first, then `async_create_entry`. No "create the setup entry, then error out in `async_setup_entry`".

### 4.3 Options Flow vs Reconfigure Flow

- **Options Flow**: for behavior settings after setup (e.g. `scan_interval`, "disabled by default" entities). Shown as "Configure" in the config entry details.
- **Reconfigure Flow** (Gold rule `reconfiguration-flow`): when the user needs to change the *connection data* (host, API key) without re-adding the integration.

```python
async def async_step_reconfigure(
    self, user_input: dict[str, Any] | None = None
) -> ConfigFlowResult:
    entry = self._get_reconfigure_entry()
    # validation analogous to async_step_user, then:
    return self.async_update_reload_and_abort(
        entry, data_updates=user_input
    )
```

### 4.4 Reauth Flow (Silver `reauthentication-flow`)

Trigger: raise `ConfigEntryAuthFailed` from `async_setup_entry` or in the coordinator.

```python
async def async_step_reauth(
    self, entry_data: Mapping[str, Any]
) -> ConfigFlowResult:
    return await self.async_step_reauth_confirm()

async def async_step_reauth_confirm(
    self, user_input: dict[str, Any] | None = None
) -> ConfigFlowResult:
    entry = self._get_reauth_entry()
    # ... validation ...
    return self.async_update_reload_and_abort(
        entry, data_updates={CONF_API_KEY: user_input[CONF_API_KEY]}
    )
```

### 4.5 Discovery (Gold `discovery`)

When the heat pump is detectable on the LAN via mDNS or DHCP, declare it in `manifest.json`:

```json
"zeroconf": [{ "type": "_heatpump._tcp.local." }]
```

And in the flow:

```python
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

async def async_step_zeroconf(
    self, discovery_info: ZeroconfServiceInfo
) -> ConfigFlowResult:
    serial = discovery_info.properties.get("serial")
    if not serial:
        return self.async_abort(reason="no_serial")
    await self.async_set_unique_id(serial)
    self._abort_if_unique_id_configured(
        updates={CONF_HOST: str(discovery_info.ip_address)}
    )
    self.context["title_placeholders"] = {"name": discovery_info.name}
    self._discovery_info = discovery_info
    return await self.async_step_discovery_confirm()
```

Sources:
- <https://developers.home-assistant.io/docs/config_entries_config_flow_handler/>
- <https://developers.home-assistant.io/docs/network_discovery/>

---

## 5. `DataUpdateCoordinator`

### 5.1 When to use a coordinator?

**Always** when multiple entities are fed from the same API response. Per-entity `async_update` methods hitting the same API are an anti-pattern and will be rejected by the quality check.

### 5.2 Polling coordinator (standard case)

```python
# coordinator.py
from __future__ import annotations
from datetime import timedelta
import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HeatpumpClient, CannotConnect, InvalidAuth
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=30)

@dataclass
class HeatpumpData:
    flow_temp: float
    return_temp: float
    compressor_state: str
    # ... further fields as a typed dataclass

type HeatpumpConfigEntry = ConfigEntry[HeatpumpCoordinator]


class HeatpumpCoordinator(DataUpdateCoordinator[HeatpumpData]):
    config_entry: HeatpumpConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: HeatpumpConfigEntry,
        client: HeatpumpClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            always_update=False,  # avoids unnecessary listener calls
        )
        self.client = client

    async def _async_setup(self) -> None:
        """Run once before the first refresh — HA 2024.8+."""
        self.device_info = await self.client.async_get_device_info()

    async def _async_update_data(self) -> HeatpumpData:
        try:
            return await self.client.async_get_state()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(err) from err
        except CannotConnect as err:
            raise UpdateFailed(f"Heatpump unreachable: {err}") from err
```

### 5.3 Push coordinator (`local_push`)

When the heat pump sends webhooks/MQTT/UDP push: `update_interval=None`, and register a callback in the API client that calls `coordinator.async_set_updated_data(new_data)`. This way you still benefit from the listener logic and `available`/`last_update_success`.

### 5.4 Initial refresh

**Always** call `await coordinator.async_config_entry_first_refresh()` in `async_setup_entry` — it correctly raises `ConfigEntryNotReady` (Bronze rule `test-before-setup`) and triggers HA's exponential backoff.

Sources:
- <https://developers.home-assistant.io/docs/integration_fetching_data/>
- <https://developers.home-assistant.io/blog/2024/08/05/coordinator_async_setup/>

---

## 6. Entity design

### 6.1 Base entity in `entity.py`

```python
from __future__ import annotations
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import HeatpumpCoordinator
from .const import DOMAIN, MANUFACTURER


class HeatpumpEntity(CoordinatorEntity[HeatpumpCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: HeatpumpCoordinator) -> None:
        super().__init__(coordinator)
        info = coordinator.device_info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info.serial_number)},
            manufacturer=MANUFACTURER,
            model=info.model,
            sw_version=info.firmware,
            configuration_url=f"http://{coordinator.client.host}/",
            serial_number=info.serial_number,
        )
```

### 6.2 `EntityDescription` pattern (mandatory)

```python
# sensor.py
from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass,
)
from homeassistant.const import UnitOfTemperature, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HeatpumpData, HeatpumpConfigEntry
from .entity import HeatpumpEntity


@dataclass(frozen=True, kw_only=True)
class HeatpumpSensorEntityDescription(SensorEntityDescription):
    value_fn: Callable[[HeatpumpData], float | int | str | None]


SENSORS: tuple[HeatpumpSensorEntityDescription, ...] = (
    HeatpumpSensorEntityDescription(
        key="flow_temp",
        translation_key="flow_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda d: d.flow_temp,
    ),
    HeatpumpSensorEntityDescription(
        key="electric_power",
        translation_key="electric_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: d.electric_power,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatpumpConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(HeatpumpSensor(coordinator, desc) for desc in SENSORS)


class HeatpumpSensor(HeatpumpEntity, SensorEntity):
    entity_description: HeatpumpSensorEntityDescription

    def __init__(
        self,
        coordinator: HeatpumpCoordinator,
        description: HeatpumpSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.device_info.serial_number}_{description.key}"
        )

    @property
    def native_value(self) -> float | int | str | None:
        return self.entity_description.value_fn(self.coordinator.data)
```

### 6.3 Required attributes per entity

| Attribute | Purpose | Quality scale |
|---|---|---|
| `_attr_unique_id` | Stable ID, ideally `<serial>_<key>` | Bronze `entity-unique-id` |
| `_attr_has_entity_name = True` | Friendly name = "<Device> <translated key>" | Bronze `has-entity-name` |
| `translation_key` | Translation in `strings.json` → entity → <platform> → <key>.name | Gold `entity-translations` |
| `device_class` | wherever possible (temperature, power, energy, …) | Gold `entity-device-class` |
| `state_class` | `MEASUREMENT` / `TOTAL` / `TOTAL_INCREASING` | Gold |
| `entity_category` | `CONFIG` / `DIAGNOSTIC` for non-primary readings | Gold `entity-category` |
| `_attr_entity_registry_enabled_default = False` | for rarely used / verbose entities | Gold `entity-disabled-by-default` |
| `available` property | from coordinator: `super().available and self.coordinator.last_update_success` | Silver `entity-unavailable` |

### 6.4 Deriving `available` correctly

```python
@property
def available(self) -> bool:
    return super().available and self.entity_description.value_fn(
        self.coordinator.data
    ) is not None
```

Source: <https://developers.home-assistant.io/docs/core/entity/>

---

## 7. Async/await patterns

- **Never blocking I/O in the event loop.** `requests`, `urllib`, `socket.recv` without async, `time.sleep` → forbidden.
- **Synchronous library** → run in executor: `await hass.async_add_executor_job(blocking_call, arg1)`. But for Platinum `async-dependency` the library must be async only.
- **HTTP session**:
  ```python
  from homeassistant.helpers.aiohttp_client import async_get_clientsession
  session = async_get_clientsession(hass)  # shared, auto-cleanup
  ```
  Never instantiate `aiohttp.ClientSession()` yourself inside integration code. Quality scale Platinum rule `inject-websession`: the external library must accept a session as a parameter.
- **Timeouts**: `async with asyncio.timeout(10):` (Python 3.11+ style) instead of `async_timeout`.
- **Context managers** used correctly:
  ```python
  async with session.get(url, timeout=ClientTimeout(total=10)) as resp:
      resp.raise_for_status()
      return await resp.json()
  ```
- **No `asyncio.create_task` without tracking.** When necessary: `entry.async_create_background_task(hass, coro, name="heatpump_listener")`.

---

## 8. API client

### 8.1 Separation from HA

The API class **must not** contain HA imports. It is potentially its own PyPI package (`pyheatpumpxy`) — that is the prerequisite for `dependency-transparency` and a requirement for Core merge of non-trivial integrations.

### 8.2 Example client

```python
# api.py (or external package pyheatpumpxy)
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Self
import aiohttp


class HeatpumpError(Exception):
    """Base class."""


class CannotConnect(HeatpumpError):
    """Network/HTTP error."""


class InvalidAuth(HeatpumpError):
    """401/403."""


@dataclass(slots=True)
class DeviceInfo:
    serial_number: str
    model: str
    firmware: str


@dataclass(slots=True)
class State:
    flow_temp: float
    return_temp: float
    electric_power: float
    compressor_state: str


class HeatpumpClient:
    def __init__(
        self,
        host: str,
        port: int,
        api_key: str,
        session: aiohttp.ClientSession,
        *,
        request_timeout: float = 10.0,
    ) -> None:
        self._base = f"http://{host}:{port}"
        self._headers = {"X-API-Key": api_key}
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)

    async def _request(self, path: str) -> dict:
        try:
            async with self._session.get(
                f"{self._base}{path}",
                headers=self._headers,
                timeout=self._timeout,
            ) as resp:
                if resp.status in (401, 403):
                    raise InvalidAuth(f"HTTP {resp.status}")
                if resp.status >= 500:
                    raise CannotConnect(f"HTTP {resp.status}")
                resp.raise_for_status()
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CannotConnect(str(err)) from err

    async def async_get_device_info(self) -> DeviceInfo:
        raw = await self._request("/api/info")
        return DeviceInfo(**raw)

    async def async_get_state(self) -> State:
        raw = await self._request("/api/state")
        return State(**raw)
```

### 8.3 Retry/backoff

`DataUpdateCoordinator` handles *polling* itself — no retry loop there. But for *individual* commands (e.g. setting a setpoint): `tenacity` or a simple 3x retry with exponential backoff on `CannotConnect` is acceptable.

---

## 9. Type hints and modern Python

- **Python 3.13** shipped with HA Core 2024.12 for HA OS / container (automatic upgrade per release blog) and has been the minimum required version for self-managed Core installations since HA 2025.2. You may use `match`, `Self`, `type X = ...` aliases, `Generic[T]` with the `class C[T]:` syntax.
- **`from __future__ import annotations`** at the top of every file.
- For data structures: `dataclass` (with `slots=True, kw_only=True`), for API responses optionally `pydantic` v2 or `mashumaro`. A bare dict is an anti-pattern.
- **`py.typed`** marker in the custom-component directory is not required (it isn't a package), but it is required in the **external API package** (Platinum `strict-typing`).
- Enable strict typing:
  ```toml
  # pyproject.toml
  [tool.mypy]
  strict = true
  python_version = "3.13"
  ```

---

## 10. Translations

### 10.1 `strings.json` (source file — English)

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Connect to Heatpump",
        "data": {
          "host": "Host or IP",
          "port": "Port",
          "api_key": "API key"
        },
        "data_description": {
          "host": "Hostname or IP address of the heatpump in the local network.",
          "api_key": "Stored under Service > API in the heatpump web interface."
        }
      },
      "reauth_confirm": {
        "title": "Re-authenticate Heatpump",
        "data": { "api_key": "API key" }
      },
      "reconfigure": {
        "title": "Reconfigure Heatpump",
        "data": { "host": "Host or IP", "port": "Port", "api_key": "API key" }
      }
    },
    "error": {
      "cannot_connect": "Failed to connect.",
      "invalid_auth": "Invalid API key.",
      "unknown": "Unexpected error."
    },
    "abort": {
      "already_configured": "Device is already configured",
      "reauth_successful": "Re-authentication was successful",
      "reconfigure_successful": "Re-configuration was successful"
    }
  },
  "entity": {
    "sensor": {
      "flow_temperature": { "name": "Flow temperature" },
      "electric_power":   { "name": "Electric power" }
    }
  },
  "exceptions": {
    "set_temperature_failed": {
      "message": "Failed to set temperature: {reason}"
    }
  }
}
```

### 10.2 Translations

`translations/en.json` is a 1:1 copy of `strings.json` (that's HA convention; in Core it is generated automatically via Lokalise, in a custom component you have to provide it yourself).

`translations/de.json` analogously with German strings.

### 10.3 Icon translations (Gold `icon-translations`)

```json
// icons.json
{
  "entity": {
    "sensor": {
      "flow_temperature": { "default": "mdi:thermometer-water" },
      "compressor_state": {
        "default": "mdi:engine",
        "state": { "off": "mdi:engine-off", "on": "mdi:engine" }
      }
    }
  }
}
```

Hassfest validates here that only `mdi:` prefixes are used.

Source: <https://developers.home-assistant.io/docs/internationalization/>

---

## 11. Services / actions

### 11.1 `services.yaml`

```yaml
set_setpoint:
  target:
    entity:
      domain: climate
      integration: heatpump_xy
  fields:
    temperature:
      required: true
      example: 21.5
      selector:
        number:
          min: 5
          max: 60
          step: 0.5
          unit_of_measurement: "°C"
```

### 11.2 Registration

Register services in **`async_setup`**, not in `async_setup_entry`. That way they exist even without a loaded entry and automations can reference them.

```python
# __init__.py
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def _async_set_setpoint(call: ServiceCall) -> ServiceResponse:
        entry_id = call.data.get("config_entry_id")
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError("config_entry_not_loaded")
        coordinator: HeatpumpCoordinator = entry.runtime_data
        try:
            await coordinator.client.async_set_setpoint(call.data["temperature"])
        except CannotConnect as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="set_temperature_failed",
                translation_placeholders={"reason": str(err)},
            ) from err
        await coordinator.async_request_refresh()
        return {"applied": call.data["temperature"]}

    hass.services.async_register(
        DOMAIN, "set_setpoint", _async_set_setpoint,
        schema=vol.Schema({...}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True
```

Source: <https://developers.home-assistant.io/docs/dev_101_services/>

---

## 12. Diagnostics

```python
# diagnostics.py
from __future__ import annotations
from typing import Any
from dataclasses import asdict
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .coordinator import HeatpumpConfigEntry

TO_REDACT = {CONF_API_KEY, "serial_number", "mac"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HeatpumpConfigEntry,
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "data": async_redact_data(asdict(coordinator.data), TO_REDACT),
        "device_info": async_redact_data(
            asdict(coordinator.device_info), TO_REDACT
        ),
    }
```

Required from **Gold** (`diagnostics`). Always redact sensitive fields — diagnostics are posted as plain text in GitHub issues.

Source: <https://developers.home-assistant.io/docs/core/integration/diagnostics/>

---

## 13. Repairs

```python
from homeassistant.helpers import issue_registry as ir

ir.async_create_issue(
    hass,
    DOMAIN,
    "firmware_outdated",
    is_fixable=False,
    is_persistent=True,
    severity=ir.IssueSeverity.WARNING,
    translation_key="firmware_outdated",
    translation_placeholders={"current": fw, "minimum": "2.5.0"},
    learn_more_url="https://github.com/christian/ha-heatpump-xy/wiki/firmware",
)
```

- `severity`: `CRITICAL | ERROR | WARNING`
- `is_fixable=True` + `repairs.py` with `RepairsFlow` for an interactive resolution
- `is_persistent=True` → stays visible after an HA restart

Source: <https://developers.home-assistant.io/docs/core/platform/repairs/>

---

## 14. Logging

```python
# Module-wide
_LOGGER = logging.getLogger(__name__)

# Usage
_LOGGER.debug("Heatpump state: %s", state)        # yes, lazy formatting
_LOGGER.warning("Retrying after %.1fs", delay)
_LOGGER.error("Setpoint failed: %s", err)
# never: _LOGGER.info(f"Heatpump {host} polled")    # no, no f-string in logs
# never: print(...)
```

Rules (Silver `log-when-unavailable`):
- **No spam.** On connection loss: one `warning` on the first failure, one `info` on recovery. `DataUpdateCoordinator` does this automatically; only add your own logs when needed.
- **No `info` during normal operation.** `debug` for telemetry, `info` only for lifecycle (setup, unload).
- Add **logger names** in `manifest.json` under `loggers` so users can enable debug.

---

## 15. Tests

### 15.1 Setup

```toml
# pyproject.toml
[project.optional-dependencies]
test = [
  "pytest>=8",
  "pytest-asyncio",
  "pytest-cov",
  "pytest-homeassistant-custom-component",
  "syrupy",
  "aioresponses",
]
```

### 15.2 `tests/conftest.py`

```python
from __future__ import annotations
import pytest
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    return snapshot.use_extension(HomeAssistantSnapshotExtension)
```

### 15.3 Config flow test

```python
from unittest.mock import AsyncMock, patch
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from custom_components.heatpump_xy.const import DOMAIN


async def test_user_flow_happy_path(hass):
    with patch(
        "custom_components.heatpump_xy.config_flow.HeatpumpClient.async_get_device_info",
        new=AsyncMock(return_value=DeviceInfo("ABC123", "XY-9000", "1.0.0")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "10.0.0.5", "port": 80, "api_key": "secret"},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["host"] == "10.0.0.5"
```

### 15.4 Snapshot tests for sensors

```python
async def test_sensors(hass, snapshot, init_integration):
    state = hass.states.get("sensor.heatpump_xy_flow_temperature")
    assert state == snapshot
```

Creating snapshots: `pytest --snapshot-update`.

### 15.5 Coverage

- Bronze requires `config-flow-test-coverage` = **100%** for `config_flow.py`.
- Silver requires `test-coverage` ≥ **95%** overall.

```bash
pytest tests/ \
  --cov=custom_components.heatpump_xy \
  --cov-report=term-missing \
  --cov-fail-under=95
```

Sources:
- <https://developers.home-assistant.io/docs/development_testing/>
- <https://github.com/MatthewFlamm/pytest-homeassistant-custom-component>

---

## 16. Quality Scale

### 16.1 Tiers (as of: official checklist Nov 2024, validated for HA 2026.x)

There are **4 scaled tiers** (Bronze → Silver → Gold → Platinum). Each higher tier includes all rules of the lower tiers. Alongside them are **4 special tiers**: `no_score`, `internal`, `legacy`, `custom`.

Source for the full list: <https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist/> (canonical, as of 20 Nov 2024).

### 16.2 Full rule list (52 rules: 18 + 10 + 21 + 3)

#### 🥉 Bronze (18) – required for every new integration

| Slug | Meaning |
|---|---|
| `action-setup` | Register services in `async_setup`, not `async_setup_entry`. |
| `appropriate-polling` | Sensible default polling interval (not too fast, not too slow). |
| `brands` | Logo/icon in the `home-assistant/brands` repo (Core) or `brand/` locally (Custom from 2026.3). |
| `common-modules` | Coordinator in `coordinator.py`, base entity in `entity.py`. |
| `config-flow` | UI setup, with `data_description` for context, `data` vs `options` correctly separated. |
| `config-flow-test-coverage` | 100% coverage in the config flow. |
| `dependency-transparency` | External library on PyPI, open source, built from inspectable source. |
| `docs-actions` | Docs describe every service action. |
| `docs-high-level-description` | Docs describe the product/service. |
| `docs-installation-instructions` | Step-by-step setup guide. |
| `docs-removal-instructions` | How to uninstall cleanly. |
| `entity-event-setup` | Subscriptions in `async_added_to_hass` / cleanup in `async_will_remove_from_hass`. |
| `entity-unique-id` | Stable `unique_id` per entity. |
| `has-entity-name` | `_attr_has_entity_name = True`. |
| `runtime-data` | `ConfigEntry.runtime_data` instead of `hass.data[DOMAIN][...]`. |
| `test-before-configure` | Connection is tested before `async_create_entry`. |
| `test-before-setup` | `ConfigEntryNotReady` / `ConfigEntryAuthFailed` raised during setup. |
| `unique-config-entry` | Duplicate setup prevented. |

#### 🥈 Silver (10) – runtime stability

| Slug | Meaning |
|---|---|
| `action-exceptions` | `ServiceValidationError` / `HomeAssistantError` in services. |
| `config-entry-unloading` | `async_unload_entry` implemented cleanly. |
| `docs-configuration-parameters` | Docs describe every options parameter. |
| `docs-installation-parameters` | Docs describe every setup field. |
| `entity-unavailable` | Entity becomes `unavailable` on API loss. |
| `integration-owner` | ≥ 1 active codeowner. |
| `log-when-unavailable` | One error, one recovery, no spam. |
| `parallel-updates` | `PARALLEL_UPDATES` explicit in every platform file. |
| `reauthentication-flow` | Reauth flow implemented. |
| `test-coverage` | ≥ 95% test coverage. |

#### 🥇 Gold (21) – best UX

| Slug | Meaning |
|---|---|
| `devices` | Correct entry in the device registry (manufacturer, model, sw_version, identifiers). |
| `diagnostics` | Diagnostics platform with redaction. |
| `discovery` | Auto-discovery via Zeroconf/SSDP/DHCP/Bluetooth/USB when possible. |
| `discovery-update-info` | IP update via discovery (DHCP) is applied. |
| `docs-data-update` | Docs describe push vs poll, intervals. |
| `docs-examples` | Automation examples in the docs. |
| `docs-known-limitations` | Known limitations documented. |
| `docs-supported-devices` | List of supported models. |
| `docs-supported-functions` | List of provided platforms/entities. |
| `docs-troubleshooting` | Troubleshooting section. |
| `docs-use-cases` | Use-case descriptions. |
| `dynamic-devices` | Devices added after setup are recognised automatically. |
| `entity-category` | `EntityCategory.CONFIG` / `DIAGNOSTIC` set correctly. |
| `entity-device-class` | `device_class` wherever possible. |
| `entity-disabled-by-default` | Rarely used entities disabled by default. |
| `entity-translations` | Entity names via `translation_key`. |
| `exception-translations` | Exception messages with `translation_domain`/`translation_key`. |
| `icon-translations` | `icons.json` instead of hardcoded `mdi:`. |
| `reconfiguration-flow` | `async_step_reconfigure` implemented. |
| `repair-issues` | `ir.async_create_issue` instead of silent log errors. |
| `stale-devices` | Removed devices are also removed in HA. |

#### 🏆 Platinum (3) – technical excellence

| Slug | Meaning |
|---|---|
| `async-dependency` | Library is fully async (no executor wrapping). |
| `inject-websession` | Library accepts an external `aiohttp.ClientSession`. |
| `strict-typing` | `mypy --strict` clean. |

### 16.3 `quality_scale.yaml`

```yaml
rules:
  # Bronze
  action-setup: done
  appropriate-polling: done
  brands: done
  common-modules: done
  config-flow: done
  config-flow-test-coverage: done
  dependency-transparency: done
  docs-actions:
    status: exempt
    comment: Integration registers no custom actions.
  docs-high-level-description: done
  docs-installation-instructions: done
  docs-removal-instructions: done
  entity-event-setup: done
  entity-unique-id: done
  has-entity-name: done
  runtime-data: done
  test-before-configure: done
  test-before-setup: done
  unique-config-entry: done
  # Silver
  action-exceptions: todo
  config-entry-unloading: done
  # ...
```

Hassfest validates: every tier declared in the manifest forces complete `done`/`exempt` of all rules of that tier and below. Exemptions **require** a `comment`.

### 16.4 Manifest entry

```json
"quality_scale": "bronze"
```

Only upgrade to `silver`/`gold`/`platinum` **when all rules are actually met** and a PR documents the upgrade.

Sources:
- <https://developers.home-assistant.io/docs/core/integration-quality-scale/>
- <https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist/>
- <https://github.com/home-assistant/architecture/blob/master/adr/0022-integration-quality-scale.md>
- <https://developers.home-assistant.io/blog/2024/11/20/integration-quality-scale/>

---

## 17. HACS specifics

### 17.1 Required files (repository root)

- `README.md` — visible in HACS when `render_readme: true`
- `hacs.json` — see §3
- `LICENSE` — HACS rejects without it
- `custom_components/<domain>/manifest.json` with `version` and `documentation`
- GitHub **releases** (tags alone are *not* enough) with SemVer (`v0.1.0`)

### 17.2 GitHub Actions

```yaml
# .github/workflows/hassfest.yaml
name: Validate
on: [push, pull_request]
jobs:
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master
  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with:
          category: integration
```

### 17.3 Brands / logo

Three routes, depending on maturity:

1. **Custom (HACS), HA ≥ 2026.3**: local `custom_components/heatpump_xy/brand/` with `icon.png` (256×256), `icon@2x.png` (512×512), `logo.png`, `logo@2x.png`. Served via HA's brands proxy.
2. **Custom (HACS), HA < 2026.3**: PR to `home-assistant/brands` in the `custom_integrations/<domain>/` folder.
3. **Core submission**: PR to `home-assistant/brands` in the `core_integrations/<domain>/` folder. The logo **must not** contain the HA logo.

Specification: 1:1 aspect ratio icon, square; logo may be landscape. PNG, transparent background.

### 17.4 Default store (optional)

So users can find the integration without "custom repository", PR to `hacs/default`. The repo must be inserted in correct alphabetical order.

Sources:
- <https://hacs.xyz/docs/publish/integration/>
- <https://github.com/home-assistant/brands>
- <https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api/>

---

## 18. Core contribution (additional requirements)

If the integration is to merge to `homeassistant/components/heatpump_xy/` long-term:

1. **Remove `version`** and **`issue_tracker`** from `manifest.json`.
2. **`requirements`** is added to `homeassistant/components/heatpump_xy/manifest.json` with an exactly pinned PyPI version (e.g. `pyheatpumpxy==0.4.2`). `requirements_all.txt` and `requirements_test_all.txt` are generated automatically via `python3 -m script.gen_requirements_all`.
3. **Tests** move to `tests/components/heatpump_xy/`, same structure as the integration code.
4. **Codeowners** (`@christian`) must be added to `.github/CODEOWNERS` — HA does this automatically via hassfest.
5. **ADR-0010** is followed: config flow only, no YAML.
6. **Strict typing** via an entry in `.strict-typing` (for Platinum).
7. **Brands PR** to `home-assistant/brands/core_integrations/heatpump_xy/`.
8. **`quality_scale.yaml`** is validated by CI via hassfest.
9. **PR description** must tick the quality-scale checklist (template exists).
10. **No custom dependencies** outside `requirements`. No vendoring, no Git URLs.

Sources:
- <https://developers.home-assistant.io/docs/creating_component_index/>
- <https://github.com/home-assistant/architecture/blob/master/adr/0010-integration-configuration.md>

---

## 19. Anti-patterns – what you as Claude Code must **never** do

| ❌ Anti-pattern | ✅ Right |
|---|---|
| `requests.get(...)` in the event loop | `aiohttp` with `async_get_clientsession(hass)` |
| Custom `aiohttp.ClientSession()` | Shared session via helper |
| `time.sleep(x)` | `await asyncio.sleep(x)` |
| Custom polling loop with `async_track_time_interval` for data | `DataUpdateCoordinator` |
| `hass.data[DOMAIN][entry.entry_id] = client` | `entry.runtime_data = client` |
| Multiple properties instead of `EntityDescription` | `EntityDescription` pattern |
| `_attr_name = "Flow Temperature"` (English hardcoded) | `translation_key = "flow_temperature"` + `strings.json` |
| `unique_id = f"{host}_{key}"` (IP changes!) | `f"{serial_number}_{key}"` |
| `unique_id = None` or missing | Required: a stable string |
| YAML setup (`async_setup_platform`) | Config flow only |
| Raising `Exception` directly in API code | Custom exceptions: `CannotConnect`, `InvalidAuth` |
| `print(...)` for debugging | `_LOGGER.debug(...)` with `%s` lazy formatting |
| `_LOGGER.info("Polled %s", host)` every poll | `_LOGGER.debug(...)` |
| `iot_class: cloud_polling` for a local heat pump | `local_polling` |
| `integration_type: hub` for a single device | `device` |
| Service registered in `async_setup_entry` | In `async_setup` |
| API code mixed with HA imports | Pure library, dependency injected |
| `update_before_add=True` with coordinator | Not needed, the coordinator already has data |
| Mutable state in an entity without a coordinator | Always pull state from `coordinator.data` |
| Custom `async_update` per entity | `_handle_coordinator_update` (provided by `CoordinatorEntity`) |
| Calling a synchronous library without executor | `await hass.async_add_executor_job(fn, arg)` |
| Diagnostics without redacting API keys | `async_redact_data(..., TO_REDACT)` |

---

## 20. Documentation pointers (for Claude Code to fetch)

| Topic | URL |
|---|---|
| Integration File Structure | <https://developers.home-assistant.io/docs/creating_integration_file_structure/> |
| Manifest | <https://developers.home-assistant.io/docs/creating_integration_manifest/> |
| Config Flow | <https://developers.home-assistant.io/docs/config_entries_config_flow_handler/> |
| Options Flow | <https://developers.home-assistant.io/docs/config_entries_options_flow_handler/> |
| Network Discovery | <https://developers.home-assistant.io/docs/network_discovery/> |
| Fetching Data / Coordinator | <https://developers.home-assistant.io/docs/integration_fetching_data/> |
| Coordinator `_async_setup` | <https://developers.home-assistant.io/blog/2024/08/05/coordinator_async_setup/> |
| `runtime_data` | <https://developers.home-assistant.io/blog/2024/04/30/store-runtime-data-inside-config-entry/> |
| Entity | <https://developers.home-assistant.io/docs/core/entity/> |
| Sensor | <https://developers.home-assistant.io/docs/core/entity/sensor/> |
| Climate | <https://developers.home-assistant.io/docs/core/entity/climate/> |
| Device Registry | <https://developers.home-assistant.io/docs/device_registry_index/> |
| Internationalization | <https://developers.home-assistant.io/docs/internationalization/> |
| Service Actions | <https://developers.home-assistant.io/docs/dev_101_services/> |
| Diagnostics | <https://developers.home-assistant.io/docs/core/integration/diagnostics/> |
| Repairs | <https://developers.home-assistant.io/docs/core/platform/repairs/> |
| Testing | <https://developers.home-assistant.io/docs/development_testing/> |
| Quality Scale Overview | <https://developers.home-assistant.io/docs/core/integration-quality-scale/> |
| Quality Scale Checklist | <https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist/> |
| Quality Scale Rules Index | <https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/> |
| ADR-0010 (no YAML) | <https://github.com/home-assistant/architecture/blob/master/adr/0010-integration-configuration.md> |
| ADR-0022 (Quality Scale) | <https://github.com/home-assistant/architecture/blob/master/adr/0022-integration-quality-scale.md> |
| Hassfest GitHub Action | <https://developers.home-assistant.io/blog/2020/04/16/hassfest/> |
| HACS Integration Publish | <https://hacs.xyz/docs/publish/integration/> |
| HACS hacs.json Spec | <https://hacs.xyz/docs/publish/start/> |
| Brands Repo | <https://github.com/home-assistant/brands> |
| Brands Proxy API (local brands) | <https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api/> |
| `pytest-homeassistant-custom-component` | <https://github.com/MatthewFlamm/pytest-homeassistant-custom-component> |
| Integration Blueprint | <https://github.com/home-assistant/integration_blueprint> |

---

## 21. Implementation checklist for the heat-pump integration

1. **Derive the repo from `integration_blueprint`**, rename the domain to `heatpump_xy`.
2. **API wrapper** (`api.py` or its own PyPI package) implemented with `aiohttp` + custom exceptions + dataclasses.
3. **`manifest.json`** with `iot_class: local_polling`, `integration_type: device`, `quality_scale: bronze`.
4. **Config flow** with a user step + validation + unique ID = serial number; if Zeroconf/DHCP discovery is possible, add it.
5. **`DataUpdateCoordinator`** in `coordinator.py` with `_async_setup` and `_async_update_data`.
6. **`entity.py`** with `HeatpumpEntity(CoordinatorEntity)` base.
7. **Platforms** (`sensor.py`, `climate.py`, optionally `number.py`/`select.py`/`switch.py`) via the `EntityDescription` pattern. **No** heat-pump domain logic (e.g. SG-Ready) — that belongs in helper entities the user orchestrates themselves, or in later extensions.
8. **`strings.json` + `translations/en.json` + `translations/de.json`** with all config/entity/exception strings.
9. **`icons.json`** for sensible default icons.
10. **`diagnostics.py`** with redaction.
11. **Reauth flow** (Silver) and **reconfigure flow** (Gold), if there is bandwidth.
12. **Tests**: `test_config_flow.py` (100%), `test_coordinator.py`, `test_init.py`, `test_sensor.py` with snapshots.
13. **Hassfest + HACS action** in `.github/workflows/`.
14. **`quality_scale.yaml`** with status per rule (Bronze fully `done`).
15. **Brands** locally (HA ≥ 2026.3) or via PR to `home-assistant/brands/custom_integrations/heatpump_xy/`.
16. **GitHub release** v0.1.0 created → HACS can install.
17. **Optional**: PR to `hacs/default` for default-store listing.
18. **Long-term**: PR to `home-assistant/core` with all adaptations from §18.

---

## 22. Known uncertainties / "recent changes, verify"

- **Local brand images** are only possible from HA 2026.3 on; before that, PR to the `brands` repo.
- The **`quality_scale.yaml`** format may change in places — when in doubt, align with a current Bronze integration in Core (e.g. `homeassistant/components/peblar/`).
- **Python 3.13** came with HA 2024.12 in HA OS / container (automatically rolled out per the official release blog) and has been the binding minimum version for standalone Core installations since HA 2025.2. Old `from __future__` tricks are no longer necessary, but they don't hurt either.
- **`update_before_add`** in `async_add_entities` is obsolete with the coordinator pattern — don't set it anymore.
- **HACS 2.x** is the current major line and itself requires HA Core 2024.4.1+. The `hacs` field in `hacs.json` should be at least `2.0.0`, no longer the old 1.34.x. Pinning `hacs/action@main` to `main` lets CI run against the current rules.
- **Quality scale rules** may change (explicitly expected per ADR-0022); check at every major release. The current anchor is the checklist file dated 20 Nov 2024 with 18+10+21+3 rules.

> If you, Claude Code, are unsure about anything (e.g. the exact current required manifest fields, new coordinator methods, changes to HACS validation), **fetch the relevant docs URL from §20** rather than guessing or reproducing outdated knowledge. Better safe than reproducible bug.
