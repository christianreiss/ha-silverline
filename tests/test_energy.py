"""Unit tests for the pure energy accumulator (``accumulate_energy``).

No Home Assistant fixtures: ``accumulate_energy`` takes ``now`` and the
prior state explicitly, so the trapezoidal integration and its guard rails
can be pinned with plain datetimes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.poolex_silverline._energy import (
    MIN_MAX_GAP,
    accumulate_energy,
    is_usable_power,
    max_energy_gap,
)

_NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
_GAP = timedelta(seconds=300)


def test_anchor_on_first_tick() -> None:
    """last_tick None: record the sample, add nothing — one power reading
    describes an instant, not an interval."""
    total, last_tick, last_power = accumulate_energy(
        now=_NOW,
        last_tick=None,
        last_power_w=None,
        total_kwh=0.0,
        power_w=1000.0,
        max_gap=_GAP,
    )
    assert total == 0.0
    assert last_tick == _NOW
    assert last_power == 1000.0


def test_constant_power_over_one_hour() -> None:
    """1000 W held for one hour is exactly 1 kWh."""
    total, _, _ = accumulate_energy(
        now=_NOW + timedelta(hours=1),
        last_tick=_NOW,
        last_power_w=1000.0,
        total_kwh=0.0,
        power_w=1000.0,
        max_gap=timedelta(hours=2),
    )
    assert total == 1.0


def test_trapezoidal_averages_the_endpoints() -> None:
    """A ramp from 0 W to 2000 W over an hour integrates as the 1000 W
    average, not as either endpoint. This is the whole reason for the
    trapezoidal rule — a rectangular sum would bill 0 or 2 kWh."""
    total, _, _ = accumulate_energy(
        now=_NOW + timedelta(hours=1),
        last_tick=_NOW,
        last_power_w=0.0,
        total_kwh=0.0,
        power_w=2000.0,
        max_gap=timedelta(hours=2),
    )
    assert total == 1.0


def test_accumulates_onto_existing_total() -> None:
    """The counter is a lifetime total: each interval adds to it."""
    total, _, _ = accumulate_energy(
        now=_NOW + timedelta(hours=1),
        last_tick=_NOW,
        last_power_w=500.0,
        total_kwh=12.5,
        power_w=500.0,
        max_gap=timedelta(hours=2),
    )
    assert total == 13.0


def test_gap_longer_than_max_is_not_billed() -> None:
    """A device offline for six hours must not have the outage billed at
    whatever power it reports on reconnect. Re-anchor, add nothing."""
    total, last_tick, last_power = accumulate_energy(
        now=_NOW + timedelta(hours=6),
        last_tick=_NOW,
        last_power_w=2000.0,
        total_kwh=4.0,
        power_w=2000.0,
        max_gap=_GAP,
    )
    assert total == 4.0
    assert last_tick == _NOW + timedelta(hours=6)
    assert last_power == 2000.0


def test_missing_power_drops_the_anchor() -> None:
    """No reading means no interval: the anchor is cleared so the next
    sample starts fresh instead of bridging across the blind spot."""
    total, last_tick, last_power = accumulate_energy(
        now=_NOW + timedelta(minutes=1),
        last_tick=_NOW,
        last_power_w=1000.0,
        total_kwh=3.0,
        power_w=None,
        max_gap=_GAP,
    )
    assert total == 3.0
    assert last_tick is None
    assert last_power is None


def test_non_positive_delta_is_ignored() -> None:
    """A clock adjustment must not rewind or double-count a
    TOTAL_INCREASING counter."""
    total, _, _ = accumulate_energy(
        now=_NOW - timedelta(minutes=5),
        last_tick=_NOW,
        last_power_w=1000.0,
        total_kwh=7.0,
        power_w=1000.0,
        max_gap=_GAP,
    )
    assert total == 7.0


def test_max_gap_floors_at_five_minutes() -> None:
    """A fast poll interval must not shrink the gap tolerance below the
    floor, or a single missed push would break the integration."""
    assert max_energy_gap(10) == MIN_MAX_GAP
    assert max_energy_gap(120) == timedelta(seconds=600)


def test_is_usable_power_rejects_nan_and_negative() -> None:
    """NaN propagates irreversibly through a running total, and a negative
    product means a DP is not carrying what the layout claims."""
    assert is_usable_power(0.0)
    assert is_usable_power(1500.0)
    assert not is_usable_power(None)
    assert not is_usable_power(float("nan"))
    assert not is_usable_power(float("inf"))
    assert not is_usable_power(-100.0)
