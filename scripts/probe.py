"""Live probe / schema-trial tool for the Poolex Silverline.

Reads credentials from `access.yaml` at the repo root, connects to the
device, dumps initial state, pokes DP 2 with its current value to provoke
a full-state echo, and listens for push frames. With `--exercise-modes`,
also cycles through the seven DP 4 enum strings (with safety: power is
forced OFF for the cycle, original power+mode restored on exit unless
`--no-restore`).

Run from the repo root:

    python scripts/probe.py
    python scripts/probe.py --exercise-modes --out tests/fixtures/live_dump.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pysilverline" / "src"))

from pysilverline import (  # noqa: E402
    CannotConnect,
    DeviceState,
    InvalidAuth,
    SilverlineClient,
)
from pysilverline.const import (  # noqa: E402
    DP_MODE,
    DP_POWER,
    DP_TEMP_SET,
    FAULT_BIT_NAMES,
)

DEFAULT_HOST = "poolheatpump.eulie.de"
ACCESS_PATH = REPO_ROOT / "access.yaml"

MODE_CYCLE: tuple[str, ...] = (
    "Heat",
    "BoostHeat",
    "SilentHeat",
    "Cool",
    "BoostCool",
    "SilentCool",
    "Auto",
)


def _load_access(path: Path) -> tuple[str, str]:
    raw = yaml.safe_load(path.read_text())
    entry = raw[0] if isinstance(raw, list) else raw
    return str(entry["id"]), str(entry["key"])


def _decode_fault(value: Any) -> list[str]:
    if not isinstance(value, int) or value == 0:
        return []
    bits: list[str] = []
    for bit in range(30):
        if value & (1 << bit):
            bits.append(FAULT_BIT_NAMES.get(bit, f"bit{bit}"))
    return bits


def _format_state(state: DeviceState) -> str:
    fault_decoded = _decode_fault(state.fault)
    lines = [
        f"  power           = {state.power}",
        f"  mode            = {state.mode!r}",
        f"  temp_set        = {state.temp_set} °C",
        f"  temp_current    = {state.temp_current} °C",
        f"  fault (DP 13)   = {state.fault} {fault_decoded}",
        f"  exhaust  (101)  = {state.exhaust_temp}",
        f"  return   (102)  = {state.return_temp}",
        f"  coil     (103)  = {state.coil_temp}",
        f"  ambient  (104)  = {state.ambient_temp}",
        f"  inlet    (105)  = {state.inlet_temp}",
        f"  outlet   (106)  = {state.outlet_temp}",
        f"  freq_tgt (107)  = {state.target_frequency}",
        f"  freq_act (108)  = {state.actual_frequency}",
        f"  eev      (109)  = {state.eev_steps}",
        f"  fan      (110)  = {state.fan_speed}",
        f"  pump     (111)  = {state.water_pump}",
    ]
    return "\n".join(lines)


def _print_dps(label: str, dps: dict[str, Any]) -> None:
    print(f"\n=== {label} ({len(dps)} DPs) ===")
    for key in sorted(dps, key=lambda k: int(k) if k.isdigit() else 9999):
        print(f"  {key:>4}: {dps[key]!r}")


class Observer:
    """Collects every distinct DP value seen across pushes."""

    def __init__(self) -> None:
        self.observed: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []

    def __call__(self, state: DeviceState) -> None:
        ts = time.monotonic()
        delta = {k: v for k, v in state.raw.items() if self.observed.get(k) != v}
        if delta:
            self.events.append({"t": round(ts, 3), "delta": delta})
            print(f"[push +{ts:7.3f}s] {delta}")
            self.observed.update(state.raw)


async def _wait(seconds: float, why: str) -> None:
    print(f"... waiting {seconds:.1f}s ({why})")
    await asyncio.sleep(seconds)


async def _exercise_modes(
    client: SilverlineClient,
    start_state: DeviceState,
    dwell: float,
) -> tuple[dict[str, dict[str, Any]], bool | None, str | None]:
    """Cycle DP 4 through every enum, return (snapshots, orig_power, orig_mode)."""

    snapshots: dict[str, dict[str, Any]] = {}
    original_power = start_state.power
    original_mode = start_state.mode

    if original_power:
        print("\nForcing power OFF for safe mode cycling...")
        await client.set_dp(DP_POWER, False)
        await _wait(dwell, "settle after power off")

    for mode in MODE_CYCLE:
        print(f"\n-> writing DP {DP_MODE} = {mode!r}")
        try:
            await client.set_dp(DP_MODE, mode)
        except Exception as err:  # noqa: BLE001
            print(f"   write rejected: {err}")
            snapshots[mode] = {"error": str(err)}
            continue
        await _wait(dwell, f"observe echoes for {mode}")
        snapshots[mode] = dict(client.state.raw)

    return snapshots, original_power, original_mode


async def _restore(
    client: SilverlineClient,
    original_power: bool | None,
    original_mode: str | None,
) -> None:
    print("\nRestoring original state...")
    writes: dict[int, bool | int | str] = {}
    if original_mode is not None:
        writes[DP_MODE] = original_mode
    if original_power is not None:
        writes[DP_POWER] = original_power
    if writes:
        try:
            await client.set_multiple(writes)
            print(f"  wrote {writes}")
        except Exception as err:  # noqa: BLE001
            print(f"  restore failed: {err}")
    else:
        print("  nothing to restore (no original state captured)")


async def probe(
    host: str,
    device_id: str,
    local_key: str,
    *,
    seconds: float,
    exercise_modes: bool,
    no_restore: bool,
    dwell: float,
    out_path: Path | None,
) -> int:
    client = SilverlineClient(
        host=host, device_id=device_id, local_key=local_key
    )
    observer = Observer()
    client.add_listener(observer)

    started = datetime.now(timezone.utc).isoformat()
    report: dict[str, Any] = {
        "host": host,
        "device_id": device_id,
        "captured_at": started,
    }

    print(f"connecting to {host}:6668...")
    try:
        await client.connect()
    except CannotConnect as err:
        print(f"FATAL connect: {err}", file=sys.stderr)
        return 2
    print("connected.")

    try:
        try:
            initial = await client.get_status()
        except InvalidAuth as err:
            print(f"FATAL auth: {err}", file=sys.stderr)
            return 3
        observer.observed.update(initial.raw)
        _print_dps("initial DP_QUERY", initial.raw)
        print("\ndecoded:")
        print(_format_state(initial))
        report["initial_dps"] = dict(initial.raw)

        if initial.temp_set is not None:
            print(f"\nPoking DP 2 with current value ({initial.temp_set})...")
            try:
                await client.set_dp(DP_TEMP_SET, initial.temp_set)
            except Exception as err:  # noqa: BLE001
                print(f"  poke failed: {err}")
            else:
                await _wait(dwell, "observe poke echo")
        else:
            print("\nSkipping poke: DP 2 not present in initial state.")

        if exercise_modes:
            print("\n=== MODE EXERCISE ===")
            snapshots, orig_pwr, orig_mode = await _exercise_modes(
                client, initial, dwell
            )
            report["mode_exercise"] = snapshots
            report["original_power"] = orig_pwr
            report["original_mode"] = orig_mode
            await _summarize_mode_results(snapshots)
            if not no_restore:
                await _restore(client, orig_pwr, orig_mode)
            else:
                print("\n--no-restore: leaving device in last-written state.")

        if seconds > 0:
            print(f"\nListening {seconds:.0f}s for push frames...")
            await asyncio.sleep(seconds)

        final = client.state
        report["final_dps"] = dict(final.raw)
        report["all_observed_dps"] = sorted(
            observer.observed.keys(), key=lambda k: int(k) if k.isdigit() else 9999
        )
        report["push_events"] = observer.events
        _print_dps("all observed DPs", observer.observed)

    finally:
        await client.disconnect()
        print("\ndisconnected.")

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
        print(f"wrote {out_path}")

    return 0


async def _summarize_mode_results(snapshots: dict[str, dict[str, Any]]) -> None:
    print("\nMode echo summary:")
    accepted = [m for m, s in snapshots.items() if "error" not in s]
    rejected = [m for m, s in snapshots.items() if "error" in s]
    print(f"  accepted: {accepted}")
    if rejected:
        print(f"  rejected: {rejected}")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument(
        "--access", type=Path, default=ACCESS_PATH,
        help="Path to access.yaml (default: repo-root)",
    )
    p.add_argument("--seconds", type=float, default=60.0,
                   help="Listen window for push frames (default 60s)")
    p.add_argument("--dwell", type=float, default=3.0,
                   help="Wait between writes (default 3s)")
    p.add_argument("--out", type=Path, default=None,
                   help="Write full report as JSON to this path")
    p.add_argument("--exercise-modes", action="store_true",
                   help="Cycle DP 4 through the seven enum strings")
    p.add_argument("--no-restore", action="store_true",
                   help="Skip restoring original power+mode after --exercise-modes")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    device_id, local_key = _load_access(args.access)
    return asyncio.run(
        probe(
            args.host,
            device_id,
            local_key,
            seconds=args.seconds,
            exercise_modes=args.exercise_modes,
            no_restore=args.no_restore,
            dwell=args.dwell,
            out_path=args.out,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
