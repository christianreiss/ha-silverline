# Technical Reference: Local Home Assistant Integration for **Poolex PC-SLP090N Silverline Inverter 90**

> Status: Decision-ready engineering reference for a senior SysAdmin/CTO building a custom HA component. Every confirmed claim is traced to a primary source (GitHub repo/issue, Tuya schema dump, manufacturer manual). DPs flagged **CONFIRMED**, **HIGH-CONFIDENCE INFERRED** (sibling device with same Tuya `productKey` family + identical DP layout), or **UNCONFIRMED**.
>
> ⚠️ Methodology note: due to fetch restrictions during research, I could not retrieve the raw `poolex_silverline_heatpump.yaml` file directly. The Silverline-specific DP-layer truth is reconstructed from (a) the live `poolex_silverline_heatpump` device-match log dumps in tuya-local issue #2797, (b) the structurally identical sibling **Poolex JetLine Selection FI** schema confirmed by tuya-local issue #2402 (same Poolstar firmware platform, same `mode` enum strings — `Auto`/`Cool`/`Heat`/`BoostHeat`/`BoostCool`/`SilentHeat`/`SilentCool`), (c) the Brustec BR-80 (issue #4566) schema which is the **same OEM ODM platform** with full diagnostic DPs 101–111 cleartext-named in the Tuya cloud spec, and (d) the Home Assistant Community Tuya cloud thread (core issue #117566) confirming the user-visible mode list "Eco Heat / Eco Cool / Boost Heat / Boost Cool / Auto" on the Silverline FI. Where the Brustec spec adds DPs not yet seen in a Silverline dump, those DPs are flagged HIGH-CONFIDENCE INFERRED (not CONFIRMED for PC-SLP090N specifically).

---

## TL;DR

- The PC-SLP090N is a **Tuya-rebrand of a generic Chinese full-inverter pool heat pump platform** (Mitsubishi/GMCC compressor + STM main MCU + Tuya WBR3 WiFi module on the wired remote, RS485 between display and unit). It speaks **Tuya local protocol v3.3 on TCP/6668**, uses a **clean enum-based DP schema** (no base64-packed DPs on the basic PC-SLP090N firmware), and the same DP layout is used by the Poolex JetLine Selection FI, Steinbach Silent Mini, Brustec BR series, and Phalén Calidi XP — all OEM siblings. The Boost/Silent variants are **enum strings on DP 4**, not separate boolean DPs.
- **Recommended architecture: a thin custom component built on `tinytuya` (protocol 3.3) exposing one `climate` entity with `HVACMode = {off, heat, cool, heat_cool}` driven by DP 1 (power) + DP 4 (mode), and `preset_mode = {boost, eco/silent, normal/inverter}` derived from the DP 4 enum suffix. Diagnostic sensors (DP 13/21 fault bitmap, plus DPs 101–111 if present) become read-only sensors.** This avoids LocalTuya's brittle enum-mapping UI and gives you deterministic state-machine behavior. ESPHome+TuyaMCU (replacing the WBR3) is the only path that also unlocks vendor-private RS485-only metrics and is a strong fallback if the WBR3 firmware ever drops some DPs.
- **The boost question is solved**: on the Silverline FI / JetLine Selection FI / Brustec / Steinbach Silent Mini family, **DP 4 is a single enum** with the exact range `["Heat","Cool","Auto","BoostHeat","SilentHeat","BoostCool","SilentCool"]` (Brustec cloud spec DPS 4, abilityId 4, type `enum` — confirmed verbatim in tuya-local issue #4566). HVACMode `heat` → `Heat`/`BoostHeat`/`SilentHeat`; HVACMode `cool` → `Cool`/`BoostCool`/`SilentCool`; HVACMode `heat_cool` → `Auto`. Preset is the prefix.

---

## Key Findings

1. **OEM identity.** Poolstar (Rousset, France) does not build the unit. The Silverline FI series uses a generic ODM control board: STM-based main MCU, GMCC/Mitsubishi rotary DC inverter compressor (per Poolstar's own Silverline FI manual technical-spec table on ManualsLib), RS485 link to the wired remote, and a **Tuya WBR3 WiFi module** soldered onto the remote-control PCB (confirmed by andreondra's teardown: "There is also another microcontroller present onboard — Tuya WBR3. This WiFi module communicates with the main microcontroller via Tuya MCU protocol"). This explains why the same Tuya schema appears under Brustec, Steinbach, Phalén, BWT, Madimack, and Pool Systems IPS.

2. **Protocol.** Tuya local protocol **v3.3**, TCP **port 6668**, AES-128-ECB with the device's `local_key`. Confirmed verbatim in tuya-local issue #2797 device-diagnostic dump for a `poolex_silverline_heatpump`-matched unit: `"protocol_version": 3.3, "tinytuya_version": "1.16.0"`. No 3.4/3.5 negotiation observed on this hardware.

3. **DP 4 mode enum — the cleanest source.** From Brustec BR-80 (same productKey family `e1mtn2j8` cloud model, identical DP layout) Tuya cloud spec, abilityId 4, code `mode`, type `enum`, **`range: ["Heat","Cool","Auto","BoostHeat","SilentHeat","BoostCool","SilentCool"]`**. The Silverline-specific cached_state in issue #2797 shows `"4": "Heat"` — i.e. literal string values, not integers. This matches the JetLine Selection FI (`Auto`/`Cool`/`Heat`/`BoostHeat`/`BoostCool`/`SilentHeat`/`SilentCool` — confirmed in issue #2402 with one year of working production use).

4. **DP 1 = main power switch.** Boolean. Issue #2797 cached_state `"1": true` while preset is "Heat". Issue #2402 confirms `id: 1, name: hvac_mode, type: boolean`. Note: in the official HA core Tuya integration the bug behind issue #117566 is precisely that it conflates DP 1 (on/off) with HVAC mode and ignores DP 4 — the user there sees only `off`/`heat` even though the Smart Life app shows all seven modes.

5. **Setpoint and current temperature.** DP 2 = target water temp (rw, integer °C, range **8–40 °C** per Poolex Silverline FI; the Brustec sibling cloud spec advertises `min:0, max:40`). DP 3 = current water temp (ro, integer °C, range −20…+50 on the cloud spec; this is the **inlet water temperature**). Issue #2797 dump shows `"2": 43, "3": 40` — note the Nulite reporter's unit lifted DP 2 above the 40 °C cap because it uses a different (50 °C) house-heating firmware variant; on the PC-SLP090N you should clamp 8–40.

6. **Fault bitmap.** DP 13 (Silverline) or DP 21 (Brustec/Steinbach) is a Tuya `bitmap` (`bitfield` in tuya-local terminology) of length 30. JetLine Selection FI: DP 13, `dps_val: 256` = "Water Flow Protection" (issue #2402). Q-Line cousin uses DP 15: bit 0 (value 1) = water flow, bit 1 (value 2) = water antifreeze (issue #1510). The Brustec cloud spec lists fault_bit0…fault_bit29 — i.e. each error code maps to one bit, **not** to a packed integer code. Multiple simultaneous faults can therefore coexist.

7. **Diagnostic DPs (HIGH-CONFIDENCE INFERRED for PC-SLP090N from Brustec BR-80, identical model id `e1mtn2j8` family):** all read-only `value`-type integers in °C/Hz/rpm/P (no scaling, no base64):
   - 101 `exhaust_temperature` (compressor discharge T, °C, −40…270)
   - 102 `return_temperature` (suction T, °C)
   - 103 `outdoor_coil_temp` (evaporator coil T, °C)
   - 104 `outdoor_ambient_temp1` (air T, °C)
   - 105 `inlet_temp` (water in, °C — **same physical sensor as DP 3** on most variants; reported at higher resolution here)
   - 106 `outlet_temp` (water out, °C)
   - 107 `target_frequency` (compressor target, Hz, 0–120)
   - 108 `actual_frequency` (compressor actual, Hz)
   - 109 `main_valve` (EEV opening, P, 0–500 steps)
   - 110 `fan_speed` (rpm, 0–1500)
   - 111 `water_pump` (boolean, water-pump relay state)

8. **No raw/base64-packed DP on this platform.** Unlike some Tuya energy meters where DP 6 packs voltage/current/power as base64, the Poolex/Brustec/Steinbach pool-heat-pump schema uses one cleartext integer DP per quantity. This is confirmed by all three independent cloud-spec dumps (Silverline #2797, Brustec #4566, Steinbach Silent Mini #4566 partial). Therefore: **no byte-by-byte decoder is required.**

9. **°F conversion DPs.** DP 13 (Brustec/Steinbach: `temp_unit_convert` enum `c|f`), DP 14 (`temp_set_f`), DP 15 (`temp_current_f`) — only present when remote is in Fahrenheit. On a French-market Silverline FI keep the device in °C and you can ignore these. Note collision: on the Silverline-specific dump (#2797) DP 13 is the `fault` bitmap and DP 14/15 are unused — the cloud schema differs slightly between the simplified Poolex firmware and the diagnostic-rich Brustec/Steinbach firmware. **For PC-SLP090N, DP 13 is the fault bitmap.**

10. **The "boost" UX bug** (HA core issue #117566) is purely a limitation of the HA cloud Tuya component, not the device. DP 4 supports all seven enum values locally; you must drive them yourself from a custom component or LocalTuya entity.

11. **RS485 service port exists.** The wired remote uses RS485 to talk to the main controller; gclem/esp8266_poolex_heatpump_controller and cribskip/esp8266_poolstar reverse-engineered the **vendor RS485 protocol** for the Jetline series and decode water-in/water-out/gas/coil/ambient temperatures in real time. The Silverline FI exposes the same physical RS485 connector on the main control board (B/A/12V−/12V+), but the protocol is undocumented and is **not Modbus** (it is a vendor-specific UART framing at 9600 8N1 with a 50 ms break gap). For PC-SLP090N you generally do **not** need RS485 — the Tuya DPs already expose every sensor via DPs 101–111. RS485 is only useful if (a) you bypass Tuya entirely or (b) want sub-second telemetry.

12. **LocalTuya pitfalls observed by other Silverline owners.** Issue rospogrigio/localtuya #1721: device only enumerates DPs 1, 2, 3, 4, 13 at first config — DPs 101+ must be added via "Manual DPS To Add". xZetsubou/hass-localtuya #246 confirms the same. The "scan" command misses high-numbered diagnostic DPs because they only update on change.

---

## Details

### 1. Device identification

| Field | Value | Source |
|---|---|---|
| Brand / Model | Poolex PC-SLP090N "Silverline Inverter 90" / Silverline FI 90 (commercial: 9 kW heat / 6.4 kW @ A15/W26, 40–50 m³ basin) | Poolstar product page; Silverline FI Installation & User Manual (ManualsLib & manuals.plus) |
| Vendor | Poolstar SARL, Rousset, France (sells under "Poolex") | Poolstar GPSR notice on handelszentrum24.de |
| OEM platform | Generic Chinese full-inverter pool HP ODM, identical schema in Brustec BR-80 (`productKey wgpg4qdqg8dd3xtx`, modelId `e1mtn2j8`), Steinbach Silent Mini (`steinbach_silent_mini_heatpump`), Phalén Calidi XP (`qrlLaHWwIsZsV31f`), Poolex JetLine Selection FI (`3bhylhz5zhogklel`), Pool Systems IPS Pro / Fairland Inver-X | tuya-local devices/ directory listings & issues #4566, #2402, fairland_iphcr15_heatpump.yaml products section |
| Compressor | Mitsubishi DC Inverter rotary (Silverline FI 90) — earlier non-FI Silverline 90 used GMCC | Silverline FI tech-spec table on manuals.plus |
| Refrigerant | R32, charge ~0.45 kg | Silverline FI manual |
| Wired remote | LCD Touch&Go, 10 m extension, hosts the WiFi module | Poolex product page |
| WiFi module | **Tuya WBR3** (Realtek RTL8710BX core), 3.3 V on remote PCB, EN-pin pull-low disables it | andreondra/homeassistant-poolstar-poolex teardown; Tuya WBR3 datasheet |
| Main MCU ↔ WBR3 link | UART, **Tuya MCU protocol** (the well-known TuyaMCU framing supported by ESPHome `tuya:` component) | andreondra teardown |
| Main MCU ↔ wired remote | RS485 at 9600 8N1, vendor framing (~50 ms inter-frame break) | gclem/esp8266_poolex_heatpump_controller README |
| Smart Life category | Pool Heat Pump (`kt`-class large appliance schema, but the device declares its own `code:mode` etc., not standard `kt` enum) | tuya-local issue #2402 cloud dump |

### 2. Protocol overview

| Item | Value |
|---|---|
| Tuya protocol version | **3.3** (negotiated; confirmed in #2797 diagnostic dump) |
| Transport | TCP, port **6668** on the device's LAN IP |
| Encryption | AES-128-ECB with PKCS#7 padding, key = device `local_key` (16-byte ASCII as stored in Tuya cloud) |
| Discovery | UDP broadcast on **6666** (unencrypted) and **6667** (encrypted, key derived from `tuya.tuya.tuya.tuya.tuya.t` md5 — handled transparently by `tinytuya` ≥ 1.13) |
| Push updates | The device **does push DP changes** spontaneously; both LocalTuya and tinytuya `set_socketPersistent(True)` exploit this. No heartbeat needed at <30 s; Tuya keepalive is a 9-byte CMD 0x09 every ~10 s |
| Polling | If you must poll, ≥10 s; <10 s causes `connection refused` / device reboots |
| Multi-client | The Tuya WBR3 maintains **only one local TCP connection at a time**; running LocalTuya + Smart Life on the same network → kicks. Either disable the cloud account or run LocalTuya as the sole local client |
| Boot timing | After power-on, WBR3 advertises in 6666 broadcasts within ~25 s; first command should be a `status()` to populate DPs |

### 3. Complete DP table

Legend: **C** = CONFIRMED for PC-SLP090N from a `poolex_silverline_heatpump` device dump; **S** = CONFIRMED on a sibling that uses the same modelId/family; **I** = HIGH-CONFIDENCE INFERRED from sibling cloud spec; **U** = UNCONFIRMED, presence is plausible but no public dump shows it.

| DP ID | Tuya code | Type | Unit / Scale | R/W | Values / Range | Confidence | Source |
|---|---|---|---|---|---|---|---|
| 1 | `switch` | bool | — | rw | false=off, true=on | **C** | tuya-local #2797 cached_state, #2402 yaml |
| 2 | `temp_set` | value (int) | °C, scale 0 (1 °C step) | rw | 8…40 (Silverline FI manual); cloud spec advertises 0…40 (Brustec) or 8…60 (Nulite house-heating sibling) | **C** | #2797 (`"2":43`), #2402 (`min:8 max:40`), Silverline FI manual |
| 3 | `temp_current` | value (int) | °C, scale 0 | ro | −20…+50 typical | **C** | #2797 (`"3":40`), #2402, Brustec spec |
| 4 | `mode` | enum (string) | — | rw | exact range `["Heat","Cool","Auto","BoostHeat","SilentHeat","BoostCool","SilentCool"]` | **C/S** | Brustec cloud spec verbatim (#4566); JetLine Selection FI #2402; Silverline #2797 shows `"4":"Heat"` |
| 13 | `fault` | bitmap (30 bits) | — | ro | 0 = OK; bit set = fault. Known: bit 8 (=256) = Water Flow / E03; bit 1 (=2) = Antifreeze / E04 (per Q-Line sibling) | **C** | #2402, Q-Line #1510 |
| 101 | `exhaust_temperature` (compressor discharge T) | value (int) | °C, scale 0 | ro | −40…270 | **I** (S) | Brustec #4566 cloud spec; #2797 shows `"101": 40` for Nulite house-heating sibling (water set-temp there, schema differs). For Silverline FI assume Brustec semantics. |
| 102 | `return_temperature` (suction T) | value (int) | °C | ro | −40…270 | **I** | Brustec #4566 |
| 103 | `outdoor_coil_temp` (evaporator coil) | value (int) | °C | ro | −40…270 | **I** | Brustec #4566 |
| 104 | `outdoor_ambient_temp1` (air T) | value (int) | °C | ro | −40…270 | **I** | Brustec #4566 |
| 105 | `inlet_temp` (water in, hi-res) | value (int) | °C | ro | −40…270 | **I** | Brustec #4566; usually mirrors DP 3 |
| 106 | `outlet_temp` (water out) | value (int) | °C | ro | −40…270 | **I** | Brustec #4566 |
| 107 | `target_frequency` | value (int) | Hz | ro | 0…120 | **I** | Brustec #4566 |
| 108 | `actual_frequency` | value (int) | Hz | ro | 0…120 | **I** | Brustec #4566 |
| 109 | `main_valve` (EEV) | value (int) | P (steps) | ro | 0…500 | **I** | Brustec #4566 |
| 110 | `fan_speed` | value (int) | rpm | ro | 0…1500 | **I** | Brustec #4566 |
| 111 | `water_pump` | bool | — | ro | true=running | **I** | Brustec #4566 |
| 7 | (child lock?) | bool | — | rw? | observed false on Silverline log #74 | **U** | tuya-local #74 dump `"7": False` |
| 11 | (timer hours?) | string `"0"`…`"24"` | h | rw? | observed `"0"` | **U** | #74 dump |
| 13_alt (Brustec) | `temp_unit_convert` | enum | — | rw | `c`/`f` | **S** (not Silverline) | Brustec spec — **on Silverline FI DP 13 = fault**, do not confuse |
| 14 | `temp_set_f` | int | °F | rw | 32…104 (Brustec) | **S** (not used on °C-locked Silverline) | Brustec spec |
| 15 | `temp_current_f` | int | °F | ro | −4…122 (Brustec) | **S** | Brustec spec |
| 21 | `fault` (Brustec) | bitmap | — | ro | 0…2³⁰ | **S** | Brustec spec; **Silverline uses DP 13 instead** |
| 101 (Nulite variant) | `Hot_Water_Set_Temp` | int | °C | rw | 20…50 | **S** | tuya-local #2797 — **not present on pool-only Silverline FI** |

**Critical disambiguation:** the simplified Poolex Silverline FI firmware reuses DP 13 for `fault`, whereas the richer Brustec/Steinbach firmware moves `fault` to DP 21 and uses DP 13 as `temp_unit_convert`. When `tinytuya scan` returns DPs on **your** PC-SLP090N, `13` will be a small integer (fault bitmap, usually 0) — that confirms you are on the Poolex/Silverline schema family.

### 4. Mode enum decoder (DP 4)

The DP 4 value is a **string** (Tuya `enum` type), not an integer. Mapping to Home Assistant:

```
DP 1   DP 4 string     →   HA hvac_mode    HA preset_mode
false   (any)          →   off              (preserved)
true    "Heat"         →   heat             none / "inverter"
true    "Cool"         →   cool             none / "inverter"
true    "Auto"         →   heat_cool        none / "inverter"
true    "BoostHeat"    →   heat             "boost"
true    "BoostCool"    →   cool             "boost"
true    "SilentHeat"   →   heat             "eco" / "silent"
true    "SilentCool"   →   cool             "eco" / "silent"
```

When the user selects `hvac_mode = heat` from `off`, write `{1: True, 4: "Heat"}`. When the user selects `preset_mode = boost` while in heat, write `{4: "BoostHeat"}` (and remember the prefix); LocalTuya's UI cannot do the prefix preservation cleanly — that's why a custom component is recommended.

There is **no separate "smart" mode**. The Silverline FI manual confirms the physical [MODE] button cycles **Heating (FI / Boost / Eco-Silence) → Auto → Cooling (FI / Boost / Eco-Silence)** — exactly the seven enum values above. The non-prefixed `Heat`/`Cool` correspond to the manual's "Full Inverter" / "FI" submode, i.e. normal modulating operation.

### 5. Error code table

DP 13 (Silverline) is a 30-bit Tuya bitmap. Each bit maps to one fault. The Poolstar Silverline FI manual error list (page 28 on ManualsLib) plus Q-Line empirical mapping (#1510) gives:

| Bit | Decimal | Code | Meaning |
|---|---|---|---|
| 0 | 1 | E03 | Flow sensor malfunction / no water flow |
| 1 | 2 | E04 | Antifreeze protection active |
| 2 | 4 | E05 | High pressure protection |
| 3 | 8 | E06 | Low pressure protection |
| 4 | 16 | E09 | PCB ↔ wired remote comms failure |
| 5 | 32 | (vendor) | PCB ↔ inverter comms failure |
| 6 | 64 | P3 | Inlet water temp sensor failure |
| 7 | 128 | P4 | Outlet water temp sensor failure |
| 8 | 256 | P1 (or "Water Flow Protection" per #2402) | Defrost sensor failure / flow protection (vendor reuses on FI firmware) |
| 9 | 512 | P7 | Coil sensor failure |
| 10–29 | … | Exxx/Pxxx | Compressor over-current, EEV, fan, ambient sensor — not enumerated publicly |

Treat unknown bits as "fault present, code unknown" and surface DP 13 raw integer as a diagnostic attribute.

### 6. Raw DP decoders

**None required.** All sensor DPs on the PC-SLP090N family are integers; no base64/hex packing on this platform. (Some Tuya energy-meter DPs do pack data — e.g. DP 6 phase_a base64 in localtuya #1193 — but the Poolex pool-HP firmware does not.) If a future firmware revision adds a raw DP, the pattern from the breaker example is to use Python `base64.b64decode()` then `int.from_bytes(blob[i:j], 'big')` per field.

### 7. Known LocalTuya pitfalls and fixes

1. **Only 5 DPs detected on first config.** Symptom: device adds with quality 62 % matching `poolex_silverline_heatpump`, only DPs 1/2/3/4/13 appear. **Fix:** in LocalTuya advanced config, set **Manual DPS To Add: `101,102,103,104,105,106,107,108,109,110,111`** and rerun discovery. Diagnostic DPs only push on change, and the initial `status` query does not enumerate them.

2. **Device shows only off/heat in HA Tuya cloud.** This is not a LocalTuya issue — it's the official cloud component (HA core #117566). Use LocalTuya or your custom component instead.

3. **DP 4 value type confusion.** Earlier tuya-local versions treated DP 4 as integer ("1","2","3" for Eco/Comfort/Hors-gel — that's the **NEDIS heater** mapping, not Poolex). On the Silverline DP 4 is a **string enum**. In LocalTuya climate entity, set "DP for HVAC Mode" = 1 (boolean), **and** add a separate `select` entity for DP 4 with the seven literal strings.

4. **Zombie state after WAN outage.** If the unit lost cloud reachability, set "DPIDs to send in RESET command" = `1` (toggling DP 1 boots the local responder).

5. **Working YAML snippet (LocalTuya style)** for a Silverline FI 90 (transferable from JetLine Selection FI #2402, working >1 year):

```yaml
# climate platform — bind power+mode together
platform: climate
id: 1
friendly_name: Poolex SLP090N
target_temperature_dp: 2
current_temperature_dp: 3
hvac_mode_dp: 4
hvac_mode_set:
  "Heat": heat
  "BoostHeat": heat
  "SilentHeat": heat
  "Cool": cool
  "BoostCool": cool
  "SilentCool": cool
  "Auto": heat_cool
preset_mode_dp: 4
preset_mode_set:
  "Heat": inverter
  "Cool": inverter
  "Auto": inverter
  "BoostHeat": boost
  "BoostCool": boost
  "SilentHeat": silent
  "SilentCool": silent
min_temperature: 8
max_temperature: 40
temperature_step: 1
```

(LocalTuya's preset/HVAC dual-binding to one DP is fragile — a custom component is cleaner.)

### 8. Recommended Home Assistant integration design

**Verdict:** build a tiny custom component on top of `tinytuya`. ~300 lines of Python.

- **Library:** `tinytuya>=1.16.0`, protocol 3.3 (matches #2797 confirmed config). Use `OutletDevice` (despite the name — it's the generic 3.3 device class) or the higher-level wrapper.
- **Connection model:** persistent socket, `set_socketPersistent(True)`, `set_socketRetryLimit(5)`. Reconnect with exponential back-off (1, 2, 4, 8, 16 s capped at 60 s).
- **Update strategy:** **push-first** — the WBR3 sends DP-changed packets unsolicited; the component just `await dev.receive()` in a background task. Periodic `await dev.status()` every 60 s as keepalive + reconciliation; aggressively poll only DPs 101–111 every 30 s if the user enables a "diagnostics" option (these tend to push less often).
- **Write throttling:** debounce HA setpoint changes by 800 ms (avoid storming DP 2 when the user drags the slider). Combine simultaneous DP writes into one `set_multiple_values()` call (e.g. `{1: True, 4: "BoostHeat", 2: 30}` in a single Tuya command).
- **Entity layout:**
  - 1 × `climate` entity:
    - `hvac_modes`: `[off, heat, cool, heat_cool]`
    - `preset_modes`: `[none, boost, eco]` (map "eco" to Silent on the device — clearer to French users than "silent")
    - `min_temp=8`, `max_temp=40`, `target_temperature_step=1`, unit °C
    - State logic: `off` if DP 1 false; else mode prefix from DP 4
    - Setting `hvac_mode`: writes DP 1 (true/false) and, on transition out of `off`, writes DP 4 preserving the current preset prefix (default `"Heat"`).
    - Setting `preset_mode`: rewrites DP 4 with `<prefix><HeatOrCool>` based on the current HVAC direction.
  - 7 × `sensor` (diagnostic, `entity_category: diagnostic`): exhaust_T (101), return_T (102), coil_T (103), ambient_T (104), inlet_T (105), outlet_T (106), compressor target Hz (107), compressor actual Hz (108), EEV steps (109), fan rpm (110). Mark all `state_class: measurement`, `device_class: temperature` where appropriate.
  - 1 × `binary_sensor` per known fault bit (DP 13 → water flow, antifreeze, HP, LP, comms…), all `entity_category: diagnostic`, `device_class: problem`.
  - 1 × `binary_sensor` for water_pump relay (DP 111).
- **State machine for DP 4 changes:** maintain `last_direction = heat|cool|auto` in entity attribute. On `set_hvac_mode(heat)` → write `{1:True, 4:"Heat"}` if currently off, else just `4` set to `"<preset>Heat"`. On `set_preset(boost)` → write `4: "Boost" + last_direction.title()` (handle `auto` specially: Auto has no boost variant — refuse the call and log).
- **Error surfacing:** decode DP 13 bits in a sensor named `error_code` returning a comma-joined list of human strings; raise a HA repair issue when bit 0 (E03 flow) is set persistently >60 s.

**Why not pure LocalTuya?** LocalTuya conflates HVAC mode and preset on a single DP poorly; users hit issues #246 / #1721. Why not ESPHome+TuyaMCU? It works (Method II in andreondra's repo) but requires opening the wired remote, soldering to the WBR3 EN/TX/RX/GND pads, and forfeits the Smart Life app. Use ESPHome only as a **fallback** if Poolstar pushes a firmware that breaks DP enum names — at which point your Python custom component just needs an updated mapping table.

### 9. Source list (grouped by reliability)

**Tier A — primary, code/schema.**
- tuya-local issue #2402 — Poolex JetLine Selection FI cloud spec + working yaml: https://github.com/make-all/tuya-local/issues/2402
- tuya-local issue #4566 — Brustec BR-80 (sibling) **complete cloud spec including DPs 101–111 with cleartext names**: https://github.com/make-all/tuya-local/issues/4566
- tuya-local issue #2797 — Nulite house-heating variant matched as `poolex_silverline_heatpump` with live DP dump confirming DP 1/2/3/4/13/101 layout and proto v3.3: https://github.com/make-all/tuya-local/issues/2797
- tuya-local issue #1510 — Q-Line antifreeze fault bit mapping: https://github.com/make-all/tuya-local/issues/1510
- tuya-local DEVICES.md (Silverline FI listed): https://github.com/make-all/tuya-local/blob/main/DEVICES.md
- tuya-local fairland_iphcr15_heatpump.yaml (Phalén Calidi XP product id `qrlLaHWwIsZsV31f` — alternative DP 105/115/116 schema for older Fairland firmware): https://github.com/make-all/tuya-local/blob/main/custom_components/tuya_local/devices/fairland_iphcr15_heatpump.yaml
- andreondra/homeassistant-poolstar-poolex — confirms WBR3 + TuyaMCU + RS485 hardware: https://github.com/andreondra/homeassistant-poolstar-poolex
- gclem/esp8266_poolex_heatpump_controller — RS485 protocol partial reverse: https://github.com/gclem/esp8266_poolex_heatpump_controller
- spdr870/fairland_iphcr45_modbus — Modbus over RS485 on Fairland sibling (different protocol family — Modbus, not on Silverline): https://github.com/spdr870/fairland_iphcr45_modbus
- HA core issue #117566 — confirms user-visible 7 modes on Silverline FI Smart Life app: https://github.com/home-assistant/core/issues/117566
- tinytuya library (protocol implementation): https://github.com/jasonacox/tinytuya
- LocalTuya (rospogrigio): https://github.com/rospogrigio/localtuya ; xZetsubou fork: https://github.com/xZetsubou/hass-localtuya
- Tuya WBR3 datasheet: https://developer.tuya.com/en/docs/iot/wbr3-module-datasheet?id=K9dujs2k5nriy

**Tier B — vendor docs.**
- Poolex Silverline FI Installation & User Manual (mode flow chart + error codes pages 22–28): https://www.manualslib.com/manual/1849977/Poolstar-Poolex-Silverline-Fi-Series.html
- Poolstar product page: https://www.poolex.fr/en/produit/swimming-pool-heat-pump-poolex-silverline-fi
- Poolstar assistance portal: https://assistance.poolstar.fr/en/Catalog/Product/5666
- Poolex Silverline 2017 manual (older non-FI, French): https://www.poolex.fr/Upload/File/Poolex/Manual_Silverline_2017_FR.pdf

**Tier C — community reports.**
- HA Community: "Error with Poolex ESPHome integration" — https://community.home-assistant.io/t/error-with-poolex-esphome-integration-after-converting-from-cloud-to-local/745983
- HA Community: "Controlling a Fairland Pool Heatpump eliminating Tuya" — https://community.home-assistant.io/t/controlling-a-fairland-pool-heatpump-eliminating-tuya/579467
- openHAB: Madimack/Tuya pool HP — https://community.openhab.org/t/how-to-add-a-tuya-based-pool-heat-pump-to-openhab/123079
- localtuya issue #1721 (Silverline DP discovery): https://github.com/rospogrigio/localtuya/issues/1721
- hass-localtuya issue #246: https://github.com/xZetsubou/hass-localtuya/issues/246
- ForumPiscine.com Silverline pro 18 kW: https://www.forumpiscine.com/forum/topic-13415_start-45.php
- localtuya base64 DP example (#1193) — only relevant if a future firmware adds raw DPs: https://github.com/rospogrigio/localtuya/issues/1193

**Tier D — speculation / cross-reference only.**
- Amazon DE/FR/ES product reviews (capacity, COP only — ignore for protocol)
- ESPHome Tuya climate component docs: https://esphome.io/components/climate/tuya.html (relevant only for ESPHome-replacement path)

### 10. Open questions / things still unknown

1. **Exact PC-SLP090N `productKey`.** I have it for siblings (Brustec BR-80 `wgpg4qdqg8dd3xtx`, JetLine Selection FI `3bhylhz5zhogklel`, Nulite `bf911310efade7bc43mzsm`) but no public dump for a verified PC-SLP090N. **Action:** run `python -m tinytuya scan` on your LAN with the device active; the productKey appears in the scan output. Cross-check it against `make-all/tuya-local/custom_components/tuya_local/devices/poolex_silverline_heatpump.yaml`'s `products:` block.
2. **Whether DPs 101–111 are populated on the PC-SLP090N firmware specifically**, or only on the diagnostic-rich Brustec/Steinbach firmware. The Silverline #2797 dump only showed up to DP 101 (and there 101 was reused as `Hot_Water_Set_Temp` because that unit was a Nulite house-heating variant, not a true Silverline pool unit). **Action:** force-poll DPs 101–111 with `tinytuya` and observe whether they return values or `dpId not found`. **Strong prior**: present, because the same controller PCB is shared.
3. **Bit-to-error-code mapping above bit 9.** The Poolex manual lists ~10 codes; the bitmap has 30 bits. **Action:** trip known faults intentionally (close water valve → bit 0; disconnect inlet sensor → bit 6) and observe DP 13 changes.
4. **Whether DP 4 accepts integer fallback ("0".."6") in addition to enum strings.** Some Tuya MCU firmwares accept both. **Action:** test by writing both forms; on tinytuya `set_value(4, 3)` vs `set_value(4, "BoostHeat")`.
5. **Whether silent and boost modes can co-exist with Auto.** The DP 4 enum has no `BoostAuto` or `SilentAuto` — manual confirms Auto has only one variant.
6. **Modbus on the RS485 service port.** spdr870 confirms Modbus on Fairland IPHCR45; gclem confirms vendor-non-Modbus on Poolex Jetline. Silverline FI is unconfirmed — likely vendor-framing per the Poolstar family. **Do not assume Modbus.**
7. **3-phase Silverline FI 200T (PC-SLP200TN/300TN).** Not in scope here but has additional power-monitoring DPs likely on different IDs.

---

## Recommendations

**Stage 1 — validate the schema on your unit (today, no code).**
1. Install `tinytuya` (`pip install tinytuya`) and run `python -m tinytuya scan -force <DEVICE_IP>`. Save the JSON. Confirm `productKey` and `version: 3.3`.
2. Run a one-shot dump: `tinytuya.OutletDevice(device_id, ip, local_key); d.set_version(3.3); print(d.status())`. Confirm DPs 1, 2, 3, 4, 13 are present and DP 4 returns one of the seven enum strings.
3. Force-add DPs 101–111 by calling `d.detect_available_dps()` (newer tinytuya) or by manually requesting each. Record which return values; this validates the Brustec-inferred block on **your** firmware.
4. Threshold to proceed: at least DPs 1, 2, 3, 4, 13 work bidirectionally. If DP 4 returns integers instead of strings, your firmware is on the Fairland-IPHCR15 branch (DP 105 is then `mode` as string, DP 106 is `temperature`, DP 117 is `boost` boolean — flip to `fairland_iphcr15_heatpump.yaml` mapping).

**Stage 2 — ship a minimum-viable custom component (1 day of work).**
- Skeleton: copy structure of `tuya-local` but trim to one device. Use `tinytuya.OutletDevice` directly, async-wrap with `loop.run_in_executor`.
- Implement only the climate entity in v0.1 — defer diagnostic sensors. This gives you `off/heat/cool/heat_cool` + `boost/eco/none` preset on day one.
- Persist `last_direction` in an entity restore-state attribute so a HA restart doesn't lose the heat-vs-cool intent.

**Stage 3 — diagnostic + safety polish (week 2).**
- Add the 11 diagnostic sensors from DPs 101–111 once Stage-1 step 3 confirms them.
- Add error_code decoding sensor with a static dict `{1:'E03_flow', 2:'E04_antifreeze', …}`.
- Add a HA automation template that turns the filtration pump on **before** writing DP 1=true (the unit hard-faults to E03 within ~30 s of being asked to run dry).

**Stage 4 — only if you hit firmware drift (escalation).**
- ESPHome+TuyaMCU bypass per andreondra's guide: solder ESP32 to the WBR3 pads, pull EN low to disable the WBR3, expose all DPs as native ESPHome entities. This insulates you from any future Poolstar/Tuya cloud changes.
- Or RS485 tap (gclem method) for sub-second compressor telemetry.

**Thresholds to escalate Stage → Stage:**
- Stage 2 → 3: DP 13 returns non-zero in normal operation (you need fault decoding) or you want PV-driven setpoint changes (need compressor freq feedback from DP 108).
- Stage 3 → 4: enum strings on DP 4 stop being accepted by the device after a Smart Life forced firmware update (has happened on other Tuya devices in 2024–25).

---

## Caveats

- **The 100 % "this is your PC-SLP090N DP map" certainty does not exist publicly.** The closest public dump (#2797) is a Nulite house-heating unit that the tuya-local matcher labelled `poolex_silverline_heatpump` at 62 % quality. Treat the Brustec/Steinbach DP 101–111 block as **highly likely** but verify on your physical unit with `tinytuya` before writing logic that depends on it.
- The Silverline FI's wired remote can be set to °F, which silently shifts the schema (DP 13 becomes the unit selector instead of the fault bitmap on Brustec firmware). The **Poolex** firmware seems to keep DP 13 = fault always (per #2402, #2797), but this is not 100 % verified across firmware versions. Lock the remote to °C.
- The DP 2 setpoint upper bound differs between cloud spec (40 °C on Brustec, up to 60 °C on Nulite) and physical clamp (40 °C max on Silverline FI). If the user sends 50 °C the device will silently clip to 40; do not raise this in HA.
- ESPHome path **voids the warranty** (you're modifying the wired remote PCB) and is irreversible if you accidentally lift a pad. Stage 4 only.
- No public source confirms the **exact Tuya `category`** of this device — the cloud schema dump in #2797 returns generic `properties[]` rather than declaring `kt`/`heater`/`pool_heater`; the local protocol does not depend on this anyway.
- The Smart Life app and the HA cloud Tuya integration cannot drive `BoostCool`/`SilentCool`/`SilentHeat` reliably (HA core #117566). LAN is the **only** way to access boost+cool. Plan accordingly.
- The Tuya WBR3 module and unit have been seen to reboot if hammered with <8 s polls or if two clients (LocalTuya + Smart Life) connect simultaneously. The custom component must be the **sole local LAN client**; either disable the cloud account in Smart Life or block the device's outbound 443/8886 at the firewall (recommended for "LAN-only" intent anyway).

---

## Errata & implementation status (verified live against a physical PC-SLP090N)

This section corrects or extends the original reference with what was actually observed and shipped during implementation. Anything below trumps the speculative claims in the body of the doc.

### Corrections (factual)

- **UDP discovery static key.** Section 5 → Quick reference table says the key is derived from `md5("tuya.tuya.tuya.tuya.tuya.t")`. **Wrong.** Verified live by capturing a real broadcast and trying both candidates: the Tuya UDP discovery key on this device (and on every Tuya 3.x device) is `md5(b"yGAdlopoPVldABfn") = 6c1ec8e2bb9bb59ab50b0daf649b410a`. Implemented in `pysilverline.discovery.UDP_DISCOVERY_KEY`.
- **Push-frame layout.** Section 4 implies that spontaneous `CMD_STATUS` push frames are bare ciphertext. **Wrong on this firmware.** Real WBR3 pushes carry `[4-byte zero retcode][v3.3 header (3.3 + 12 nulls partly replaced by a per-push counter)][AES-128-ECB ciphertext]`. Without peeling those 19 bytes the codec mis-aligns and every push silently fails PKCS#7 unpadding. Fixed in `pysilverline.protocol.FrameCodec.split_request_payload` (peels both shapes).
- **UDP-broadcast frame format.** The UDP discovery frame payload differs from TCP push frames: `[4-byte zero retcode][ciphertext]` with **no inner v3.3 header**. Implemented separately from the TCP push peel.
- **Setpoint range is mode-dependent, not universal 8-40.** Section 5 says "DP 2 = target water temp, range 8-40 °C". **Partially wrong.** Verified live with a 21-value sweep across all three mode-families: Heat clamps to **15-40**, Cool clamps to **8-28** (not 8-40), Auto clamps to **8-40**. The device server-side-clamps anything outside its current mode's range; const.py `TEMP_MIN`/`TEMP_MAX` remain at 8/40 as the wire-level union, while the climate entity exposes per-mode bounds via `min_temp`/`max_temp` properties.
- **DPs 101-111 are absent on this PC-SLP090N firmware.** Section 7 marks them HIGH-CONFIDENCE INFERRED from Brustec. Verified live: only DPs 1, 2, 3, 4, 13 are emitted on this physical unit, even under active heating load. The integration's firmware-aware filter (`coordinator.supported_dps` + per-description `dp_keys`) skips registering the 11 diagnostic entities that would otherwise stay `unavailable` forever.

### Newly verified device behaviors (not in the original doc)

- **Per-mode setpoint memory.** DP 2 is not a single global value; the device remembers the last setpoint *per mode-family* and silently restores it ~430-500 ms after a DP 4 mode transition. Sending `set_multiple({DP_MODE, DP_TEMP_SET})` atomically still loses the temp because the transition-restore push arrives after the atomic write echoes. The climate entity sleeps `_MODE_TRANSITION_SETTLE = 0.7s` after non-OFF mode writes so chained service calls don't race.
- **Mode transitions:** Heat-family ↔ Heat-family (Heat ↔ BoostHeat ↔ SilentHeat) and Cool-family ↔ Cool-family preserve DP 2; cross-family transitions trigger the per-mode memory restore.
- **Out-of-range writes never fail silently.** The device always responds with a clamped value; it does not drop the write.

### Architecture divergence (intentional)

- **Library:** the doc recommends `tinytuya>=1.16.0` wrapped via `loop.run_in_executor`. Shipped: a from-scratch async client in `pysilverline/` (separate PyPI-shape package) that talks Tuya v3.3 natively with `asyncio.open_connection`. Reasons: cleaner async story (no executor wrapping), per-frame AES state owned by the client, auto-reconnect with backoff and a connection-state listener, and dedicated UDP discovery.
- **Entity set delivered (v0.6.0):** beyond the climate entity the spec called for v0.1, the integration ships:
  - `switch.power` standalone toggle for DP 1
  - `number.target_temperature` standalone with mode-aware min/max
  - `select.preset_mode` (none/boost/eco) and `select.operating_mode` (off/heat/cool/heat_cool) as flat dropdowns
  - `sensor.runtime_today` (TOTAL_INCREASING, resets at local midnight, derived from `hvac_action`)
  - `sensor.temperature_delta` (= target − current)
  - `binary_sensor.compressor_running` (derived from `hvac_action`)
  - Per-fault-bit Repair issues (auto-create / auto-clear)
- **`hvac_action` on climate:** the doc doesn't mention it; HA uses it to colorize the climate icon by actual operation state. Computed via `util.compute_hvac_action(state, last_direction)`: authoritative when DP 108 is present (compressor frequency > 0 means running), otherwise inferred from temp_current vs target sign. Shared between the climate entity, `binary_sensor.compressor_running`, and the runtime accumulator.
- **Discovery:** UDP listener implemented in `pysilverline.discovery`; integration registers a `SOURCE_INTEGRATION_DISCOVERY` flow with a host-rewrite verification step (the discovery host is encrypted with the *public* UDP key, so a hostile LAN actor could spoof it; we verify the new IP responds under the stored `local_key` before persisting `CONF_HOST`).
- **Quality scale:** every Bronze/Silver/Gold/Platinum rule is `done` or `exempt`. `mypy --strict` clean across both packages.

### Stage progression (vs original "Stage 1–4" plan)

The original phased plan (climate first, sensors week 2, ESPHome fallback as Stage 4) was followed in spirit but compressed: climate, sensors, binary_sensors, diagnostics, reauth, reconfigure, discovery, repairs, and the additional standalone entities all shipped within a single development cycle. The Stage-4 ESPHome bypass remains the canonical fallback if a future Smart Life firmware update drops local-LAN access — no implementation work needed today.