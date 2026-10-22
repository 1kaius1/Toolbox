#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Name:    container_crawler.py
# Version: 0.2.0
# Author:  Kaius
#
# Iterates every running Docker/Podman container on the host and runs checks
# inside each one. The shipped check ("php") reports the package manager, the
# installed php* packages with versions, and any php binaries found on PATH.
#
# Must run on Python 3.6 (RHEL 8 system python): no capture_output, no text=,
# no dataclasses, no postponed annotations.
#
# See PLANNING.md for the design and output formats.

import argparse
import csv
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed


VERSION = "0.2.0"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_PARTIAL = 2
EXIT_INTERNAL = 3
EXIT_INTERRUPTED = 130

RUNTIMES = ("docker", "podman")

# One exec per container keeps the crawl fast. Output is tagged lines so the
# parser never has to guess where one section ends. Everything is POSIX sh so
# it runs under dash and busybox ash; the rpm/apk lists are filtered in the
# shell because grep may not exist in minimal images.
PHP_PROBE = r"""
pm=none
if command -v dpkg-query >/dev/null 2>&1; then pm=dpkg
elif command -v rpm >/dev/null 2>&1; then pm=rpm
elif command -v apk >/dev/null 2>&1; then pm=apk
fi
printf 'PM\t%s\n' "$pm"
case "$pm" in
dpkg)
    dpkg-query -W -f='${db:Status-Abbrev}\t${Package}\t${Version}\n' 'php*' 2>/dev/null \
    | while IFS= read -r l; do printf 'PKG\t%s\n' "$l"; done
    ;;
rpm)
    rpm -qa --qf '%{NAME}\t%{VERSION}-%{RELEASE}\n' 2>/dev/null \
    | while IFS= read -r l; do case "$l" in php*) printf 'PKG\t%s\n' "$l";; esac; done
    ;;
apk)
    apk info -v 2>/dev/null \
    | while IFS= read -r l; do case "$l" in php*) printf 'PKG\t%s\n' "$l";; esac; done
    ;;
esac
oldifs=$IFS
IFS=:
for d in ${PATH:-/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin}; do
    for f in "$d/php" "$d"/php[0-9]* "$d"/php-fpm* "$d"/php-cgi*; do
        if [ -f "$f" ] && [ -x "$f" ]; then
            r=$(readlink -f "$f" 2>/dev/null) || r=$f
            [ -n "$r" ] || r=$f
            printf 'BIN\t%s\t%s\n' "$f" "$r"
            "$f" -v </dev/null 2>&1 | head -n 5 \
            | while IFS= read -r l; do printf 'OUT\t%s\n' "$l"; done
        fi
    done
done
IFS=$oldifs
exit 0
"""

PHP_VERSION_RE = re.compile(r"^PHP (\d+\.\d+\.\d+\S*)")
# apk prints name-version-rN; the version starts at the last "-<digit>".
APK_PKG_RE = re.compile(r"^(?P<name>.+)-(?P<version>\d[\w.]*-r\d+)$")

# One row per container, so a row proves the container was inspected. The
# php_* columns are empty (not 0) when the php check did not produce a result.
CSV_FIELDS = [
    "timestamp", "hostname", "runtime", "container_id", "container_name",
    "image", "status", "package_manager", "php_package_count", "php_packages",
    "php_binary_count", "php_binaries", "php_versions", "error",
]


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that exits with code 1 on parse errors."""

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


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


def emit(text="", stream=None):
    # Flushed on every line so progress is visible live, even when piped.
    print(text, file=stream or sys.stdout, flush=True)


def short_error(text):
    """Collapse stderr to its last non-empty line for single-line reporting."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1][:200] if lines else ""


# ---------------------------------------------------------------------------
# Runtime access
# ---------------------------------------------------------------------------

def run_cmd(argv, timeout):
    """Run argv; return (rc, stdout, stderr). rc is None on launch failure or timeout."""
    try:
        proc = subprocess.run(
            argv, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {timeout}s"
    except OSError as exc:
        return None, "", str(exc)
    # Decode by hand: container output is not guaranteed to be valid in the
    # host locale, and a UnicodeDecodeError must not abort the whole crawl.
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


def exec_in(runtime, cid, argv, timeout):
    """The single place that executes inside a container; all checks use it."""
    return run_cmd([runtime, "exec", cid] + argv, timeout)


def is_podman_alias(path):
    """True when the 'docker' at path is really podman (podman-docker package).

    The package ships either a symlink to podman or a wrapper script that execs
    it, so check the resolved name first and fall back to the version banner.
    """
    if os.path.basename(os.path.realpath(path)) == "podman":
        return True
    rc, out, err = run_cmd([path, "--version"], 5)
    return rc == 0 and "podman" in (out + err).lower()


def detect_runtimes(wanted):
    """Return (runtimes, notes). notes are human-readable detection remarks."""
    names = RUNTIMES if wanted == "all" else (wanted,)
    runtimes = []
    notes = []
    for name in names:
        path = shutil.which(name)
        if not path:
            continue
        if name == "docker" and is_podman_alias(path):
            if wanted == "all" and shutil.which("podman"):
                # Scanning both would list every container twice, the first
                # time mislabeled as docker.
                notes.append("docker on PATH is a podman alias (podman-docker); scanning podman only")
                continue
            notes.append("docker on PATH is a podman alias (podman-docker); containers are podman's")
        runtimes.append(name)
    return runtimes, notes


def list_containers(runtime, timeout):
    """Return (containers, error). error is a string when the listing failed."""
    rc, out, err = run_cmd(
        [runtime, "ps", "--format", "{{.ID}}|{{.Names}}|{{.Image}}"], timeout
    )
    if rc is None or rc != 0:
        return [], short_error(err) or f"{runtime} ps failed (exit {rc})"
    containers = []
    for line in out.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) != 3 or not parts[0]:
            continue
        containers.append({
            "runtime": runtime, "id": parts[0], "name": parts[1],
            "image": parts[2], "checks": {}, "cmd": None,
        })
    return containers, None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def parse_php_probe(out):
    """Parse tagged PHP_PROBE output; returns the result dict minus status."""
    pm = None
    packages = []
    bins = {}
    cur = None
    for line in out.splitlines():
        parts = line.split("\t")
        tag = parts[0]
        if tag == "PM" and len(parts) >= 2:
            pm = parts[1]
        elif tag == "PKG":
            fields = parts[1:]
            if pm == "dpkg":
                # Only "ii" is installed; "rc" is removed with config left.
                if len(fields) >= 3 and fields[0].strip()[:2] == "ii":
                    packages.append({"name": fields[1], "version": fields[2]})
            elif pm == "rpm":
                if len(fields) >= 2:
                    packages.append({"name": fields[0], "version": fields[1]})
            elif pm == "apk" and fields:
                m = APK_PKG_RE.match(fields[0])
                if m:
                    packages.append({"name": m.group("name"), "version": m.group("version")})
        elif tag == "BIN" and len(parts) >= 3:
            real = parts[2]
            if real in bins:
                # PATH aliases (/bin -> /usr/bin, alternatives symlinks).
                cur = {"lines": []}
            else:
                cur = {"path": real, "name": os.path.basename(real), "lines": []}
                bins[real] = cur
        elif tag == "OUT" and cur is not None:
            cur["lines"].append(line.split("\t", 1)[1] if "\t" in line else "")

    binaries = []
    for real in sorted(bins):
        b = bins[real]
        version = "unknown"
        for ln in b["lines"]:
            m = PHP_VERSION_RE.match(ln)
            if m:
                version = m.group(1)
                break
        binaries.append({"name": b["name"], "version": version, "path": b["path"]})
    packages.sort(key=lambda p: p["name"])
    return {"package_manager": pm, "packages": packages, "binaries": binaries}


def check_php(runtime, c, timeout):
    rc, out, err = exec_in(runtime, c["id"], ["sh", "-c", PHP_PROBE], timeout)
    if rc is None:
        return {"status": "error", "error": err}
    parsed = parse_php_probe(out)
    if parsed["package_manager"] is None:
        # The probe always prints PM first, so its absence means sh never ran
        # (distroless image, exec denied, container exited). Never report that
        # as "no PHP": a failed scan must not look like a clean result.
        return {"status": "error", "error": short_error(err) or f"exec failed (exit {rc})"}
    parsed["status"] = "found" if (parsed["packages"] or parsed["binaries"]) else "not_found"
    return parsed


def check_cmd(runtime, c, timeout, cmd):
    rc, out, err = exec_in(runtime, c["id"], ["sh", "-c", cmd], timeout)
    if rc is None:
        return {"status": "error", "error": err}
    return {"status": "ok", "rc": rc, "stdout": out, "stderr": err}


def render_php(res):
    if res["status"] == "error":
        return [f"    ERROR: {res['error']}"]
    pk = res["packages"]
    lines = [
        f"    package manager : {res['package_manager']}",
        f"    php packages    : {len(pk)}",
    ]
    width = max([len(p["name"]) for p in pk] or [0])
    for p in pk:
        lines.append(f"        {p['name']:<{width}}  {p['version']}")
    if res["binaries"]:
        for i, b in enumerate(res["binaries"]):
            label = "    php binaries    : " if i == 0 else "                      "
            lines.append(f"{label}{b['name']} {b['version']} ({b['path']})")
    else:
        lines.append("    php binaries    : none")
    return lines


def render_cmd(res):
    if res["status"] == "error":
        return [f"    ERROR: {res['error']}"]
    lines = [f"    cmd exit code   : {res['rc']}"]
    for ln in res["stdout"].splitlines():
        lines.append(f"        {ln}")
    for ln in res["stderr"].splitlines():
        lines.append(f"        [stderr] {ln}")
    return lines


CHECKS = {"php": {"run": check_php, "render": render_php}}


def now_stamp():
    return datetime.datetime.now().isoformat(timespec="seconds")


def log_row(host, c):
    """Build the CSV row for a scanned container.

    List columns are ';'-separated: packages as name=version, binaries as
    name=version@path.
    """
    results = list(c["checks"].values()) + ([c["cmd"]] if c["cmd"] else [])
    errors = [r["error"] for r in results if r["status"] == "error"]
    php = c["checks"].get("php")
    if errors:
        status = "error"
    else:
        status = php["status"] if php else "ok"
    row = [now_stamp(), host, c["runtime"], c["id"], c["name"], c["image"], status]
    if php and php["status"] != "error":
        row += [
            php["package_manager"],
            len(php["packages"]),
            ";".join(f"{p['name']}={p['version']}" for p in php["packages"]),
            len(php["binaries"]),
            ";".join(f"{b['name']}={b['version']}@{b['path']}" for b in php["binaries"]),
            ";".join(sorted({b["version"] for b in php["binaries"]})),
        ]
    else:
        row += [""] * 6
    row.append("; ".join(errors))
    return row


def runtime_error_row(host, runtime, err):
    """A listing failure has no containers, so it gets its own row; otherwise a
    failed `ps` would leave a header-only log that looks like a clean host."""
    return [now_stamp(), host, runtime, "", "", "", "runtime_error"] + [""] * 6 + [err]


# ---------------------------------------------------------------------------
# Crawl
# ---------------------------------------------------------------------------

def scan_container(c, checks, cmd, timeout):
    for name in checks:
        c["checks"][name] = CHECKS[name]["run"](c["runtime"], c, timeout)
    if cmd:
        c["cmd"] = check_cmd(c["runtime"], c, timeout, cmd)
    return c


def container_state(c):
    """Return 'error', 'found' or 'clear' for a scanned container."""
    results = list(c["checks"].values()) + ([c["cmd"]] if c["cmd"] else [])
    if any(r["status"] == "error" for r in results):
        return "error"
    if any(r["status"] == "found" for r in results):
        return "found"
    return "clear"


def header_line(i, total, c):
    return f"[{i}/{total}] {c['runtime']}  {c['id']}  {c['name']}  ({c['image']})"


def detail_lines(c):
    lines = []
    for name, res in c["checks"].items():
        lines.extend(CHECKS[name]["render"](res))
    if c["cmd"]:
        lines.extend(render_cmd(c["cmd"]))
    return lines


def build_summary(containers, runtimes, runtime_errors, checks, log_path):
    states = [container_state(c) for c in containers]
    summary = {
        "runtimes_scanned": runtimes,
        "containers_scanned": len(containers),
        "log_file": log_path,
        "runtime_errors": runtime_errors,
        "errors": states.count("error"),
    }
    if "php" in checks:
        php = [c["checks"]["php"] for c in containers]
        versions = {}
        managers = {}
        for res in php:
            if res["status"] == "error":
                continue
            managers[res["package_manager"]] = managers.get(res["package_manager"], 0) + 1
            for v in {b["version"] for b in res["binaries"]}:
                versions[v] = versions.get(v, 0) + 1
        summary["with_php"] = sum(1 for r in php if r["status"] == "found")
        summary["without_php"] = sum(1 for r in php if r["status"] == "not_found")
        summary["php_versions"] = versions
        summary["package_managers"] = managers
    return summary


def format_counts(counts):
    if not counts:
        return "none"
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(
        f"{k} ({v} container{'s' if v != 1 else ''})" for k, v in ordered
    )


def print_summary(s, checks):
    emit()
    emit("=== Summary ===")
    emit(f"Runtimes scanned      : {', '.join(s['runtimes_scanned']) or 'none'}")
    emit(f"Containers scanned    : {s['containers_scanned']}")
    if "php" in checks:
        emit(f"  with PHP            : {s['with_php']}")
        emit(f"  without PHP         : {s['without_php']}")
    emit(f"  errors (not checked): {s['errors']}")
    if "php" in checks:
        emit(f"PHP versions seen     : {format_counts(s['php_versions'])}")
        pms = ", ".join(
            f"{k} ({v})" for k, v in sorted(s["package_managers"].items())
        )
        emit(f"Package managers      : {pms or 'none'}")
    for note in s["notes"]:
        emit(f"Note                  : {note}")
    for err in s["runtime_errors"]:
        emit(f"Runtime error         : {err}")
    emit(f"Log file              : {s['log_file']}")


def default_log_name():
    return "Container_Crawler-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".log"


def build_parser():
    p = _Parser(
        prog="container_crawler",
        description="Run checks inside every running Docker/Podman container.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  container_crawler                       scan all runtimes for PHP\n"
            "  container_crawler --runtime podman      scan podman only\n"
            "  container_crawler --only-found --log /tmp/php.csv\n"
            "  container_crawler --workers 4 --json > scan.json\n"
            "  container_crawler --cmd 'cat /etc/os-release'\n"
            "\n"
            "exit codes: 0 ok, 1 usage/config error, 2 partial scan, 3 internal error"
        ),
    )
    p.add_argument(
        "--log", metavar="FILE",
        help="CSV log path (default: Container_Crawler-YYYYMMDD_HHMMSS.log in the current directory)",
    )
    p.add_argument(
        "--check", action="append", choices=sorted(CHECKS), metavar="NAME",
        help="check to run, repeatable (available: %s; default: php unless --cmd is given)"
        % ", ".join(sorted(CHECKS)),
    )
    p.add_argument(
        "--cmd", metavar="COMMAND",
        help="run COMMAND via 'sh -c' in every container and report its output",
    )
    p.add_argument(
        "--runtime", choices=("docker", "podman", "all"), default="all",
        help="runtime to scan (default: all that are installed)",
    )
    p.add_argument(
        "--timeout", type=_int_min(1), default=15, metavar="SEC",
        help="per-command timeout in seconds (default: 15)",
    )
    p.add_argument(
        "--workers", type=_int_min(1), default=1, metavar="N",
        help="containers scanned in parallel; results print as each finishes (default: 1)",
    )
    p.add_argument(
        "--only-found", action="store_true",
        help="list only containers where a check found something or failed; progress goes to stderr",
    )
    p.add_argument("--json", action="store_true", help="emit one JSON object on stdout at the end")
    p.add_argument(
        "--version", action="version", version=f"container_crawler {VERSION}",
    )
    return p


def run(args):
    checks = args.check or ([] if args.cmd else ["php"])
    checks = list(dict.fromkeys(checks))
    host = socket.gethostname().split(".")[0]
    log_path = args.log or default_log_name()

    # Opened before scanning so an unwritable path fails fast, not after a
    # long crawl.
    try:
        log_fh = open(log_path, "w", newline="")
    except OSError as exc:
        print(f"container_crawler: cannot open log file {log_path}: {exc}", file=sys.stderr)
        return EXIT_USAGE

    with log_fh:
        writer = csv.writer(log_fh)
        writer.writerow(CSV_FIELDS)
        log_fh.flush()

        runtimes, notes = detect_runtimes(args.runtime)
        for note in notes:
            print(f"container_crawler: {note}", file=sys.stderr)
        if not runtimes:
            wanted = "docker or podman" if args.runtime == "all" else args.runtime
            print(f"container_crawler: {wanted} not found on PATH", file=sys.stderr)
            return EXIT_USAGE

        containers = []
        runtime_errors = []
        for rt in runtimes:
            found, err = list_containers(rt, args.timeout)
            if err:
                runtime_errors.append(f"{rt}: {err}")
                print(f"container_crawler: {rt}: {err}", file=sys.stderr)
                writer.writerow(runtime_error_row(host, rt, err))
                log_fh.flush()
            containers.extend(found)

        total = len(containers)
        quiet = args.json or args.only_found
        if not args.json:
            emit(f"Found {total} running container(s) via {', '.join(runtimes)}; "
                 f"host {host}, log {log_path}")

        done = [0]

        def on_start(i, c):
            if quiet:
                emit(f"[{i}/{total}] scanning {c['runtime']} {c['id']} {c['name']}", sys.stderr)
            elif args.workers == 1:
                emit(header_line(i, total, c))

        def on_done(i, c):
            writer.writerow(log_row(host, c))
            log_fh.flush()
            if args.json:
                return
            state = container_state(c)
            if args.only_found and state == "clear":
                return
            if quiet or args.workers > 1:
                emit(header_line(i, total, c))
            for ln in detail_lines(c):
                emit(ln)

        if args.workers == 1:
            for i, c in enumerate(containers, 1):
                on_start(i, c)
                scan_container(c, checks, args.cmd, args.timeout)
                on_done(i, c)
        else:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = []
                for i, c in enumerate(containers, 1):
                    on_start(i, c)
                    futures.append(pool.submit(scan_container, c, checks, args.cmd, args.timeout))
                for fut in as_completed(futures):
                    done[0] += 1
                    on_done(done[0], fut.result())

        summary = build_summary(containers, runtimes, runtime_errors, checks, log_path)
        summary["notes"] = notes
        if args.json:
            doc = {
                "tool": "container_crawler",
                "version": VERSION,
                "hostname": host,
                "timestamp": datetime.datetime.now().isoformat(),
                "containers": containers,
                "summary": summary,
            }
            emit(json.dumps(doc, indent=2))
        else:
            print_summary(summary, checks)

    partial = summary["errors"] > 0 or bool(runtime_errors)
    return EXIT_PARTIAL if partial else EXIT_OK


def main():
    args = build_parser().parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        print("container_crawler: interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as exc:  # last-resort guard so the exit code stays documented
        print(f"container_crawler: internal error: {exc!r}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
