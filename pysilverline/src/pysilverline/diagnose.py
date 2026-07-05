"""Paste-ready diagnostic report for GitHub issues.

Gathers everything a maintainer needs to triage a device problem — the protocol
probe ladder, the full DP map, and (optionally) the write-path ack — into one
Markdown report with secrets redacted. Runs without Home Assistant, so it also
serves the users whose config flow fails to connect at all.

The field set and redaction policy mirror the HA integration's
``diagnostics.py`` so a maintainer reads one format and the two never diverge:
``local_key`` / ``device_id`` / ``host`` / ``ip`` / ``gwId`` are redacted; the
``raw`` DP map and ``productKey`` are kept (they carry no secret and are the
whole point of the report).

Usage::

    python -m pysilverline diagnose --host 10.0.0.5 \\
        --device-id <id> --local-key <key>

Add ``--probe-write`` to exercise the control path (off by default; it writes a
DP back to its *current* value, so it does not change device state).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import platform
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from . import __version__, const
from .client import SilverlineClient
from .discovery import DiscoveryInfo, discover_once
from .exceptions import SilverlineError
from .models import DeviceState

_REDACTED = "**REDACTED**"
_PLACEHOLDER = {
    "host": "<host>",
    "device_id": "<device_id>",
    "local_key": "<local_key>",
    "ip": "<ip>",
}


class _CollectingHandler(logging.Handler):
    """Buffers ``pysilverline`` log records emitted during a diagnostic run.

    The probe ladder (``v3.5 probe failed … trying next protocol``) and the
    ``writing DPs …`` line are the single most useful datapoints for the
    "can't connect at all" reports this tool exists to serve, and they only
    exist in the logs.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.levelname} {record.name}: {record.getMessage()}")


def _sanitize(text: str, secrets: dict[str, str | None]) -> str:
    """Replace any secret substring in ``text`` with a stable placeholder.

    pysilverline exception strings and debug logs embed the host (and can embed
    the device id), so a raw paste would leak them into a public issue — the
    same lesson as the HA diagnostics dump. Longest secrets first so a value
    that contains another (rare) is masked completely.
    """
    for key, value in sorted(
        secrets.items(), key=lambda kv: len(kv[1] or ""), reverse=True
    ):
        if value:
            text = text.replace(value, _PLACEHOLDER.get(key, f"<{key}>"))
    return text


async def gather(
    *,
    host: str,
    device_id: str,
    local_key: str,
    version: str | None = None,
    probe_write: bool = False,
    discovery_timeout: float = 6.0,
    discovered: list[DiscoveryInfo] | None = None,
) -> dict[str, Any]:
    """Run the diagnostic sequence and return an un-redacted result dict.

    Never raises for device-side problems: a connection or read failure is
    captured into ``error`` so the report still carries the discovery results
    and the probe ladder, which is exactly the failing case this tool targets.
    """
    handler = _CollectingHandler()
    pkg_logger = logging.getLogger("pysilverline")
    prev_level = pkg_logger.level
    pkg_logger.addHandler(handler)
    pkg_logger.setLevel(logging.DEBUG)

    result: dict[str, Any] = {
        "versions": {
            "pysilverline": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "target": {"host": host, "device_id": device_id, "pinned_version": version},
        "discovery": [],
        "connection": {"detected_version": None, "connected": False},
        "read": None,
        "write_probe": None,
        "error": None,
        "log": [],
    }

    try:
        if discovered is not None:
            result["discovery"] = [asdict(d) for d in discovered]
        else:
            try:
                found = await discover_once(timeout=discovery_timeout)
                result["discovery"] = [asdict(d) for d in found]
            except Exception as err:  # noqa: BLE001 — discovery is best-effort
                result["discovery_error"] = type(err).__name__

        client = SilverlineClient(
            host=host,
            device_id=device_id,
            local_key=local_key,
            protocol_version=version,
        )
        try:
            await client.connect()
            result["connection"]["connected"] = client.connected
            result["connection"]["detected_version"] = client.detected_version

            state = await client.get_status()
            result["read"] = {
                "supported_dps": sorted(state.raw, key=_dp_sort_key),
                "raw": dict(state.raw),
                "decoded": _decoded_state(state),
            }

            if probe_write:
                result["write_probe"] = await _run_write_probe(client, state)
        finally:
            await client.disconnect()
    except SilverlineError as err:
        result["error"] = {"type": type(err).__name__, "message": str(err)}
    except Exception as err:  # noqa: BLE001 — report unexpected failures, don't crash
        result["error"] = {"type": type(err).__name__, "message": str(err)}
    finally:
        pkg_logger.removeHandler(handler)
        pkg_logger.setLevel(prev_level)

    result["log"] = handler.records
    return result


def _dp_sort_key(dp: str) -> tuple[int, str]:
    """Sort DP ids numerically when possible, lexically otherwise."""
    return (int(dp), "") if dp.isdigit() else (1 << 30, dp)


def _decoded_state(state: DeviceState) -> dict[str, Any]:
    """The mapped, human-readable fields (power/mode/temps), nulls dropped."""
    return {k: v for k, v in asdict(state).items() if k != "raw" and v is not None}


async def _run_write_probe(
    client: SilverlineClient, state: DeviceState
) -> dict[str, Any]:
    """Exercise the control path without changing device state.

    Writes the setpoint (DP 2) back to its *current* value. DP 2 is chosen over
    power (DP 1): a same-value setpoint write while the unit is off is inert,
    whereas a power command is more likely to nudge a state machine. A genuine
    write rejection surfaces as the ack retcode in the captured error (this is
    the exact signal that root-caused issue #7).
    """
    probe: dict[str, Any] = {"dp": const.DP_TEMP_SET}
    value = state.raw.get(str(const.DP_TEMP_SET))
    probe["value_before"] = value
    if value is None:
        probe["result"] = "skipped"
        probe["detail"] = (
            "device does not expose DP 2 (setpoint); nothing safe to write"
        )
        return probe
    try:
        await client.set_multiple({const.DP_TEMP_SET: value})
        probe["result"] = "ok"
    except SilverlineError as err:
        probe["result"] = "rejected"
        probe["error_type"] = type(err).__name__
        probe["error_message"] = str(err)
        return probe
    try:
        after = await client.get_status()
        probe["value_after"] = after.raw.get(str(const.DP_TEMP_SET))
    except SilverlineError as err:
        probe["readback_error"] = str(err)
    return probe


# ---------------------------------------------------------------------------
# Redaction + Markdown rendering
# ---------------------------------------------------------------------------


def redact(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``gather``'s result with secrets removed.

    Keeps ``raw`` / ``productKey`` (no secret, the point of the report); masks
    host / device_id / local_key / ip / gwId in both the structured fields and
    the free-text log + error strings.
    """
    secrets = {
        "host": data["target"].get("host"),
        "device_id": data["target"].get("device_id"),
        "local_key": None,  # never gathered into the result, but mask if present
    }
    out: dict[str, Any] = {
        "versions": data["versions"],
        "target": {
            "host": _REDACTED,
            "device_id": _REDACTED,
            "pinned_version": data["target"].get("pinned_version"),
        },
        "discovery": [_redact_discovery(d) for d in data.get("discovery", [])],
        "connection": data["connection"],
        "read": data.get("read"),
        "write_probe": data.get("write_probe"),
        "error": _redact_message(data.get("error"), secrets),
        "log": [_sanitize(line, secrets) for line in data.get("log", [])],
    }
    if "discovery_error" in data:
        out["discovery_error"] = data["discovery_error"]
    if out["write_probe"] and "error_message" in out["write_probe"]:
        out["write_probe"] = dict(out["write_probe"])
        out["write_probe"]["error_message"] = _sanitize(
            out["write_probe"]["error_message"], secrets
        )
    return out


def _redact_discovery(entry: dict[str, Any]) -> dict[str, Any]:
    """Mask the device id / ip on a discovery entry; keep productKey + version."""
    redacted = dict(entry)
    for key in ("ip", "device_id", "gw_id", "gwId"):
        if key in redacted:
            redacted[key] = _REDACTED
    return redacted


def _redact_message(
    error: dict[str, Any] | None, secrets: dict[str, str | None]
) -> dict[str, Any] | None:
    if not error:
        return error
    return {
        "type": error.get("type"),
        "message": _sanitize(str(error.get("message", "")), secrets),
    }


def format_markdown(report: dict[str, Any]) -> str:
    """Render a redacted report as paste-ready GitHub-flavoured Markdown."""
    v = report["versions"]
    lines: list[str] = [
        "# pysilverline diagnostic report",
        "",
        "_Generated by `pysilverline diagnose`. Paste this into your GitHub "
        "issue — device id, local key and host/IP are redacted._",
        "",
        "## Versions",
        f"- pysilverline: `{v['pysilverline']}`",
        f"- Python: `{v['python']}`",
        f"- Platform: `{v['platform']}`",
        "",
        "## Connection",
        f"- Pinned version: `{report['target']['pinned_version'] or 'auto'}`",
        f"- Detected protocol version: "
        f"`{report['connection']['detected_version'] or 'n/a'}`",
        f"- Connected: `{report['connection']['connected']}`",
        "",
    ]

    lines += ["## Discovery (UDP broadcast)"]
    discovery = report.get("discovery") or []
    if discovery:
        lines += ["", "| productKey | version | ip | device id |", "|---|---|---|---|"]
        for d in discovery:
            lines.append(
                f"| `{d.get('product_key') or '?'}` | `{d.get('version') or '?'}` "
                f"| {d.get('ip', _REDACTED)} | {d.get('device_id', _REDACTED)} |"
            )
    else:
        note = report.get("discovery_error")
        lines.append(f"_No devices found ({note})._" if note else "_No devices found._")
    lines.append("")

    read = report.get("read")
    lines += ["## Device state (DP_QUERY)"]
    if read:
        dps = ", ".join(f"`{d}`" for d in read["supported_dps"])
        lines += [
            f"Supported DPs: {dps or '_none_'}",
            "",
            "Raw DP map:",
            "```json",
            _json(read["raw"]),
            "```",
            "",
            "Decoded:",
            "```json",
            _json(read["decoded"]),
            "```",
        ]
    else:
        lines.append("_No state read (see Errors)._")
    lines.append("")

    probe = report.get("write_probe")
    if probe is not None:
        lines += ["## Write probe (control path)"]
        lines.append(f"- DP written: `{probe.get('dp')}` (same-value, no state change)")
        lines.append(f"- Result: `{probe.get('result')}`")
        for key in (
            "value_before",
            "value_after",
            "detail",
            "error_type",
            "error_message",
            "readback_error",
        ):
            if key in probe:
                lines.append(f"- {key}: `{probe[key]}`")
        lines.append("")

    error = report.get("error")
    if error:
        lines += [
            "## Errors",
            f"- Type: `{error['type']}`",
            f"- Message: `{error['message']}`",
            "",
        ]

    log = report.get("log") or []
    if log:
        lines += ["## Log (sanitized)", "```", *log, "```", ""]

    return "\n".join(lines).rstrip() + "\n"


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_diagnose_args(parser: argparse.ArgumentParser) -> None:
    # Connection details are optional: when any are missing the tool prompts for
    # them interactively (the path normal users take). Pass all three for a
    # scriptable, non-interactive run (the issue template / CI path).
    parser.add_argument("--host", help="device IP or hostname")
    parser.add_argument("--device-id", help="Tuya device id (gwId)")
    parser.add_argument("--local-key", help="Tuya local key (16 characters)")
    parser.add_argument(
        "--version",
        choices=["3.3", "3.4", "3.5"],
        default=None,
        help="pin the protocol version (default: auto-probe)",
    )
    parser.add_argument(
        "--probe-write",
        action="store_true",
        help="exercise the control path (writes DP 2 back to its current value)",
    )
    parser.add_argument(
        "--no-redact",
        action="store_true",
        help="do NOT redact secrets (for local inspection only — never paste)",
    )
    parser.add_argument(
        "--output", metavar="FILE", help="also write the report to FILE"
    )
    parser.add_argument(
        "--discovery-timeout", type=float, default=6.0, help="UDP discovery seconds"
    )


# ---------------------------------------------------------------------------
# Interactive prompts (stdlib only — no curses/dependencies, works over SSH)
# ---------------------------------------------------------------------------


def _prompt(
    text: str,
    *,
    default: str | None = None,
    validate: Callable[[str], str | None] | None = None,
    input_fn: Callable[[str], str] = input,
) -> str:
    """Ask until a valid answer is given. ``validate`` returns an error or None."""
    suffix = f" [{default}]" if default else ""
    while True:
        raw = input_fn(f"{text}{suffix}: ").strip()
        if not raw and default is not None:
            raw = default
        if validate is not None:
            error = validate(raw)
            if error:
                print(f"  ! {error}")
                continue
        return raw


def _prompt_yes_no(
    text: str, *, default: bool = False, input_fn: Callable[[str], str] = input
) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input_fn(f"{text} [{hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  ! please answer y or n")


def _valid_key(value: str) -> str | None:
    return None if len(value) == 16 else "must be exactly 16 characters"


def _required(value: str) -> str | None:
    return None if value else "required"


def _valid_version(value: str) -> str | None:
    return (
        None
        if value.lower() in ("", "auto", "3.3", "3.4", "3.5")
        else "enter auto, 3.3, 3.4 or 3.5"
    )


def _collect_interactive(
    args: argparse.Namespace,
    discovered: list[DiscoveryInfo],
    *,
    input_fn: Callable[[str], str] = input,
) -> dict[str, Any]:
    """Fill in any missing connection details by prompting the user."""
    print("\npysilverline diagnostics — let's gather a report for a GitHub issue.\n")
    host, device_id = args.host, args.device_id

    if discovered and not (host and device_id):
        print("Devices found on your network:")
        for i, d in enumerate(discovered, 1):
            print(
                f"  [{i}] {d.ip}  (productKey {d.product_key or '?'}, "
                f"v{d.version}, id {d.device_id})"
            )
        pick = _prompt(
            "Pick a number, or press Enter to type the details manually",
            default="",
            input_fn=input_fn,
        )
        if pick.isdigit() and 1 <= int(pick) <= len(discovered):
            chosen = discovered[int(pick) - 1]
            host = host or chosen.ip
            device_id = device_id or chosen.device_id
            print(f"  → using {chosen.ip}\n")

    if not host:
        host = _prompt("Device IP or hostname", validate=_required, input_fn=input_fn)
    if not device_id:
        device_id = _prompt("Device id (gwId)", validate=_required, input_fn=input_fn)
    local_key = args.local_key or _prompt(
        "Local key (16 characters)", validate=_valid_key, input_fn=input_fn
    )

    version = args.version
    if version is None:
        answer = _prompt(
            "Protocol version (auto / 3.3 / 3.4 / 3.5)",
            default="auto",
            validate=_valid_version,
            input_fn=input_fn,
        )
        version = None if answer.lower() in ("auto", "") else answer

    probe_write = args.probe_write or _prompt_yes_no(
        "Also test the control path? (writes the setpoint back to its current "
        "value — no state change)",
        default=False,
        input_fn=input_fn,
    )
    return {
        "host": host,
        "device_id": device_id,
        "local_key": local_key,
        "version": version,
        "probe_write": probe_write,
        "discovery_timeout": args.discovery_timeout,
    }


def run_diagnose(args: argparse.Namespace) -> int:
    interactive = not (args.host and args.device_id and args.local_key)
    discovered: list[DiscoveryInfo] | None = None

    if interactive:
        print(f"Scanning for devices ({args.discovery_timeout:.0f}s)…")
        try:
            discovered = asyncio.run(discover_once(timeout=args.discovery_timeout))
        except Exception:  # noqa: BLE001 — discovery is a convenience, never fatal
            discovered = []
        params = _collect_interactive(args, discovered)
        print("\nConnecting and reading the device…\n")
    else:
        params = {
            "host": args.host,
            "device_id": args.device_id,
            "local_key": args.local_key,
            "version": args.version,
            "probe_write": args.probe_write,
            "discovery_timeout": args.discovery_timeout,
        }

    data = asyncio.run(gather(discovered=discovered, **params))
    report = data if args.no_redact else redact(data)
    text = format_markdown(report)
    print(text)

    output = args.output
    if interactive and not output:
        answer = input("Save this report to a file? (path, or Enter to skip): ").strip()
        output = answer or None
    if output:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Saved to {output}")

    # Exit non-zero if we never read state, so scripts/CI can tell.
    return 0 if report.get("read") else 1
