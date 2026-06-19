"""Fault-bitmap decoding for the Poolex Silverline.

Seed of the unified fault module: today it only decodes DP 13's bitmap
into human-readable names, but a later phase folds the coordinator's
fault → repair reconciliation in here too.
"""

from __future__ import annotations

from pysilverline import const as tuya_const


def _decode_fault(raw: int | None) -> str | None:
    """Return every active fault bit as a comma-joined name list.

    - ``None`` when DP 13 hasn't been observed yet.
    - ``None`` when the fault bitmap is zero — the sensor surfaces as
      "unknown" / no state which matches the OEM controller's blank
      display when nothing is wrong.
    - Otherwise a comma-joined list of FAULT_BIT_NAMES values in bit
      order, plus ``"bit<n>"`` placeholders for any bits we don't have a
      symbolic name for so a new fault on a new firmware variant still
      surfaces instead of being silently dropped.
    """
    if raw is None or raw == 0:
        return None
    names: list[str] = []
    bit = 0
    while (1 << bit) <= raw:
        if raw & (1 << bit):
            names.append(tuya_const.FAULT_BIT_NAMES.get(bit, f"bit{bit}"))
        bit += 1
    return ", ".join(names)
