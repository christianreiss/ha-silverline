"""Constants for the Tuya v3.3 protocol and Poolex Silverline DPs."""

from __future__ import annotations

from typing import Final

DEFAULT_PORT: Final = 6668
DISCOVERY_PORT_PLAIN: Final = 6666
DISCOVERY_PORT_ENCRYPTED: Final = 6667

PROTOCOL_VERSION: Final = b"3.3"
PROTOCOL_33_HEADER: Final = PROTOCOL_VERSION + b"\x00" * 12  # 15 bytes
FRAME_PREFIX: Final = 0x000055AA
FRAME_SUFFIX: Final = 0x0000AA55

# Tuya v3.4 shares the 55AA frame envelope with v3.3 but swaps the CRC32 trailer
# for a 32-byte HMAC-SHA256 and encrypts the version header *inside* the AES
# ciphertext (v3.3 prepends it outside). See ``Frame34Codec``.
PROTOCOL_VERSION_34: Final = b"3.4"
PROTOCOL_34_HEADER: Final = PROTOCOL_VERSION_34 + b"\x00" * 12  # 15 bytes

FRAME_PREFIX_35: Final = 0x00006699
FRAME_SUFFIX_35: Final = 0x00009966

SESS_KEY_NEG_START: Final = 0x03
SESS_KEY_NEG_RESP: Final = 0x04
SESS_KEY_NEG_FINISH: Final = 0x05

CMD_CONTROL: Final = 0x07
CMD_STATUS: Final = 0x08
CMD_HEART_BEAT: Final = 0x09
CMD_DP_QUERY: Final = 0x0A
CMD_CONTROL_NEW: Final = 0x0D   # v3.4 "device22" control write (protocol:5 wrapper)
CMD_DP_QUERY_NEW: Final = 0x10
CMD_DP_REFRESH: Final = 0x12

CMDS_WITHOUT_HEADER: Final = frozenset({CMD_DP_QUERY})

#: v3.4 omits the inner version header for these commands (mirrors TinyTuya's
#: ``NO_PROTOCOL_HEADER_CMDS``). Among the commands this client sends, CONTROL
#: and CONTROL_NEW carry the header; everything else — DP_QUERY(_NEW),
#: heartbeat, refresh, and the three session-negotiation frames — does not.
#: Confirmed against real v3.4 WBR3 pool firmware (productKey wfzeiyn1ed3axxde,
#: contributed by Martin Čarek / @olomouckyorel).
CMDS_34_WITHOUT_HEADER: Final = frozenset(
    {
        CMD_DP_QUERY,
        CMD_DP_QUERY_NEW,
        CMD_HEART_BEAT,
        CMD_DP_REFRESH,
        SESS_KEY_NEG_START,
        SESS_KEY_NEG_RESP,
        SESS_KEY_NEG_FINISH,
    }
)

DP_POWER: Final = 1
DP_TEMP_SET: Final = 2
DP_TEMP_CURRENT: Final = 3
DP_MODE: Final = 4
DP_FAULT: Final = 13
DP_SUCTION_TEMP: Final = 101   # compressor suction / return-gas temperature (°C)
DP_AMBIENT_TEMP: Final = 102   # outdoor ambient air temperature (°C)
DP_POOL_TEMP: Final = 103      # pool water temperature (°C)
DP_DISCHARGE_TEMP: Final = 104  # compressor discharge / hot-gas temperature (°C)
DP_INLET_TEMP: Final = 105
DP_OUTLET_TEMP: Final = 106
DP_TARGET_FREQUENCY: Final = 107
DP_ACTUAL_FREQUENCY: Final = 108
DP_EEV_STEPS: Final = 109
DP_FAN_SPEED: Final = 110
DP_WATER_PUMP: Final = 111
# Extended diagnostic DPs observed on Silverline FI 150 firmware (v3.5).
# Meanings are inferred from refrigeration engineering and cross-checked
# against measured operating conditions — treat as confirmed once a user
# verifies the values make sense on their device.
DP_CONDENSING_TEMP: Final = 124   # refrigerant high-side saturation temp (°C)
DP_EVAPORATING_TEMP: Final = 133  # refrigerant low-side saturation temp (°C)
DP_SUPERHEAT: Final = 132         # compressor suction superheat (°C, can be negative)
DP_COMPRESSOR_LOAD: Final = 140   # compressor load (%)
DP_TOTAL_HOURS: Final = 120       # cumulative operating hours since first power-on
DP_TARGET_SUPERHEAT: Final = 137  # EEV target superheat setpoint (°C)
DP_TARGET_CONDENSING: Final = 142 # high-side condensing temperature setpoint (°C)

#: Symbolic short names for the fault bitmap on DP 13. Stable across firmware
#: variants — picked to read clearly in entity ids / sensor states without
#: needing the user to memorise the OEM E-code table. The matching OEM codes
#: live in ``FAULT_BIT_CODES`` so log lines and Repair issue keys can still
#: surface them when a service technician needs the original error.
FAULT_BIT_NAMES: Final = {
    0: "water_flow",
    1: "antifreeze",
    2: "high_pressure",
    3: "low_pressure",
    4: "communication",
    5: "inverter_comms",
    6: "inlet_sensor",
    7: "outlet_sensor",
    8: "defrost_sensor",
    9: "coil_sensor",
}

#: OEM service codes printed on the wired controller. Order mirrors
#: FAULT_BIT_NAMES so callers can join the two when they need both
#: representations (e.g. an issue title showing "Water flow (E03)").
FAULT_BIT_CODES: Final = {
    0: "E03",
    1: "E04",
    2: "E05",
    3: "E06",
    4: "E09",
    5: "E10",
    6: "P3",
    7: "P4",
    8: "P1",
    9: "P7",
}
