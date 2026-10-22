# Container_Crawler - run checks inside every running container

## Context

The user needs to find PHP (and its version) in running Docker and Podman containers
to judge exposure to the "php2shell" vulnerability. They want the tool built as a
general container crawler: it iterates every running container on the host and runs
checks inside each one. The PHP check is the first check, not the whole purpose.
Speed to a working tool is still the top priority, so the generic part is a thin core
plus a small check registry, and the PHP check is the only check shipped now.

Decisions already made with the user:
- Name: **Container_Crawler** (directory `Container_Crawler/`, script
  `container_crawler.py`, command name `container_crawler`).
- Python, stdlib only, and it must run on **Python 3.6** (RHEL 8 system python). This
  overrides the repo's usual 3.9+ target for this tool; record it in the tool README.
- Report versions only. No CVE or affected-range data is baked in, because the
  "php2shell" vulnerability could not be identified from its name.
- Detect by exec'ing into **running** containers only.
- Runtimes: Docker (root daemon) and Podman rootful. Auto-detect which CLIs are on PATH.

## Design

Style follows `MemPress/mempress.py:24-60`: header block, SPDX line, `VERSION`,
custom argparse class exiting 1 on usage errors, `--version`, `--json`.

Python 3.6 constraints:
- `subprocess.run(..., stdout=PIPE, stderr=PIPE, universal_newlines=True)`. No
  `capture_output` or `text=` (3.7+).
- No dataclasses, no `from __future__ import annotations`, no walrus, no
  `list[str]` hints. Plain dicts and functions.
- f-strings are fine (3.6).

### Core (generic)

1. `detect_runtimes()`: `shutil.which("docker")` and `shutil.which("podman")`,
   filtered by `--runtime {docker,podman,all}` (default all). None found is a fatal
   config error.
2. `list_containers(runtime)`: `<rt> ps --format '{{.ID}}\t{{.Names}}\t{{.Image}}'`.
   A permission or daemon error is recorded against that runtime and the other runtime
   is still scanned.
3. `exec_in(runtime, cid, argv, timeout)`: the single place that calls
   `<rt> exec <cid> ...` and returns `(rc, stdout, stderr)` or a timeout/exec error.
   Every check goes through it, which is also the seam the mock test stubs.
4. `crawl(...)`: runs the selected checks against each container with a per-exec
   timeout (`--timeout`, default 15s). Default is **sequential** (`--workers 1`) so
   progress is strictly in order and easy to follow live. `--workers N` uses a
   `ThreadPoolExecutor` and prints each container as it completes
   (`as_completed`), still live but in completion order.
5. A check that cannot run in a container (no `sh`, distroless or scratch image, exec
   denied, timeout) is recorded as `status: "error"` with the reason. It is never
   reported as "not found", so a scan failure cannot be mistaken for a clean result.

### Check registry

A dict `CHECKS = {name: function}`. A check function takes `(runtime, cid, timeout)`
and returns a dict with at least `status` (`found` / `not_found` / `error`) and its own
fields. `--check NAME` selects checks (repeatable, default `php`).

`php` check (the only shipped check):
- package manager detection: first of `dpkg-query`, `rpm`, `apk` found in the
  container. Always recorded (`dpkg`, `rpm`, `apk`, or `none`) even when no PHP is
  present.
- installed `php*` packages as `(name, version)` pairs: `dpkg-query -W -f
  '${Package}\t${Version}\n' 'php*'` (filtered to installed state), `rpm -qa
  --qf '%{NAME}\t%{VERSION}-%{RELEASE}\n'` filtered to `php*`, or `apk info -v`
  parsed by splitting name from version
- `php -v` first line and `command -v php` for the binary version and path. This
  catches PHP that is not package-managed, e.g. the official `php:*` images, where
  the package count is 0 but PHP is present. Same for php-fpm / php-cgi if present.
- Fields: `package_manager`, `packages[]` (name, version), `binaries[]` (name,
  version, path)

Generic escape hatch: `--cmd 'COMMAND'` runs an arbitrary shell command via
`sh -c` in every container and reports rc and output. It is about 10 lines and makes
the tool useful for other one-off questions immediately. Drop it if the user would
rather keep the first version tighter.

### Output

**Live output.** Human-readable mode prints a line when scanning starts for each
container and then its result block as soon as that container finishes, flushed
immediately (`flush=True`), so the user sees what the tool is doing as it runs. The
header says how many containers were found, and each block is prefixed with a
`[n/total]` counter.

Per-container block (php check):

```
[3/12] docker  3f2a1b9c0d1e  web-app  (php:8.2-fpm)
    package manager : dpkg
    php packages    : 3
        php8.2-cli      8.2.12-1
        php8.2-fpm      8.2.12-1
        php8.2-mbstring 8.2.12-1
    php binaries    : php 8.2.12 (/usr/local/bin/php)
```

A container with no PHP shows `php packages : 0` and no binaries. A container where
the check could not run shows `ERROR: <reason>` in place of the details.

**Summary block** always printed last, after all containers:

```
=== Summary ===
Runtimes scanned      : docker, podman
Containers scanned    : 12
  with PHP            : 4
  without PHP         : 6
  errors (not checked): 2
PHP versions seen     : 8.2.12 (3 containers), 7.4.33 (1 container)
Package managers      : dpkg (8), rpm (2), apk (1), none (1)
```

Runtime-level failures (daemon unreachable, permission denied) are listed under the
summary too.

`--json` cannot stream a single object, so it prints progress lines to stderr as it
goes (live) and then emits one JSON object to stdout at the end: host, timestamp,
`containers[]` (each with `package_manager`, `packages[]`, `binaries[]`, `error`),
`summary{}`, `errors[]`.

Errors and progress go to stderr, result data to stdout.

**CSV log.** Always written, in addition to the console output. Path is
`--log FILE` if given, otherwise `Container_Crawler-YYYYMMDD_HHMMSS.log` in the
current directory, built with `datetime.now().strftime("%Y%m%d_%H%M%S")` (`%Y` 4-digit
year, `%m` month, `%d` day, `%H` 24-hour, `%M` minute, `%S` second; stdlib `strftime`
codes, so the placeholders decode as assumed). Written with the `csv` module (correct
quoting), header row first, flushed after each container so the log is usable even if
the scan is interrupted. The file is opened (truncating any existing file of that name)
before scanning starts; failure to open it is a config error, exit 1.

One row per package, only for containers that have PHP packages:

```
hostname,runtime,container_id,container_name,image,package_manager,kind,name,version
web01,docker,3f2a1b9c0d1e,web-app,php:8.2-fpm,dpkg,package,php8.2-cli,8.2.12-1
web01,docker,3f2a1b9c0d1e,web-app,php:8.2-fpm,dpkg,package,php8.2-fpm,8.2.12-1
web01,docker,9a8b7c6d5e4f,legacy,php:7.4-cli,dpkg,binary,php,7.4.33
```

- `hostname` is the short hostname: `socket.gethostname().split(".")[0]`.
- `kind` is `package` or `binary`. Binary rows are included for containers where PHP
  exists only as a binary (official `php:*` images have no php packages), so those
  hosts are not silently missing from the log. Filter on `kind=package` if only
  package-managed PHP is wanted.
- Containers with no PHP, and containers that errored, produce no rows (errors are
  already on the console and in the `--json` output).

The summary block prints the log path as its last line.

Flags: `--log`, `--check`, `--cmd`, `--runtime`, `--timeout`, `--workers`,
`--only-found` (hide containers with no PHP in the per-container list; summary still
counts them), `--json`, `--version`, `--help` with usage examples.

Note: the default log name uses a hyphen as specified by the user, which differs from
the repo's underscores-in-filenames rule. That rule is for files in the repo; this is
a runtime output name, so the user's spec wins.

Exit codes (distinct per category):
- `0` scan completed with no errors
- `1` usage or config error (bad args, no runtime found, log file cannot be opened)
- `2` scan completed but some runtime or container could not be checked (partial)
- `3` unexpected internal error

## Files

Create:
- `Container_Crawler/container_crawler.py` - the tool (SPDX AGPL header)
- `Container_Crawler/README.md` - requirements (Python 3.6+, docker/podman CLI,
  privileges), usage, flags, available checks, console output format, CSV log format
  and default filename, exit codes, limits (running containers only, needs a shell in
  the container)
- `Container_Crawler/CHANGELOG.md` - keepachangelog, version 0.1.0

Modify:
- `README.md` (root) - add a row to the Tools table
- `CHANGELOG.md` (root) - add an entry for the new tool

Skipped for speed: `SPEC.md` and `ARCHITECTURE.md`. The README and this plan cover the
contract. They can be added when more checks are added. This file is the saved plan.

Implementation notes (differences from the plan above):
- Added exit code `130` for Ctrl-C.
- The `php*` package match includes PHP applications such as `phpMyAdmin`.
- Verified against stubbed runtimes with fake container filesystems, and against a
  real throwaway Podman 4.9.3 container (dpkg path, binary-only PHP).
- Not yet verified on a real RHEL 8 / Python 3.6 host or with real PHP packages
  installed; the grammar was checked with `ast.parse(feature_version=(3, 6))`.

`shared/` is empty, so there is nothing to import.

## Workflow

Per CLAUDE.md, work on a branch `feat/container-crawler` and never commit to master.
Commit with Conventional Commits (`feat(container_crawler): ...`), CHANGELOG in the
same commit. Per the user's global rule, do not add a Co-Authored-By trailer to
commits or PRs.

## Verification

Neither docker nor podman is installed on this dev machine, so:
1. Put a fake `docker` shell script first on PATH (in the scratchpad dir). It returns
   canned `ps` output and canned `php -v`, `dpkg-query` and `rpm` output, plus one
   container whose exec fails and one with no PHP.
2. Check human and `--json` output for: PHP found via dpkg, via rpm, via apk, binary
   only (no packages), PHP not found, exec error, runtime permission error, no
   runtimes on PATH, and `--cmd`. Confirm the summary block counts match. Confirm
   live output by piping through `ts` or adding a `sleep` in the stub and watching
   that blocks appear one at a time rather than all at the end.
3. Check exit codes 0, 1 and 2 against those cases, including an unwritable `--log`
   path (exit 1).
   Check the CSV log: header present, short hostname, one row per package, binary-only
   rows, no rows for no-PHP or errored containers, correct quoting if a field contains
   a comma, default filename matches `Container_Crawler-YYYYMMDD_HHMMSS.log`, `--log`
   overrides it. Parse it back with `csv.reader` to confirm it round-trips.
4. Run `python3 -m py_compile`, `flake8` or `ruff`, and `black --check` if available.
   Grep for 3.7+ usage: `capture_output`, `text=True`, `dataclass`, `fromisoformat`.
5. Ask the user to run it on a real host with docker or podman for the final check,
   since mocks can't prove the real CLI output format.
