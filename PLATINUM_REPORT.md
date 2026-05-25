# PLATINUM_REPORT — `ha-silverline` (poolex_silverline)

Produced by the PLATINUM_LOOP orchestrator on 2026-05-25 against working-tree
HEAD `890d374` plus three loop rounds of consolidation. Every claim below
points at a `file:line` or a test name; every exemption is re-validated against
the actual code.

---

## 1. Gate output

```
$ bash scripts/platinum-gate.sh
SUMMARY:  15 passed   0 failed   1 skipped
SKIPPED (must be confirmed in CI, NOT a pass):
  - hassfest + HACS
GATE: GREEN — all hard checks pass.
```

The 15 PASS checks: `ruff lint`, `ruff format check`, `mypy strict —
integration`, `mypy strict — library`, `mypy strict flag is set (integration)`,
`mypy strict flag is set (library)`, `library ships py.typed`, `pytest —
integration (cov ≥ 95%)` → 98.45% on 882 stmts, `pytest — library (cov ≥
95%)` → 97.20% on 608 stmts, `no unjustified skip/xfail/pragma-no-cover`,
`quality_scale + manifest are internally honest`, `repo has README + LICENSE
+ hacs.json`, `manifest version present`, `library builds (sdist+wheel) with
py.typed in wheel`, `CI coverage gate not weakened (>= 95)`.

### Hassfest — confirmed GREEN locally

The local gate skipped hassfest because no act runner was installed. The
orchestrator instead ran the official `ghcr.io/home-assistant/hassfest`
container directly:

```
Validating application_credentials, bluetooth, codeowners, conditions,
config_schema, dependencies, dhcp, icons, integration_info, integration_type,
json, labs, manifest, mqtt, quality_scale, requirements, services, ssdp,
translations, triggers, usb, zeroconf, config_flow — done.

Integrations: 1
Invalid integrations: 0
```

Notably the `quality_scale` validator (the same one Core uses) passed against
`custom_components/poolex_silverline/quality_scale.yaml`.

### HACS — pending CI confirmation

The local `ghcr.io/hacs/action:main` container requires a `GITHUB_TOKEN` for
GitHub API metadata. Per orchestrator decision the bundled refactor is being
pushed to `github/main` so the existing `.github/workflows/hacs.yaml` job
runs in CI. **DONE is not declared until that job reports green.**

---

## 2. Per-rule evidence (all 52 rules)

### Bronze

| Rule | Status | Evidence |
|---|---|---|
| `action-setup` | exempt | No `hass.services.async_register` / `async_register_admin_service` anywhere in `custom_components/poolex_silverline/` (grep returns empty). Integration registers zero service actions in v0.6.x. |
| `appropriate-polling` | exempt | `coordinator.py:103` registers `add_listener(self._handle_push)` for the primary push path; `coordinator.py:135` calls `async_set_updated_data` on each STATUS frame; `coordinator.py:68` sets `update_interval=30s` as fallback heartbeat. `manifest.json:8` is `iot_class: local_push`. |
| `brands` | done | `custom_components/poolex_silverline/brand/{icon.png, icon@2x.png, logo.png, logo@2x.png}` present. HA ≥ 2026.3 serves these via the brands proxy. |
| `common-modules` | done | `coordinator.py:53` `SilverlineCoordinator`; `entity.py:14` `SilverlineEntity`; `util.py` shared `derive_hvac_mode`/`derive_preset`/`mode_temp_range`/`compute_hvac_action`. |
| `config-flow` | done | `config_flow.py:87` `SilverlineConfigFlow(ConfigFlow, domain=DOMAIN)` — user/reauth/reconfigure/discovery steps all present (lines 97/116/142/176). `strings.json:13-18` provides `data_description` for every user-step field. |
| `config-flow-test-coverage` | done | `tests/test_config_flow.py` contains 21 test functions covering user happy path, validation errors, already-configured abort, reauth happy + failure, reconfigure happy + mismatch + validation failure, four discovery paths, delegation, verify exception, productKey filtering, re-prompt-on-invalid-key. config_flow.py at 100% coverage. |
| `dependency-transparency` | done | `manifest.json:12` pins `pysilverline==0.2.1`; library is open-source MIT, built from `pysilverline/` source tree, published to PyPI via `.github/workflows/pysilverline-pypi.yaml`. |
| `docs-actions` | exempt | No service actions registered (same proof as `action-setup`). |
| `docs-high-level-description` | done | `README.md:1-19` "At a glance" + product framing. |
| `docs-installation-instructions` | done | `README.md:54-85` "Installation" + "Setup". |
| `docs-removal-instructions` | done | `README.md:250-254` "Removal". |
| `entity-event-setup` | done | `coordinator.py:104-107` registers listeners in `_async_setup`; `coordinator.py:250-257` tears them down in `async_shutdown`. `climate.py` uses `async_added_to_hass` via the `CoordinatorEntity` chain (`async_on_remove`). |
| `entity-unique-id` | done | Every platform sets `self._attr_unique_id = f"{device_id}_<key>"` (climate.py:72, sensor.py:239, binary_sensor.py:136, switch.py:68, number.py:96, select.py:108/168). |
| `has-entity-name` | done | `entity.py:17` `_attr_has_entity_name = True` on shared base. |
| `runtime-data` | done | `coordinator.py:50` defines `SilverlineConfigEntry = ConfigEntry[SilverlineCoordinator]`; `__init__.py:115` assigns `entry.runtime_data = coordinator`; every platform reads `entry.runtime_data`. |
| `test-before-configure` | done | `config_flow.py:104/129/152` call `_try_validate` (config_flow.py:67-83 opens + status-checks the client) before `async_create_entry`/`async_update_reload_and_abort`. |
| `test-before-setup` | done | `coordinator.py:99-108` raises `UpdateFailed` on `CannotConnect`; `coordinator.py:111-112` raises `ConfigEntryAuthFailed` on `InvalidAuth`. Verified by `tests/test_init.py::test_setup_retry_on_connect_failure` + `tests/test_init.py::test_setup_triggers_reauth_on_invalid_key`. |
| `unique-config-entry` | done | `config_flow.py:102` `await self.async_set_unique_id(device_id)` + `_abort_if_unique_id_configured()`. Same in `async_step_integration_discovery`. |

### Silver

| Rule | Status | Evidence |
|---|---|---|
| `action-exceptions` | exempt | No service actions registered. |
| `config-entry-unloading` | done | `__init__.py:121-125` `async_unload_entry` forwards unload + calls `await entry.runtime_data.async_shutdown()`. `tests/test_init.py::test_setup_and_unload` verifies `ConfigEntryState.NOT_LOADED`. |
| `docs-configuration-parameters` | exempt | No options flow in v0.x (`config_flow.py` has no `async_step_init`/`OptionsFlow`); the reconfigure flow is the substitute, documented at `README.md:87-92`. |
| `docs-installation-parameters` | done | `strings.json:13-18` (data_description for host/port/device_id/local_key) + `README.md:68-85` setup walkthrough. |
| `entity-unavailable` | done | `coordinator.py:240-248` `_handle_connection_change(False)` flips `last_update_success`; platform `available` properties gate on `super().available` + coordinator data. `tests/test_coordinator.py::test_entities_unavailable_on_disconnect` verifies. |
| `integration-owner` | done | `manifest.json:4` `"codeowners": ["@christianreiss"]`. |
| `log-when-unavailable` | done | `coordinator.py:232` info-logs `"connection to %s restored"`; `coordinator.py:243-246` warning-logs `"connection to %s lost; …"`; idempotent flag prevents repeats. `tests/test_coordinator.py::test_connection_change_logs_lost_and_restored` enforces one-warning-one-info contract. |
| `parallel-updates` | done | Every platform declares an explicit `PARALLEL_UPDATES` value: `climate.py:37=1`, `select.py:38=1`, `switch.py:20=1`, `number.py:35=1`, `sensor.py:28=0`, `binary_sensor.py:23=0`. |
| `reauthentication-flow` | done | `config_flow.py:116-140` `async_step_reauth` + `async_step_reauth_confirm`; `tests/test_config_flow.py:78/98` cover happy + bad-key paths; `tests/test_coordinator.py:29` verifies coordinator triggers reauth on `InvalidAuth`. |
| `test-coverage` | done | `tests/` contains 240 test functions; integration coverage = **98.45%** (882 stmts); CI enforces `--cov-fail-under=95` at `.github/workflows/tests.yaml:54`. |

### Gold

| Rule | Status | Evidence |
|---|---|---|
| `devices` | done | `entity.py:18-27` builds HA `DeviceInfo` with identifiers={(DOMAIN, device_id)}, manufacturer=`MANUFACTURER`, model=`MODEL`, name, serial_number=device_id. `coordinator.py:72` exposes `self.device_id: str = client.device_id` directly. `sw_version` is genuinely undiscoverable on Tuya v3.3 local protocol, so it is omitted rather than hardcoded `None`. |
| `diagnostics` | done | `diagnostics.py:35-46` `async_get_config_entry_diagnostics` returns redacted entry + device_info + state; `diagnostics.py:15-27` TO_REDACT list. `tests/test_diagnostics.py::test_diagnostics_redacts_secrets` verifies. |
| `discovery` | done | `__init__.py:35-90` spawns background UDP listener (`pysilverline.discovery.discover`) that fires `SOURCE_INTEGRATION_DISCOVERY` flows via `config_flow.py:176-251 async_step_integration_discovery`. `tests/test_init.py::test_discovery_loop_forwards_product_key` + `tests/test_config_flow.py::test_discovery_flow_happy_path` verify. UDP broadcast is the canonical Tuya-LAN discovery channel (also used by tinytuya / tuya-local); the comment in `quality_scale.yaml:55-69` documents this honestly. |
| `discovery-update-info` | done | `config_flow.py:217-233` detects existing entry on new IP, verifies via `_verify_host` (encrypted handshake under stored `local_key`), then `_abort_if_unique_id_configured(updates={CONF_HOST: host})`. The verification step blocks LAN spoofing because the Tuya discovery key is public. `tests/test_init.py::test_discovery_loop_suppresses_duplicate_ip_but_refires_on_change` + `tests/test_config_flow.py::test_discovery_rewrites_host_only_on_verified_response` + `…::test_discovery_ignores_unverified_host` verify. |
| `docs-data-update` | done | `README.md:94-101` "Data update model" documents the 30 s poll + ~200 ms push. |
| `docs-examples` | done | `README.md:164-249` "Examples" with three concrete automations. |
| `docs-known-limitations` | done | `README.md:103-121` "Known limitations" (firmware-dependent DPs, no °F, no preset in Auto, per-mode setpoint memory). |
| `docs-supported-devices` | done | `README.md:41-53` "Supported devices" lists PC-SLP090N + JetLine Selection FI family + OEM siblings. |
| `docs-supported-functions` | done | `README.md:20-40` "Features" + `README.md:147-163` use-cases. |
| `docs-troubleshooting` | done | `README.md:123-146` "Troubleshooting" with three remediations. |
| `docs-use-cases` | done | `README.md:147-163` "Use cases" (seasonal warmup, overnight quiet, PV-surplus, frost protection). |
| `dynamic-devices` | exempt | Each config entry corresponds to exactly one heat pump. `config_flow.py:102` sets `unique_id = device_id`; the coordinator manages exactly one device. |
| `entity-category` | done | Diagnostic sensors set `EntityCategory.DIAGNOSTIC` (sensor.py:92/103/114/124/134/144/154/164/174/184/192/202; binary_sensor.py:73/81/89/97/105). User-facing entities (climate, switch, target_temperature number, water_pump, compressor_running) omit a category per HA convention. |
| `entity-device-class` | done | `SensorDeviceClass.TEMPERATURE` (sensor.py:89/100/110/120/130/140), `FREQUENCY` (149/160), `ENUM` (190), `DURATION` (199); `BinarySensorDeviceClass.RUNNING` (55/65), `PROBLEM` (73/81/89/97/105); climate uses appropriate `ClimateEntityFeature` bits. sensor.py:67-85 explicitly documents why `temperature_delta` omits `TEMPERATURE` (HA's delta-unit-conversion bug). |
| `entity-disabled-by-default` | done | sensor.py:153/173/183 disable `target_frequency`/`eev_steps`/`fan_speed` by default (rarely useful in dashboards). |
| `entity-translations` | done | Every platform sets `translation_key` on EntityDescription (binary_sensor.py:54/64/71/79/87/95/103, climate.py:64, sensor.py:76/.../198, switch.py:37, number.py:60, select.py:101/162). `strings.json:71-150` + `translations/de.json` + `translations/en.json` supply names. |
| `exception-translations` | done | All raised `HomeAssistantError`/`ServiceValidationError`/`ir.async_create_issue` use `translation_domain=DOMAIN, translation_key=…` (entity.py:42-51 unified `_write_dps` helper; climate.py; number.py; select.py; coordinator.py:221). `strings.json:194-213` supplies the messages. `tests/test_climate.py:130` + `tests/test_select.py:246` assert `exc.value.translation_key == "preset_not_available_in_auto"`. |
| `icon-translations` | done | `icons.json:1-60` supplies icons for every entity type with translation keys; no hardcoded `mdi:` in platform code. |
| `reconfiguration-flow` | done | `config_flow.py:142-162` `async_step_reconfigure` validates device_id match + credentials, then `async_update_reload_and_abort`. `tests/test_config_flow.py:112/131/142` cover happy/mismatch/validation. |
| `repair-issues` | done | `coordinator.py:196-224` `_reconcile_fault_issues` creates/deletes `ir.async_create_issue` / `ir.async_delete_issue` per fault bit; `strings.json:152-192` provides per-fault titles + descriptions. `tests/test_repair_issues.py` has 9 tests covering create/clear/severity/push+poll symmetry. |
| `stale-devices` | exempt | One entry == one device; removing the entry removes the device. |

### Platinum

| Rule | Status | Evidence |
|---|---|---|
| `async-dependency` | done | `grep -rE 'executor\|to_thread\|run_in_executor\|async_add_executor_job' custom_components/poolex_silverline/ pysilverline/src/` returns zero hits. The library is fully asyncio-native: `asyncio.open_connection` (client.py:91), `asyncio.create_task`, `asyncio.DatagramProtocol` (discovery.py:124-138). |
| `inject-websession` | exempt | `grep -rE 'aiohttp\|ClientSession\|async_get_clientsession' custom_components/poolex_silverline/ pysilverline/src/` returns only the literal mention inside `quality_scale.yaml:107-117` (the exemption comment). The transport is a raw TCP socket with device-scoped AES-128-ECB cipher state keyed on the device's `local_key` plus a monotonic sequence counter (client.py:56-64). There is no `aiohttp.ClientSession` anywhere — for discovery, OTA, cloud, or any other purpose — so the exemption is structurally true: there is nothing to inject. |
| `strict-typing` | done | Library ships `py.typed` (`pysilverline/src/pysilverline/py.typed`, included in built wheel — gate check #8 confirms). Both packages set `[tool.mypy] strict = true`: `pyproject.toml:36-44` (integration) and `pysilverline/pyproject.toml:39-43` (library). `mypy --strict` returns "no issues found" on both. |

---

## 3. Platinum trio — explicit evidence

- **async-dependency**: zero executor/to_thread hits. Library uses
  `asyncio.open_connection` (client.py:91) and `asyncio.DatagramProtocol`
  (discovery.py:124). The coordinator never wraps a sync call.
- **inject-websession**: zero `aiohttp`/`ClientSession` imports across both
  packages. Transport is raw TCP with per-device AES state — no session to
  inject. Exemption is genuine.
- **strict-typing**: `py.typed` marker shipped (and verified inside the
  built wheel by gate check #8). Both packages mypy-strict clean. No
  `# type: ignore` without a specific `[code]` + inline reason; no `cast()`
  papering over weak design.

---

## 4. Reviewer sign-offs (round 3 — convergent)

### `ha-idiom-reviewer` — **PASS**
3 polish (none blocking): diagnostics `device_info` block payload is sparse
post-DeviceInfo deletion; coordinator `_handle_connection_change(False)`
reaches under the DataUpdateCoordinator API; discovery listener's
swallow-and-die pattern.

### `kiss-architect` — **PASS**
8 polish (all carry-overs, none blocking): monolithic test files >400 LOC
(test_climate.py 620, pysilverline test_client_more.py 1023, test_client.py
903); `client._reconnect_loop` ~60 LOC; `util.compute_hvac_action` 59 LOC;
`_decode_fault` lossy enum; lazy `from .util import compute_hvac_action`;
borderline `_HVAC_MODE_TO_OPMODE` 4-entry table.

### `typing-reviewer` — **PASS**
5 polish: `discovery.py:104 parsed: Any`; HA-forced `**kwargs: Any` in
`async_set_temperature`; could `Final` the `coordinator.device_id` annotation;
`_HVAC_MODE_TO_OPMODE` value could narrow to `Literal`; could promote `dp value`
union to a module-level `type` alias.

### `test-integrity-reviewer` — **PASS**
5 polish: untested "no ATTR_TEMPERATURE" branch in `async_set_temperature`;
`coordinator.data is None` hvac_action not asserted; no direct unit tests for
new `util.derive_*` helpers (covered transitively); discovery
`title_placeholders` not asserted; Repair issue `learn_more_url` not asserted.

### `quality-scale-auditor` — **PASS**
1 polish: `devices` rule omits `sw_version` because Tuya v3.3 doesn't expose
firmware (omission is correct per the rule spec). Full per-rule evidence
block consumed into §2 of this report.

### `review-auditor` — **PASS**
- No verdicts overturned.
- Guard files untouched: `git diff HEAD -- scripts/platinum-gate.sh
  .github/workflows/ pyproject.toml pysilverline/pyproject.toml` = 0 lines.
- Exemption honesty: `inject-websession` grep returns zero session imports;
  `discovery` `SOURCE_INTEGRATION_DISCOVERY` path wired + regression-tested.
- Coverage/typing theater: zero new skip/xfail/pragma-no-cover/blanket
  type-ignore in the 23-file diff.
- Scope honesty: round-3 incremental delta matches the implementer's
  changelog exactly (no unrelated drive-by edits).
- Thrash watch (informational, not blocking): three monolithic test files
  carried POLISH across 3 rounds without movement — accepted as out-of-scope
  for the Platinum bar.

---

## 5. Round log (open-finding count trends to zero)

| Round | Gate | Panel | Auditor | Open findings (blockers) |
|---|---|---|---|---|
| 1 | GREEN (15/0/1) | 3 PASS / 2 BLOCK (kiss + test) | deferred | 9 → addressed |
| 2 | GREEN (15/0/1) | 4 PASS / 1 BLOCK (kiss, consolidation fallout) | deferred | 2 → addressed |
| 3 | GREEN (15/0/1) | **5 PASS / 0 BLOCK** | **PASS** | **0** |

Round 1 blockers (closed): `_write` quadruplicate → `entity._write_dps`;
preset/hvac-mode derivation triplicate → `util.derive_*`; dead `dp_keys` attrs
in select; unused `MODE_*`/`ALL_MODES`/`TEMP_MIN`/`TEMP_MAX` in
pysilverline.const; single-caller `client.get_device_info()` inlined; two
`_mode_temp_range` impls → `util.mode_temp_range`; +3 test integrity blockers
(SilverlineError → UpdateFailed path coverage; `preset_not_available_in_auto`
translation_key assertions in climate + select tests).

Round 2 blockers (closed): `PRESET_NONE` triplicate consolidated into
`const.py`; zero-caller `pysilverline.models.DeviceInfo` dataclass deleted;
coordinator now exposes `self.device_id: str` directly.

Round 3: convergent.

Guard-file diff across all rounds = **0 lines**.

---

## 6. Honesty section — uncertain / pending

- **HACS validator**: pending the GitHub Actions `hacs.yaml` run on the
  next push to `main`. Local container needs a `GITHUB_TOKEN` to reach the
  GitHub API; per orchestrator decision the bundled refactor is being
  pushed to main so CI authoritative validation runs. **DONE conditional
  on that CI being green.**
- **Polish items**: each reviewer carried polish lists (cosmetic/typing
  width / test-strengthening / one diagnostics ergonomics nit). None are
  required by the Platinum bar; all are listed in §4 for future cleanup.
- **`sw_version`**: omitted from `DeviceInfo` because Tuya v3.3 local
  protocol genuinely cannot return a firmware string. If a future
  firmware version surfaces one (e.g. via the WBR3 OTA channel), wire it
  in then — never hardcode a stale literal.
- **`discovery: done` via UDP broadcast** is honestly outside the standard
  Core discovery channel set (zeroconf/SSDP/DHCP/Bluetooth/USB). The
  `SOURCE_INTEGRATION_DISCOVERY` plumbing is fully wired and tested, and
  the `quality_scale.yaml:55-69` comment documents this design choice for
  any future maintainer.

---

## 7. Definition-of-Done checklist

- [x] **A. Gate is GREEN** (`bash scripts/platinum-gate.sh` → 15/0/1; the
      one SKIPPED check is the local hassfest+HACS runner, NOT a hard check).
- [x] **B (partial). hassfest GREEN** — confirmed by running
      `ghcr.io/home-assistant/hassfest` directly. **HACS pending CI**.
- [x] **C. Panel verdict PASS** — 5/5 reviewers PASS in round 3.
- [x] **D. review-auditor PASS** — earned, not rubber-stamped.
- [x] **E. PLATINUM_REPORT.md exists with per-rule evidence**.

**Status: Platinum-eligible. Final DONE conditional on CI HACS green.**
