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

CMD_CONTROL: Final = 0x07
CMD_STATUS: Final = 0x08
CMD_HEART_BEAT: Final = 0x09
CMD_DP_QUERY: Final = 0x0A
CMD_DP_REFRESH: Final = 0x12

CMDS_WITHOUT_HEADER: Final = frozenset({CMD_DP_QUERY})

DP_POWER: Final = 1
DP_TEMP_SET: Final = 2
DP_TEMP_CURRENT: Final = 3
DP_MODE: Final = 4
DP_FAULT: Final = 13
DP_EXHAUST_TEMP: Final = 101
DP_RETURN_TEMP: Final = 102
DP_COIL_TEMP: Final = 103
DP_AMBIENT_TEMP: Final = 104
DP_INLET_TEMP: Final = 105
DP_OUTLET_TEMP: Final = 106
DP_TARGET_FREQUENCY: Final = 107
DP_ACTUAL_FREQUENCY: Final = 108
DP_EEV_STEPS: Final = 109
DP_FAN_SPEED: Final = 110
DP_WATER_PUMP: Final = 111

FAULT_BIT_NAMES: Final = {
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
