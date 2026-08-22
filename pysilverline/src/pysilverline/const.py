"""Constants for the Tuya v3.3 protocol and Poolex Silverline DPs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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

# v3.5 prepends the same 15-byte inner version header as v3.4 to control writes,
# encrypted inside the GCM ciphertext. Omitting it made real v3.5 firmware reject
# CONTROL_NEW writes with retcode 0x01000000 while reads (header-less DP_QUERY)
# kept working — issue #7. Confirmed by a tinytuya write carrying this exact
# header powering the same unit on (Paulus385), where pysilverline's header-less
# write did not. See ``Frame35Codec.encode``.
PROTOCOL_VERSION_35: Final = b"3.5"
PROTOCOL_35_HEADER: Final = PROTOCOL_VERSION_35 + b"\x00" * 12  # 15 bytes

SESS_KEY_NEG_START: Final = 0x03
SESS_KEY_NEG_RESP: Final = 0x04
SESS_KEY_NEG_FINISH: Final = 0x05

CMD_CONTROL: Final = 0x07
CMD_STATUS: Final = 0x08
CMD_HEART_BEAT: Final = 0x09
CMD_DP_QUERY: Final = 0x0A
CMD_CONTROL_NEW: Final = 0x0D  # v3.4 "device22" control write (protocol:5 wrapper)
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

#: v3.5 uses the same no-inner-header command set as v3.4 — tinytuya keys
#: ``NO_PROTOCOL_HEADER_CMDS`` off the command, not the protocol version, so
#: CONTROL/CONTROL_NEW carry the header while DP_QUERY(_NEW), heartbeat, refresh
#: and the session-negotiation frames do not.
CMDS_35_WITHOUT_HEADER: Final = CMDS_34_WITHOUT_HEADER

DP_POWER: Final = 1
DP_TEMP_SET: Final = 2
DP_TEMP_CURRENT: Final = 3
DP_MODE: Final = 4
DP_FAULT: Final = 13
DP_SUCTION_TEMP: Final = 101  # compressor suction / return-gas temperature (°C)
DP_AMBIENT_TEMP: Final = 102  # outdoor ambient air temperature (°C)
DP_POOL_TEMP: Final = 103  # pool water temperature (°C)
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
DP_CONDENSING_TEMP: Final = 124  # refrigerant high-side saturation temp (°C)
DP_EVAPORATING_TEMP: Final = 133  # refrigerant low-side saturation temp (°C)
DP_SUPERHEAT: Final = 132  # compressor suction superheat (°C, can be negative)
DP_COMPRESSOR_LOAD: Final = 140  # compressor load (%)
DP_TOTAL_HOURS: Final = 120  # cumulative operating hours since first power-on
DP_TARGET_SUPERHEAT: Final = 137  # EEV target superheat setpoint (°C)
DP_TARGET_CONDENSING: Final = 142  # high-side condensing temperature setpoint (°C)

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

#: Symbolic bit names for the status/fault bitmap on DP 21 — Poolex Nano
#: 5kW WiFi and its OEM siblings (productKey ``yk3bytlujz2xshuy``, issue
#: #16). This is a DIFFERENT bitmap from FAULT_BIT_NAMES: same OEM concept
#: (a fault bitfield) but a distinct hardware/firmware family with its own
#: bit assignments — do not assume the two tables share a bit layout.
#:
#: Bit 8 (raw value 256) is hardware-confirmed: a reporter captured DP 21
#: == 0 during normal operation and DP 21 == 256 with the physical
#: controller displaying E6 (insufficient water flow), self-clearing once
#: flow was restored. Cross-referenced against the public tuya-local schema
#: for this productKey, which lumps every other bit into an undifferentiated
#: "fault present" flag — those stay undecoded (surfaced as "bitN" by
#: _decode_fault) rather than guessed.
NANO_5KW_FAULT_BIT_NAMES: Final = {
    8: "water_flow",
}

#: OEM service codes for ``NANO_5KW_FAULT_BIT_NAMES``. Only bit 8 is decoded
#: on this family, and the physical controller prints E6 for it (issue #16).
NANO_5KW_FAULT_BIT_CODES: Final = {
    8: "E6",
}

#: Symbolic bit names for DP 13 on **Full Inverter (FI) firmware** — the
#: Poolex Nano Fi 3kW/5kW line, Tuya pid ``am4nomaadnhwvekq`` (issue #19).
#:
#: Same DP as FAULT_BIT_NAMES, DIFFERENT bit layout. The FI firmware puts the
#: water-flow fault on bit 8, where the classic PC-SLP090N firmware puts the
#: defrost-sensor fault. Wiring an FI unit to FAULT_BIT_NAMES told users to
#: check a defrost probe when their filter pump was off.
#:
#: Hardware-confirmed: a reporter cut the filtration pump under BoostHeat, the
#: Poolex app displayed "Fault of Water Flow Switch", and DP 13 read 256 — bit
#: 8 alone (issue #19, @patrickpetos, 2026-08-22). ``silverline-fe-specs.md``
#: already carried this with a source, annotating bit 8 as "flow protection
#: (vendor reuses on FI firmware)" per tuya-local #2402, the JetLine Selection
#: FI schema; the implementation had followed that file's other, unsourced
#: table instead. Corroborated family-wide by the Nano 5kW, whose DP 21 bitmap
#: also carries water flow on bit 8.
#:
#: Deliberately sparse, like NANO_5KW_FAULT_BIT_NAMES. The classic table's
#: other assignments were never verified here and are not carried over on the
#: strength of the DP number matching; an unnamed bit stays visible as
#: "bit<n>" through _decode_fault's fallback.
#:
#: Bit 19 is named but has NO entry in NANO_FI_FAULT_BIT_CODES, and that
#: asymmetry is deliberate rather than an oversight. The same reporter read
#: 524288 off a unit whose panel displayed "P25 Ambient temperature too
#: high/low" while the app said "ambient temperature out of range" (issue #19,
#: @patrickpetos, 2026-08-22) — two independent readouts agreeing on the
#: meaning, so the name is earned. What it does not earn is a Repair card: an
#: ambient-range protection is weather-driven and self-clearing, so a card
#: would appear and vanish with the forecast. Bits present in ``names`` but
#: absent from ``codes`` yield a binary sensor and a fault_code string and
#: nothing else — see FaultReconciler.reconcile, which enumerates ``codes``.
NANO_FI_FAULT_BIT_NAMES: Final = {
    8: "water_flow",
    19: "ambient_range",
}

#: OEM service codes for ``NANO_FI_FAULT_BIT_NAMES``.
#:
#: Hardware-confirmed against the FI wired controller's own display: during the
#: deliberate pump-cut test above the panel printed **E25 "Water Flow switch
#: failure"** (issue #19, @patrickpetos, 2026-08-22). An earlier revision
#: assumed E03 here — the code the classic Poolstar manual prints for this
#: fault — on the expectation that the FI manual would match. It does not. The
#: two families disagree about the printed code exactly as they disagree about
#: the bit position, which is why the code table travels with the layout.
#:
#: Sparser than ``names`` on purpose: bit 19 is deliberately absent so the
#: ambient-range protection raises no Repair card. See NANO_FI_FAULT_BIT_NAMES.
NANO_FI_FAULT_BIT_CODES: Final = {
    8: "E25",
}


@dataclass(frozen=True, slots=True)
class FaultTable:
    """One firmware family's fault-bitmap decode: bit -> name, bit -> OEM code.

    Names and codes are paired in a single object because they must stay in
    lock-step — a decoder that reads a bit position out of one table and an
    OEM service code out of the other is how issue #19's "Defrost sensor
    fault (P1)" got raised for a water-flow fault. Consumers take the table
    off ``DpLayout.fault_table`` rather than importing a module-level default,
    so a firmware family cannot be silently decoded with another's layout.

    ``names`` may be sparse: an unnamed bit surfaces as ``bit<n>`` rather than
    being dropped, so a fault we have not characterised still reaches the user.

    ``codes`` may be sparser still. A bit in ``names`` but not in ``codes`` is
    a fault we can name but deliberately do not want to raise a Repair card
    for — it gets a binary sensor and a fault_code string only. The reverse
    (a code with no name) is not meaningful and no table does it.
    """

    names: Mapping[int, str]
    codes: Mapping[int, str]


#: The classic PC-SLP090N / JetLine family bitmap on DP 13.
STANDARD_FAULT_TABLE: Final = FaultTable(names=FAULT_BIT_NAMES, codes=FAULT_BIT_CODES)
#: Nano 5kW WiFi family — same concept, DP 21, its own sparse layout (issue #16).
NANO_5KW_FAULT_TABLE: Final = FaultTable(
    names=NANO_5KW_FAULT_BIT_NAMES, codes=NANO_5KW_FAULT_BIT_CODES
)
#: Full Inverter firmware — DP 13 like the classic family, but bit 8 is water
#: flow, not the defrost sensor (issue #19).
NANO_FI_FAULT_TABLE: Final = FaultTable(
    names=NANO_FI_FAULT_BIT_NAMES, codes=NANO_FI_FAULT_BIT_CODES
)
