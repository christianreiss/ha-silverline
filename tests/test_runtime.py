"""Unit tests for the pure runtime accumulator (``accumulate_runtime``).

No Home Assistant fixtures: ``accumulate_runtime`` takes ``now`` and the
prior state explicitly, so the midnight-rollover + accumulation logic can
be pinned with plain datetimes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from homeassistant.components.climate.const import HVACAction
from homeassistant.util import dt as dt_util

from custom_components.poolex_silverline._runtime import accumulate_runtime


def test_anchor_on_first_tick() -> None:
    """last_tick None: anchor the clock, carry today_seconds unchanged."""
    now = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    seconds, last_tick, local_date = accumulate_runtime(
        now=now,
        last_tick=None,
        local_date=None,
        today_seconds=5.0,
        action=HVACAction.HEATING,
    )
    assert seconds == 5.0
    assert last_tick == now
    assert local_date == dt_util.as_local(now).date()


def test_midnight_reset_zeroes_counter() -> None:
    """A tick whose local date differs from the stored one resets to 0."""
    now = datetime(2026, 5, 23, 12, 0, 0, tzinfo=timezone.utc)
    stale_date = dt_util.as_local(now).date() - timedelta(days=1)
    seconds, last_tick, local_date = accumulate_runtime(
        now=now,
        last_tick=now - timedelta(seconds=60),
        local_date=stale_date,
        today_seconds=120.0,
        action=HVACAction.HEATING,
    )
    assert seconds == 0.0
    assert last_tick == now
    assert local_date == dt_util.as_local(now).date()


def test_heating_accumulates_positive_delta() -> None:
    """HEATING with a positive gap since last_tick adds the gap."""
    last = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    now = last + timedelta(seconds=60)
    seconds, last_tick, local_date = accumulate_runtime(
        now=now,
        last_tick=last,
        local_date=dt_util.as_local(last).date(),
        today_seconds=30.0,
        action=HVACAction.HEATING,
    )
    assert seconds == 90.0
    assert last_tick == now
    assert local_date == dt_util.as_local(now).date()


def test_cooling_accumulates_positive_delta() -> None:
    """COOLING is treated identically to HEATING for accumulation."""
    last = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    now = last + timedelta(seconds=45)
    seconds, _, _ = accumulate_runtime(
        now=now,
        last_tick=last,
        local_date=dt_util.as_local(last).date(),
        today_seconds=0.0,
        action=HVACAction.COOLING,
    )
    assert seconds == 45.0


def test_idle_does_not_accumulate() -> None:
    """IDLE/OFF/None never add seconds even across a large gap."""
    last = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    now = last + timedelta(seconds=300)
    for action in (HVACAction.IDLE, HVACAction.OFF, None):
        seconds, last_tick, _ = accumulate_runtime(
            now=now,
            last_tick=last,
            local_date=dt_util.as_local(last).date(),
            today_seconds=42.0,
            action=action,
        )
        assert seconds == 42.0
        # The clock still re-anchors so the next interval is measured fresh.
        assert last_tick == now


def test_nonpositive_delta_is_guarded() -> None:
    """A last_tick after now (delta <= 0) must not subtract or add time."""
    now = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    future_last = now + timedelta(seconds=10)
    seconds, last_tick, _ = accumulate_runtime(
        now=now,
        last_tick=future_last,
        local_date=dt_util.as_local(now).date(),
        today_seconds=10.0,
        action=HVACAction.HEATING,
    )
    assert seconds == 10.0
    assert last_tick == now
