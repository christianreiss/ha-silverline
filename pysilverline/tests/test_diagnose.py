"""Tests for the diagnostic report builder (no network — the client is faked)."""

from __future__ import annotations

import argparse
import logging
from typing import Any

import pytest

from pysilverline import diagnose
from pysilverline.discovery import DiscoveryInfo
from pysilverline.exceptions import CannotConnect, SilverlineError
from pysilverline.models import DeviceState

HOST = "poolheatpump.secret.example"
DEVICE_ID = "bf77SECRETdeviceid32"
LOCAL_KEY = "SECRETlocalkey16"


def _sample(**over: Any) -> dict[str, Any]:
    """A representative ``gather()`` result with secrets embedded everywhere
    they realistically appear: target fields, discovery, an error message and a
    log line (the host-leak surfaces the tool exists to avoid)."""
    base: dict[str, Any] = {
        "versions": {"pysilverline": "0.4.9", "python": "3.14.5", "platform": "Linux"},
        "target": {"host": HOST, "device_id": DEVICE_ID, "pinned_version": None},
        "discovery": [
            {
                "ip": "10.2.1.98",
                "device_id": DEVICE_ID,
                "product_key": "3bhylhz5zhogklel",
                "version": "3.3",
            }
        ],
        "connection": {"detected_version": "3.3", "connected": True},
        "read": {
            "supported_dps": ["1", "2", "3", "4", "13"],
            "raw": {"1": False, "2": 28, "3": 31, "4": "Auto", "13": 0},
            "decoded": {"power": False, "temp_set": 28, "mode": "Auto"},
        },
        "write_probe": None,
        "error": None,
        "log": [f"DEBUG pysilverline.session: v3.5 probe failed for {HOST}; next"],
    }
    base.update(over)
    return base


def test_redact_masks_identifiers_keeps_dp_map_and_product_key() -> None:
    report = diagnose.redact(_sample())
    assert report["target"]["host"] == diagnose._REDACTED
    assert report["target"]["device_id"] == diagnose._REDACTED
    # Discovery: ip + device id masked, productKey + version kept.
    disc = report["discovery"][0]
    assert disc["ip"] == diagnose._REDACTED
    assert disc["device_id"] == diagnose._REDACTED
    assert disc["product_key"] == "3bhylhz5zhogklel"
    # The device id must not leak through the rendered table either.
    assert DEVICE_ID not in diagnose.format_markdown(report)
    # The DP map is the whole point — never redacted.
    assert report["read"]["raw"] == {"1": False, "2": 28, "3": 31, "4": "Auto", "13": 0}


def test_redact_scrubs_host_from_log_lines() -> None:
    report = diagnose.redact(_sample())
    joined = "\n".join(report["log"])
    assert HOST not in joined
    assert "<host>" in joined


def test_redact_scrubs_host_from_error_message() -> None:
    # pysilverline CannotConnect embeds the host; a raw paste would leak it.
    sample = _sample(
        read=None,
        error={"type": "CannotConnect", "message": f"cannot connect to {HOST}:6668"},
    )
    report = diagnose.redact(sample)
    assert HOST not in report["error"]["message"]
    assert report["error"]["type"] == "CannotConnect"


def test_format_markdown_never_leaks_secrets() -> None:
    sample = _sample(
        error={"type": "CannotConnect", "message": f"cannot connect to {HOST}"},
    )
    text = diagnose.format_markdown(diagnose.redact(sample))
    for secret in (HOST, DEVICE_ID, LOCAL_KEY):
        assert secret not in text
    # Useful, non-secret content survives.
    assert "3bhylhz5zhogklel" in text
    assert "Detected protocol version: `3.3`" in text
    assert '"4": "Auto"' in text


def test_format_markdown_renders_write_probe_rejection() -> None:
    sample = _sample(
        write_probe={
            "dp": 2,
            "value_before": 28,
            "result": "rejected",
            "error_type": "SilverlineError",
            "error_message": "CONTROL failed retcode=0x01000000",
        }
    )
    text = diagnose.format_markdown(diagnose.redact(sample))
    assert "Write probe" in text
    assert "rejected" in text
    assert "0x01000000" in text  # the issue-#7 signal must survive to the report


def test_sanitize_longest_secret_first() -> None:
    # A value that contains a shorter secret must still be fully masked.
    secrets = {"host": "abc.example", "device_id": "abc"}
    out = diagnose._sanitize("connect abc.example failed", secrets)
    assert "abc.example" not in out
    assert out == "connect <host> failed"


def test_no_redact_passthrough_keeps_raw_dict_identity() -> None:
    # --no-redact path: format the un-redacted gather() dict directly.
    sample = _sample()
    text = diagnose.format_markdown(sample)
    assert HOST in text  # not redacted in this mode


# --- interactive prompts -------------------------------------------------


def _scripted(answers: list[str]) -> Any:
    """A fake ``input`` that returns queued answers in order."""
    it = iter(answers)
    return lambda _prompt="": next(it)


def _empty_args() -> Any:
    import argparse

    return argparse.Namespace(
        host=None,
        device_id=None,
        local_key=None,
        version=None,
        probe_write=False,
        discovery_timeout=6.0,
    )


def test_interactive_device_picker_prefills_host_and_id() -> None:
    from pysilverline.discovery import DiscoveryInfo

    discovered = [
        DiscoveryInfo(
            device_id="DEV123", ip="10.0.0.9", version="3.5", product_key="pk"
        )
    ]
    # Pick device 1, enter a valid key, accept auto version, decline write probe.
    answers = _scripted(["1", "0123456789abcdef", "", "n"])
    params = diagnose._collect_interactive(_empty_args(), discovered, input_fn=answers)
    assert params["host"] == "10.0.0.9"
    assert params["device_id"] == "DEV123"
    assert params["version"] is None  # "auto"
    assert params["probe_write"] is False
    assert params["local_key"] == "0123456789abcdef"


def test_interactive_key_validation_reprompts() -> None:
    # Manual entry (no discovery): short key rejected, then a valid one accepted.
    answers = _scripted(
        ["192.168.1.5", "DEVID", "tooshort", "0123456789abcdef", "auto", "y"]
    )
    params = diagnose._collect_interactive(_empty_args(), [], input_fn=answers)
    assert params["host"] == "192.168.1.5"
    assert params["device_id"] == "DEVID"
    assert params["local_key"] == "0123456789abcdef"
    assert params["probe_write"] is True


def test_prompt_yes_no_default_and_parsing() -> None:
    assert diagnose._prompt_yes_no("?", default=True, input_fn=_scripted([""])) is True
    assert (
        diagnose._prompt_yes_no("?", default=False, input_fn=_scripted([""])) is False
    )
    assert diagnose._prompt_yes_no("?", input_fn=_scripted(["yes"])) is True
    # A non-answer re-prompts until a real y/n arrives.
    assert diagnose._prompt_yes_no("?", input_fn=_scripted(["maybe", "n"])) is False


# --- gather() + write probe (client faked, no sockets) ---------------------


def _fake_client_cls(
    *,
    dps: dict[str, Any] | None = None,
    connect_exc: Exception | None = None,
    set_exc: Exception | None = None,
    status_exc: Exception | None = None,
) -> type:
    """Build a ``SilverlineClient`` stand-in for monkeypatching into gather()."""

    class _FakeClient:
        def __init__(
            self,
            *,
            host: str,
            device_id: str,
            local_key: str,
            protocol_version: str | None = None,
        ) -> None:
            self._host = host
            self.connected = False
            self.detected_version: str | None = None

        async def connect(self) -> None:
            # Emitted under the "pysilverline" logger tree so gather()'s
            # collecting handler sees it — like the real probe-ladder lines.
            logging.getLogger("pysilverline.fake").debug("connecting to %s", self._host)
            if connect_exc is not None:
                raise connect_exc
            self.connected = True
            self.detected_version = "3.3"

        async def get_status(self) -> DeviceState:
            if status_exc is not None:
                raise status_exc
            return DeviceState.from_dps(dict(dps or {}))

        async def set_multiple(self, new_dps: dict[int, Any]) -> None:
            if set_exc is not None:
                raise set_exc

        async def disconnect(self) -> None:
            self.connected = False

    return _FakeClient


async def test_gather_happy_path_reads_state_and_collects_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diagnose,
        "SilverlineClient",
        _fake_client_cls(dps={"1": True, "2": 28, "4": "Heat"}),
    )
    discovered = [DiscoveryInfo(device_id=DEVICE_ID, ip="10.2.1.98", product_key="pk")]
    result = await diagnose.gather(
        host=HOST, device_id=DEVICE_ID, local_key=LOCAL_KEY, discovered=discovered
    )
    assert result["error"] is None
    assert result["connection"] == {"detected_version": "3.3", "connected": True}
    assert result["read"]["supported_dps"] == ["1", "2", "4"]
    assert result["read"]["raw"] == {"1": True, "2": 28, "4": "Heat"}
    assert result["read"]["decoded"]["mode"] == "Heat"
    assert result["discovery"][0]["device_id"] == DEVICE_ID
    assert result["write_probe"] is None  # probe_write defaults to off
    assert any("connecting to" in line for line in result["log"])


async def test_gather_runs_discovery_when_not_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_discover(timeout: float) -> list[DiscoveryInfo]:
        return [DiscoveryInfo(device_id="D1", ip="10.0.0.7")]

    monkeypatch.setattr(diagnose, "discover_once", fake_discover)
    monkeypatch.setattr(
        diagnose, "SilverlineClient", _fake_client_cls(dps={"1": False})
    )
    result = await diagnose.gather(host=HOST, device_id=DEVICE_ID, local_key=LOCAL_KEY)
    assert result["discovery"][0]["ip"] == "10.0.0.7"
    assert "discovery_error" not in result


async def test_gather_discovery_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(timeout: float) -> list[DiscoveryInfo]:
        raise OSError("no broadcast socket")

    monkeypatch.setattr(diagnose, "discover_once", boom)
    monkeypatch.setattr(diagnose, "SilverlineClient", _fake_client_cls(dps={"1": True}))
    result = await diagnose.gather(host=HOST, device_id=DEVICE_ID, local_key=LOCAL_KEY)
    assert result["discovery"] == []
    assert result["discovery_error"] == "OSError"
    assert result["read"] is not None  # the run itself still succeeds


async def test_gather_captures_silverline_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diagnose,
        "SilverlineClient",
        _fake_client_cls(connect_exc=CannotConnect(f"cannot connect to {HOST}")),
    )
    result = await diagnose.gather(
        host=HOST, device_id=DEVICE_ID, local_key=LOCAL_KEY, discovered=[]
    )
    assert result["read"] is None
    assert result["error"] == {
        "type": "CannotConnect",
        "message": f"cannot connect to {HOST}",
    }


async def test_gather_captures_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diagnose,
        "SilverlineClient",
        _fake_client_cls(connect_exc=RuntimeError("boom")),
    )
    result = await diagnose.gather(
        host=HOST, device_id=DEVICE_ID, local_key=LOCAL_KEY, discovered=[]
    )
    assert result["error"] == {"type": "RuntimeError", "message": "boom"}


async def test_gather_write_probe_same_value_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diagnose, "SilverlineClient", _fake_client_cls(dps={"2": 28}))
    result = await diagnose.gather(
        host=HOST,
        device_id=DEVICE_ID,
        local_key=LOCAL_KEY,
        probe_write=True,
        discovered=[],
    )
    assert result["write_probe"] == {
        "dp": 2,
        "value_before": 28,
        "result": "ok",
        "value_after": 28,
    }


async def test_write_probe_skipped_without_setpoint_dp() -> None:
    client = _fake_client_cls()(host="h", device_id="d", local_key="k")
    probe = await diagnose._run_write_probe(client, DeviceState.from_dps({}))
    assert probe["result"] == "skipped"
    assert "nothing safe to write" in probe["detail"]


async def test_write_probe_rejection_captures_retcode_error() -> None:
    client = _fake_client_cls(
        set_exc=SilverlineError("CONTROL failed retcode=0x01000000")
    )(host="h", device_id="d", local_key="k")
    probe = await diagnose._run_write_probe(client, DeviceState.from_dps({"2": 28}))
    assert probe["result"] == "rejected"
    assert probe["error_type"] == "SilverlineError"
    assert "0x01000000" in probe["error_message"]
    assert "value_after" not in probe  # no readback after a rejection


async def test_write_probe_readback_failure_is_reported() -> None:
    client = _fake_client_cls(status_exc=SilverlineError("read timeout"))(
        host="h", device_id="d", local_key="k"
    )
    probe = await diagnose._run_write_probe(client, DeviceState.from_dps({"2": 28}))
    assert probe["result"] == "ok"  # the write itself was acked
    assert probe["readback_error"] == "read timeout"


# --- run_diagnose() CLI glue ------------------------------------------------


def _cli_args(**over: Any) -> argparse.Namespace:
    ns = argparse.Namespace(
        host=None,
        device_id=None,
        local_key=None,
        version=None,
        probe_write=False,
        no_redact=False,
        output=None,
        discovery_timeout=0.1,
    )
    for key, value in over.items():
        setattr(ns, key, value)
    return ns


def test_run_diagnose_noninteractive_redacts_and_writes_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, capsys: Any
) -> None:
    captured: dict[str, Any] = {}

    async def fake_gather(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _sample()

    monkeypatch.setattr(diagnose, "gather", fake_gather)
    out = tmp_path / "report.md"
    rc = diagnose.run_diagnose(
        _cli_args(host=HOST, device_id=DEVICE_ID, local_key=LOCAL_KEY, output=str(out))
    )
    assert rc == 0
    assert captured["host"] == HOST
    assert captured["discovered"] is None  # no scan when fully scripted
    text = out.read_text()
    assert HOST not in text  # redacted by default
    assert "pysilverline diagnostic report" in capsys.readouterr().out


def test_run_diagnose_interactive_prompts_and_flags_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    async def no_devices(timeout: float) -> list[DiscoveryInfo]:
        raise OSError("no network")

    async def fake_gather(**kwargs: Any) -> dict[str, Any]:
        return _sample(
            read=None,
            error={"type": "CannotConnect", "message": "cannot connect"},
        )

    def fake_collect(
        args: argparse.Namespace, discovered: list[DiscoveryInfo]
    ) -> dict[str, Any]:
        assert discovered == []  # the failed scan degraded to "none found"
        return {
            "host": HOST,
            "device_id": DEVICE_ID,
            "local_key": "0123456789abcdef",
            "version": None,
            "probe_write": False,
            "discovery_timeout": args.discovery_timeout,
        }

    monkeypatch.setattr(diagnose, "discover_once", no_devices)
    monkeypatch.setattr(diagnose, "gather", fake_gather)
    # The prompts themselves are covered via _collect_interactive's input_fn
    # tests above; here only the late-bound save-to-file prompt reads input.
    monkeypatch.setattr(diagnose, "_collect_interactive", fake_collect)
    monkeypatch.setattr("builtins.input", _scripted([""]))
    rc = diagnose.run_diagnose(_cli_args())
    assert rc == 1  # no state was read → non-zero for scripts/CI
    assert "No state read" in capsys.readouterr().out


def test_format_markdown_no_read_and_discovery_error() -> None:
    sample = _sample(read=None, discovery=[])
    sample["discovery_error"] = "OSError"
    text = diagnose.format_markdown(diagnose.redact(sample))
    assert "_No devices found (OSError)._" in text
    assert "_No state read" in text
