#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Name:    mempress.py
# Version: 0.2.0
# Author:  Kaius
#
# Memory pressure diagnostics for Linux (RHEL 8+, Ubuntu 22.04+, Debian 11+).
# Classifies host memory state as ok, watch, pressure, or unknown.
# Uses PSI (/proc/pressure/memory) when available; falls back to
# vmstat-based heuristics on kernels without PSI support.
#
# See SPEC.md for the full behavioural specification.

import argparse
import sys


VERSION = "0.2.0"


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that exits with code 1 on parse errors (SPEC §3.2)."""

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(1)


def _int_min(minimum):
    """Return an argparse type function that enforces an integer minimum."""
    def check(value):
        try:
            v = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}")
        if v < minimum:
            raise argparse.ArgumentTypeError(f"must be >= {minimum}, got {v}")
        return v
    return check


def build_parser():
    p = _Parser(
        prog="mempress",
        description="Memory pressure diagnostics for Linux.",
    )
    p.add_argument(
        "--delay", type=_int_min(1), default=1, metavar="INT",
        help="seconds between /proc/vmstat samples; vmstat interval in fallback mode (default: 1, min: 1)",
    )
    p.add_argument(
        "--samples", type=_int_min(2), default=5, metavar="INT",
        help="vmstat sample count, fallback mode only (default: 5, min: 2)",
    )
    p.add_argument(
        "--min-avail-mb", type=int, default=1024, metavar="INT",
        help="available-memory floor in MB (default: 1024)",
    )
    p.add_argument(
        "--min-avail-pct", type=float, default=10.0, metavar="FLOAT",
        help="available-memory percentage floor (default: 10.0)",
    )
    p.add_argument(
        "--psi-some-warn", type=float, default=10.0, metavar="FLOAT",
        help="PSI some.avg60 watch threshold, PSI mode only (default: 10.0)",
    )
    p.add_argument(
        "--psi-full-pressure", type=float, default=5.0, metavar="FLOAT",
        help="PSI full.avg10 pressure threshold, PSI mode only (default: 5.0)",
    )
    p.add_argument(
        "--severe-swap-ops", type=int, default=50, metavar="INT",
        help="aggregate swap ops pressure threshold, fallback mode only (default: 50)",
    )
    p.add_argument(
        "--severe-direct-delta", type=int, default=10, metavar="INT",
        help="direct reclaim delta threshold (default: 10)",
    )
    p.add_argument(
        "--json", action="store_true",
        help="emit JSON output only; no human-readable report",
    )
    p.add_argument(
        "--top-n", type=_int_min(2), default=6, metavar="INT",
        help="ps lines to include in output, including header (default: 6, min: 2)",
    )
    return p


def detect_capabilities() -> dict:
    try:
        with open("/proc/pressure/memory", "r"):
            pass
        return {"use_psi": True}
    except OSError:
        return {"use_psi": False}


def run(args):
    capabilities = detect_capabilities()
    _ = capabilities  # phases 3-7 will consume this


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        run(args)
    except Exception as e:
        print(f"mempress: error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
