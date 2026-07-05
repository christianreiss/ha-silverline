"""``python -m pysilverline`` command-line entry point."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .diagnose import _add_diagnose_args, run_diagnose


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pysilverline",
        description="Tuya pool heat-pump local client tools.",
    )
    parser.add_argument(
        "--version", action="version", version=f"pysilverline {__version__}"
    )
    sub = parser.add_subparsers(dest="command")

    diagnose = sub.add_parser(
        "diagnose",
        help="gather a paste-ready diagnostic report for a GitHub issue",
    )
    _add_diagnose_args(diagnose)
    diagnose.set_defaults(func=run_diagnose)

    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw)
    # No subcommand → default to the interactive diagnostic flow.
    if args.command is None:
        args = parser.parse_args(["diagnose", *raw])
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
