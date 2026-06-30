#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Name:    mempress.py
# Version: 0.8.1
# Author:  Kaius
#
# Memory pressure diagnostics for Linux (RHEL 8+, Ubuntu 22.04+, Debian 11+).
# Classifies host memory state as ok, watch, pressure, or unknown.
# Uses PSI (/proc/pressure/memory) when available; falls back to
# vmstat-based heuristics on kernels without PSI support.
#
# See SPEC.md for the full behavioural specification.

import argparse
import datetime
import json
import socket
import subprocess
import sys
import time


VERSION = "0.8.1"


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
    p.add_argument(
        "--version", action="version", version=f"%(prog)s {VERSION}",
    )
    return p


def detect_capabilities() -> dict:
    try:
        with open("/proc/pressure/memory", "r"):
            pass
        return {"use_psi": True}
    except OSError:
        return {"use_psi": False}


def run_command(cmd):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return proc.stdout, proc.stderr, proc.returncode, None
    except OSError as exc:
        return "", "", -1, f"{cmd[0]}: {exc}"


def read_psi():
    path = "/proc/pressure/memory"
    result = {"errors": []}
    try:
        with open(path, "r") as fh:
            content = fh.read()
    except OSError as exc:
        result["errors"].append(f"{path}: {type(exc).__name__}: {exc}")
        return result

    parsed = {}
    for line in content.splitlines():
        parts = line.split()
        if not parts:
            continue
        kind = parts[0]
        if kind not in ("some", "full"):
            continue
        fields = {}
        for token in parts[1:]:
            if "=" in token:
                k, _, v = token.partition("=")
                fields[k] = v
        parsed[kind] = fields

    for kind in ("some", "full"):
        if kind not in parsed:
            result["errors"].append(f"{path}: missing '{kind}' line")
            return result
        for field in ("avg10", "avg60", "avg300"):
            raw = parsed[kind].get(field)
            if raw is None:
                result["errors"].append(f"{path}: missing field '{kind}.{field}'")
                return result
            try:
                result[f"psi_{kind}_{field}"] = float(raw)
            except ValueError:
                result["errors"].append(
                    f"{path}: non-numeric value for '{kind}.{field}': {raw!r}"
                )
                return result

    return result


def read_meminfo():
    path = "/proc/meminfo"
    result = {"errors": []}
    try:
        with open(path, "r") as fh:
            content = fh.read()
    except OSError as exc:
        result["errors"].append(f"{path}: {type(exc).__name__}: {exc}")
        return result

    data = {}
    for line in content.splitlines():
        if ":" in line:
            key, _, rest = line.partition(":")
            data[key.strip()] = rest.strip()

    for meminfo_key, out_key in (
        ("MemTotal", "mem_total_mb"),
        ("MemAvailable", "mem_available_mb"),
    ):
        if meminfo_key not in data:
            result["errors"].append(f"{path}: missing key '{meminfo_key}'")
            return result
        parts = data[meminfo_key].split()
        try:
            kb = int(parts[0])
        except (ValueError, IndexError):
            result["errors"].append(
                f"{path}: non-numeric value for '{meminfo_key}': {data[meminfo_key]!r}"
            )
            return result
        result[out_key] = kb // 1024

    return result


def _read_vmstat():
    path = "/proc/vmstat"
    data = {}
    try:
        with open(path, "r") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) != 2:
                    continue
                try:
                    data[parts[0]] = int(parts[1])
                except ValueError:
                    continue
    except OSError as exc:
        return None, f"{path}: {type(exc).__name__}: {exc}"
    return data, None


def sample_vmstat_file(delay, psi_mode):
    result = {"errors": []}
    required = ["pgscan_direct", "pgscan_kswapd", "pswpin", "pswpout"]
    if psi_mode:
        required += ["pgmajfault", "oom_kill"]

    s1, err = _read_vmstat()
    if err:
        result["errors"].append(err)
        return result

    time.sleep(delay)

    s2, err = _read_vmstat()
    if err:
        result["errors"].append(err)
        return result

    for key in required:
        if key not in s1 or key not in s2:
            result["errors"].append(f"/proc/vmstat: missing required key '{key}'")
            return result

    result["direct_reclaim_delta"] = s2["pgscan_direct"] - s1["pgscan_direct"]
    result["kswapd_reclaim_delta"] = s2["pgscan_kswapd"] - s1["pgscan_kswapd"]
    result["pswpin_delta"] = s2["pswpin"] - s1["pswpin"]
    result["pswpout_delta"] = s2["pswpout"] - s1["pswpout"]
    if psi_mode:
        result["pgmajfault_delta"] = s2["pgmajfault"] - s1["pgmajfault"]
        result["oom_kill_delta"] = s2["oom_kill"] - s1["oom_kill"]

    return result


def collect_vmstat_subprocess(samples, delay):
    result = {"errors": [], "swap_in_samples": [], "swap_out_samples": []}
    cmd = ["vmstat", str(delay), str(samples)]
    stdout, stderr, returncode, error_msg = run_command(cmd)

    if error_msg:
        result["errors"].append(f"{' '.join(cmd)}: {error_msg}")
        return result
    if returncode != 0:
        snippet = stderr[:200] if stderr else ""
        result["errors"].append(f"{' '.join(cmd)}: exit {returncode}: {snippet}")
        return result

    header_tokens = None
    si_col = so_col = None
    data_rows = []

    for line in stdout.splitlines():
        tokens = line.split()
        if not tokens:
            continue
        if header_tokens is None:
            if "si" in tokens and "so" in tokens:
                header_tokens = tokens
                si_col = tokens.index("si")
                so_col = tokens.index("so")
            continue
        if len(tokens) != len(header_tokens):
            continue
        try:
            ints = [int(t) for t in tokens]
        except ValueError:
            continue
        data_rows.append((ints[si_col], ints[so_col]))

    if header_tokens is None:
        result["errors"].append(
            f"{' '.join(cmd)}: could not find 'si'/'so' column header in output"
        )
        return result

    if len(data_rows) < samples:
        result["errors"].append(
            f"{' '.join(cmd)}: expected {samples} data rows, got {len(data_rows)}"
        )
        return result

    result["swap_in_samples"] = [r[0] for r in data_rows]
    result["swap_out_samples"] = [r[1] for r in data_rows]
    return result


def collect_top_processes(top_n):
    result = {"top_processes": [], "errors": []}
    cmd = ["ps", "aux", "--sort=-%mem"]
    stdout, stderr, returncode, error_msg = run_command(cmd)

    if error_msg:
        result["errors"].append(f"{' '.join(cmd)}: {error_msg}")
        return result
    if returncode != 0:
        snippet = stderr[:200] if stderr else ""
        result["errors"].append(f"{' '.join(cmd)}: exit {returncode}: {snippet}")
        return result

    result["top_processes"] = stdout.splitlines()[:top_n]
    return result


def derive_signals(raw, capabilities, thresholds):
    use_psi = capabilities["use_psi"]
    s = {"use_psi": use_psi}

    # Pass through all raw metrics; None when the probe did not supply the key.
    for key in (
        "psi_some_avg10", "psi_some_avg60", "psi_some_avg300",
        "psi_full_avg10", "psi_full_avg60", "psi_full_avg300",
        "direct_reclaim_delta", "kswapd_reclaim_delta",
        "pswpin_delta", "pswpout_delta",
        "pgmajfault_delta", "oom_kill_delta",
        "swap_in_samples", "swap_out_samples",
        "mem_total_mb", "mem_available_mb",
    ):
        s[key] = raw.get(key)

    if use_psi:
        required = [
            "psi_some_avg10", "psi_some_avg60", "psi_some_avg300",
            "psi_full_avg10", "psi_full_avg60", "psi_full_avg300",
            "mem_total_mb", "mem_available_mb",
            "direct_reclaim_delta", "kswapd_reclaim_delta",
            "pswpin_delta", "pswpout_delta",
            "pgmajfault_delta", "oom_kill_delta",
        ]
    else:
        required = [
            "mem_total_mb", "mem_available_mb",
            "direct_reclaim_delta", "kswapd_reclaim_delta",
            "pswpin_delta", "pswpout_delta",
            "swap_in_samples", "swap_out_samples",
        ]

    def _present(k):
        v = raw.get(k)
        if v is None:
            return False
        if isinstance(v, list):
            return len(v) > 0
        return True

    s["data_quality_ok"] = all(_present(k) for k in required)

    # Pre-set all derived signals to None; populated below if data is available.
    for k in (
        "mem_available_pct", "low_memory",
        "psi_some_elevated", "psi_full_elevated",
        "swap_active", "swap_ops_total",
        "direct_reclaim_active", "kswapd_active",
        "oom_event", "severe_swap", "severe_direct",
    ):
        s[k] = None

    if not s["data_quality_ok"]:
        return s

    mem_total = s["mem_total_mb"]
    mem_avail = s["mem_available_mb"]
    s["mem_available_pct"] = round((mem_avail / mem_total) * 100, 1)
    s["low_memory"] = (
        mem_avail <= thresholds["min_avail_mb"]
        or s["mem_available_pct"] <= thresholds["min_avail_pct"]
    )
    s["direct_reclaim_active"] = s["direct_reclaim_delta"] > 0

    if use_psi:
        s["psi_some_elevated"] = s["psi_some_avg60"] >= thresholds["psi_some_warn"]
        s["psi_full_elevated"] = s["psi_full_avg10"] >= thresholds["psi_full_pressure"]
        s["swap_active"] = s["pswpin_delta"] > 0 or s["pswpout_delta"] > 0
        s["oom_event"] = s["oom_kill_delta"] > 0
    else:
        si = s["swap_in_samples"]
        so = s["swap_out_samples"]
        s["swap_active"] = any(v > 0 for v in si + so)
        s["swap_ops_total"] = sum(si) + sum(so)
        s["kswapd_active"] = s["kswapd_reclaim_delta"] > 0
        s["severe_swap"] = s["swap_ops_total"] >= thresholds["severe_swap_ops"]
        s["severe_direct"] = s["direct_reclaim_delta"] >= thresholds["severe_direct_delta"]

    return s


def _classify_psi(signals):
    s = signals

    if s["psi_full_elevated"]:
        return "pressure", [
            f"PSI full.avg10={s['psi_full_avg10']:.2f} at or above pressure threshold",
        ]

    if s["psi_some_elevated"] and (s["low_memory"] or s["direct_reclaim_active"]):
        reasons = [f"PSI some.avg60={s['psi_some_avg60']:.2f} elevated"]
        if s["low_memory"]:
            reasons.append(
                f"available memory {s['mem_available_mb']} MB"
                f" ({s['mem_available_pct']}%) below threshold"
            )
        if s["direct_reclaim_active"]:
            reasons.append(
                f"direct reclaim active (pgscan_direct delta={s['direct_reclaim_delta']})"
            )
        return "pressure", reasons

    reasons = []
    if s["psi_some_elevated"]:
        reasons.append(f"PSI some.avg60={s['psi_some_avg60']:.2f} elevated")
    if s["low_memory"]:
        reasons.append(
            f"available memory {s['mem_available_mb']} MB"
            f" ({s['mem_available_pct']}%) below threshold"
        )
    if s["direct_reclaim_active"]:
        reasons.append(
            f"direct reclaim active (pgscan_direct delta={s['direct_reclaim_delta']})"
        )
    if s["swap_active"]:
        reasons.append(
            f"swap activity detected"
            f" (pswpin delta={s['pswpin_delta']}, pswpout delta={s['pswpout_delta']})"
        )
    if reasons:
        return "watch", reasons

    return "ok", ["no memory pressure signals detected"]


def _classify_fallback(signals):
    s = signals
    sa = s["swap_active"]
    dr = s["direct_reclaim_active"]
    lm = s["low_memory"]

    if sa and dr:
        return "pressure", [
            "swap active and direct reclaim active simultaneously",
            f"swap_ops_total={s['swap_ops_total']}",
            f"pgscan_direct delta={s['direct_reclaim_delta']}",
        ]

    if dr and lm:
        return "pressure", [
            "direct reclaim active with low available memory",
            f"pgscan_direct delta={s['direct_reclaim_delta']}",
            f"available memory {s['mem_available_mb']} MB"
            f" ({s['mem_available_pct']}%) below threshold",
        ]

    if sa and lm and (s["severe_swap"] or s["severe_direct"]):
        reasons = [
            "swap active with low available memory and severe intensity",
            f"swap_ops_total={s['swap_ops_total']}",
            f"available memory {s['mem_available_mb']} MB"
            f" ({s['mem_available_pct']}%) below threshold",
        ]
        if s["severe_swap"]:
            reasons.append(
                f"severe swap rate (swap_ops_total={s['swap_ops_total']} at or above threshold)"
            )
        if s["severe_direct"]:
            reasons.append(
                f"severe direct reclaim (pgscan_direct delta={s['direct_reclaim_delta']}"
                f" at or above threshold)"
            )
        return "pressure", reasons

    reasons = []
    if sa:
        reasons.append(f"swap activity detected (swap_ops_total={s['swap_ops_total']})")
    if dr:
        reasons.append(
            f"direct reclaim active (pgscan_direct delta={s['direct_reclaim_delta']})"
        )
    if lm:
        reasons.append(
            f"available memory {s['mem_available_mb']} MB"
            f" ({s['mem_available_pct']}%) below threshold"
        )
    if s["kswapd_active"]:
        reasons.append(
            f"kswapd active (pgscan_kswapd delta={s['kswapd_reclaim_delta']})"
        )
    if reasons:
        return "watch", reasons

    return "ok", ["no memory pressure signals detected"]


def _confidence(status, use_psi):
    if status == "unknown":
        return "low"
    if use_psi:
        return "high" if status in ("pressure", "ok") else "medium"
    return "medium" if status in ("pressure", "ok") else "low"


def classify(signals, capabilities, thresholds):
    use_psi = capabilities["use_psi"]

    if not signals["data_quality_ok"]:
        return "unknown", "low", ["probe failure prevented classification; see errors"]

    if use_psi:
        status, reasons = _classify_psi(signals)
    else:
        status, reasons = _classify_fallback(signals)

    if signals.get("oom_event"):
        reasons.append(
            f"OOM event detected (oom_kill delta={signals['oom_kill_delta']})"
        )

    return status, _confidence(status, use_psi), reasons


_IMPACT = {
    "ok":       "System memory is adequate. No action required.",
    "watch":    "One or more mild indicators present. Monitor for escalation; check top processes.",
    "pressure": "Active memory pressure detected. Investigate top memory consumers; consider adding RAM or reducing workload.",
    "unknown":  "Probe failure prevented classification. No definitive conclusion can be drawn. See errors below.",
}


def render_human(result):
    sig = result["signals"]
    thr = result["thresholds"]
    use_psi = sig["use_psi"]
    out = []

    def ln(s=""):
        out.append(s)

    ln("=== MemPress Memory Pressure Report ===")
    ln(f"Host:       {result['host']}")
    ln(f"Time (UTC): {result['timestamp_utc']}")
    ln(f"Probe mode: {'PSI' if use_psi else 'Fallback'}")

    ln()
    ln("--- Memory Availability ---")
    ma = sig["mem_available_mb"]
    mt = sig["mem_total_mb"]
    mp = sig["mem_available_pct"]
    if ma is not None and mt is not None and mp is not None:
        tag = "LOW" if sig["low_memory"] else "OK"
        ln(f"Available:  {ma} MB ({mp}%) of {mt} MB  [{tag}]")
    else:
        ln("Available:  (data unavailable)")
    ln(f"Thresholds: min {thr['min_avail_mb']} MB or {thr['min_avail_pct']}%")

    if use_psi:
        ln()
        ln("--- PSI Metrics ---")
        if sig["psi_some_avg10"] is not None:
            ln(
                f"some  avg10={sig['psi_some_avg10']:.2f}"
                f"  avg60={sig['psi_some_avg60']:.2f}"
                f"  avg300={sig['psi_some_avg300']:.2f}"
                f"   [watch: avg60 >= {thr['psi_some_warn']}]"
            )
            ln(
                f"full  avg10={sig['psi_full_avg10']:.2f}"
                f"  avg60={sig['psi_full_avg60']:.2f}"
                f"  avg300={sig['psi_full_avg300']:.2f}"
                f"   [pressure: avg10 >= {thr['psi_full_pressure']}]"
            )
        else:
            ln("(PSI data unavailable)")

    ln()
    ln("--- Swap Activity ---")
    if use_psi:
        pi = sig["pswpin_delta"]
        po = sig["pswpout_delta"]
        ln(f"pswpin delta:   {pi if pi is not None else 'n/a'}")
        ln(f"pswpout delta:  {po if po is not None else 'n/a'}")
    else:
        si = sig["swap_in_samples"]
        so = sig["swap_out_samples"]
        ops = sig["swap_ops_total"]
        ln(f"si samples (pages/s):  {si if si is not None else 'n/a'}")
        ln(f"so samples (pages/s):  {so if so is not None else 'n/a'}")
        ln(f"swap_ops_total:        {ops if ops is not None else 'n/a'}")

    ln()
    ln("--- Kernel Reclaim Activity ---")
    dr = sig["direct_reclaim_delta"]
    kr = sig["kswapd_reclaim_delta"]
    ln(f"Direct reclaim (pgscan_direct delta):   {dr if dr is not None else 'n/a'}")
    ln(f"kswapd reclaim (pgscan_kswapd delta):   {kr if kr is not None else 'n/a'}")
    if use_psi:
        mf = sig["pgmajfault_delta"]
        ln(f"Major faults   (pgmajfault delta):      {mf if mf is not None else 'n/a'}")

    ln()
    ln("--- Top Memory Processes ---")
    procs = result.get("top_processes") or []
    if procs:
        for p in procs:
            ln(p)
    else:
        ln("(ps data unavailable)")

    ln()
    ln("--- Assessment ---")
    ln(f"Status:     {result['status']}")
    ln(f"Confidence: {result['confidence']}")
    ln()
    ln("Why:")
    for reason in result["reasons"]:
        ln(f"  - {reason}")
    ln()
    ln(f"Impact: {_IMPACT[result['status']]}")

    if result["errors"]:
        ln()
        ln("--- Errors ---")
        for err in result["errors"]:
            ln(f"  {err}")

    return "\n".join(out)


def render_json(result):
    return json.dumps(result, indent=2)


def run(args):
    capabilities = detect_capabilities()
    use_psi = capabilities["use_psi"]

    thresholds = {
        "min_avail_mb":       args.min_avail_mb,
        "min_avail_pct":      args.min_avail_pct,
        "psi_some_warn":      args.psi_some_warn,
        "psi_full_pressure":  args.psi_full_pressure,
        "severe_swap_ops":    args.severe_swap_ops,
        "severe_direct_delta": args.severe_direct_delta,
    }

    errors = []
    raw = {}

    def _merge(probe_result):
        errors.extend(probe_result.pop("errors", []))
        raw.update(probe_result)

    if use_psi:
        _merge(read_psi())
        _merge(read_meminfo())
        _merge(sample_vmstat_file(args.delay, psi_mode=True))
    else:
        _merge(read_meminfo())
        _merge(sample_vmstat_file(args.delay, psi_mode=False))
        _merge(collect_vmstat_subprocess(args.samples, args.delay))

    top_result = collect_top_processes(args.top_n)
    top_processes = top_result["top_processes"]
    errors.extend(top_result["errors"])

    signals = derive_signals(raw, capabilities, thresholds)
    status, confidence, reasons = classify(signals, capabilities, thresholds)

    result = {
        "version":       "3.0",
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "host":          socket.gethostname(),
        "probe_mode":    "psi" if use_psi else "fallback",
        "status":        status,
        "confidence":    confidence,
        "summary":       _IMPACT[status],
        "signals":       signals,
        "thresholds": {
            "min_avail_mb":       thresholds["min_avail_mb"],
            "min_avail_pct":      thresholds["min_avail_pct"],
            "psi_some_warn":      thresholds["psi_some_warn"] if use_psi else None,
            "psi_full_pressure":  thresholds["psi_full_pressure"] if use_psi else None,
            "severe_swap_ops":    None if use_psi else thresholds["severe_swap_ops"],
            "severe_direct_delta": thresholds["severe_direct_delta"],
        },
        "reasons":       reasons,
        "errors":        errors,
        "top_processes": top_processes,
    }

    if args.json:
        print(render_json(result))
    else:
        print(render_human(result))

    sys.exit(0 if status in ("ok", "watch", "pressure") else 2)


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
