"""Tests for the ``python -m pysilverline`` command-line entry point."""

from __future__ import annotations

from typing import Any

import pytest

from pysilverline import __main__ as cli
from pysilverline import __version__


def test_version_flag_prints_version_and_exits(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_diagnose_subcommand_dispatches_parsed_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(args: Any) -> int:
        seen["host"] = args.host
        seen["probe_write"] = args.probe_write
        seen["version"] = args.version
        return 0

    monkeypatch.setattr(cli, "run_diagnose", fake_run)
    rc = cli.main(["diagnose", "--host", "10.0.0.5", "--probe-write"])
    assert rc == 0
    assert seen == {"host": "10.0.0.5", "probe_write": True, "version": None}


def test_no_subcommand_defaults_to_diagnose(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_diagnose", lambda args: 7)
    assert cli.main([]) == 7
