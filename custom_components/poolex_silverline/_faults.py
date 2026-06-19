"""Fault-bitmap decoding and Repair-issue reconciliation.

The unified fault module: decodes DP 13's bitmap into human-readable
names (``_decode_fault``) and owns the fault → Repair-issue
reconciliation state and logic (``FaultReconciler``).
"""

from __future__ import annotations

from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pysilverline import DeviceState
from pysilverline import const as tuya_const

from .const import DOMAIN, E03_DEBOUNCE_SECONDS

# Fault-bit severity for Repair issues. Operational faults (water flow,
# antifreeze, pressure) need user attention now; sensor and comms faults
# are warnings — annoying but the unit usually recovers on its own.
_FAULT_SEVERITY: Final[dict[str, ir.IssueSeverity]] = {
    "E03": ir.IssueSeverity.ERROR,
    "E04": ir.IssueSeverity.ERROR,
    "E05": ir.IssueSeverity.ERROR,
    "E06": ir.IssueSeverity.ERROR,
    "E09": ir.IssueSeverity.WARNING,
    "E10": ir.IssueSeverity.WARNING,
    "P1": ir.IssueSeverity.WARNING,
    "P3": ir.IssueSeverity.WARNING,
    "P4": ir.IssueSeverity.WARNING,
    "P7": ir.IssueSeverity.WARNING,
}
_LEARN_MORE_URL: Final = (
    "https://github.com/christianreiss/ha-silverline#troubleshooting"
)


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


class FaultReconciler:
    """Owns the fault → Repair-issue state and reconciliation logic."""

    def __init__(self) -> None:
        # Tracks which fault codes currently have an open Repair issue so
        # we only fire create/delete when the bit actually flips.
        self._active_issues: set[str] = set()
        # Per-bit monotonic timestamp of the first sighting of an active
        # fault. Drives the E03 debounce: bit 0 only opens a Repair issue
        # after E03_DEBOUNCE_SECONDS of continuous activation. Entries are
        # cleared when the bit clears so a later re-trip restarts the
        # window from zero.
        self._first_seen: dict[int, float] = {}

    @property
    def active_codes(self) -> frozenset[str]:
        """Read-only view of the OEM codes with an open Repair issue."""
        return frozenset(self._active_issues)

    def reconcile(self, hass: HomeAssistant, state: DeviceState, *, now: float) -> None:
        """Create / delete HA Repair issues to match the fault bitmap.

        Fault DP 13 is a 30-bit field; each set bit maps to an OEM service
        code in pysilverline.const.FAULT_BIT_CODES (E03, E04, ...). We
        open one Repair issue per active code and close it the moment the
        device clears the bit — the user gets a transient, self-clearing
        notification stream without having to dismiss each one manually.

        Bit 0 (E03 water flow) is debounced by ``E03_DEBOUNCE_SECONDS``:
        the spec only wants the Repair card to surface once flow has been
        absent persistently, because the unit briefly self-trips E03 on
        startup before the filter pump primes — raising a card in that
        window would be noise. Other bits are immediate; they either don't
        bounce that way or they're already informational.

        ``now`` is a monotonic timestamp supplied by the caller so the
        debounce clock stays patchable from the coordinator under test.
        """
        active_bits: set[int] = set()
        fault = state.fault
        if isinstance(fault, int) and fault != 0:
            for bit in tuya_const.FAULT_BIT_CODES:
                if fault & (1 << bit):
                    active_bits.add(bit)

        # Drop first_seen entries for bits that are no longer set so a
        # later re-trip restarts the debounce window from zero.
        for bit in list(self._first_seen):
            if bit not in active_bits:
                del self._first_seen[bit]
        for bit in active_bits:
            self._first_seen.setdefault(bit, now)

        # Resolve active_bits into the set of OEM codes whose Repair issue
        # should currently be open. Bit 0 only counts after the debounce
        # window has elapsed; everything else counts immediately.
        eligible_codes: set[str] = set()
        for bit in active_bits:
            if bit == 0 and now - self._first_seen[bit] < E03_DEBOUNCE_SECONDS:
                continue
            eligible_codes.add(tuya_const.FAULT_BIT_CODES[bit])

        for cleared in self._active_issues - eligible_codes:
            ir.async_delete_issue(hass, DOMAIN, f"fault_{cleared}")
        for raised in eligible_codes - self._active_issues:
            ir.async_create_issue(
                hass,
                DOMAIN,
                f"fault_{raised}",
                is_fixable=False,
                is_persistent=False,
                severity=_FAULT_SEVERITY.get(raised, ir.IssueSeverity.WARNING),
                translation_key=f"fault_{raised}",
                learn_more_url=_LEARN_MORE_URL,
            )
        self._active_issues = eligible_codes
