# Container_Crawler

A CLI that iterates every running Docker and Podman container on a host and runs checks inside each one.

The first shipped check, `php`, reports the package manager, every installed `php*` package with its version, and every PHP binary found on `PATH` with its version. It was built to find PHP in containers so their versions can be compared against a vulnerability advisory. The tool reports versions only; it does not decide whether a version is vulnerable.

---

## Requirements

- Linux (Red Hat Enterprise Linux 8+, Ubuntu 22.04+, Debian 11+)
- Python 3.6+ (RHEL 8 system python is supported; this is lower than the repo-wide 3.9+ target on purpose)
- `docker` and/or `podman` on `PATH`
- Permission to talk to the runtime: root or the `docker` group for Docker, root for system-level (rootful) Podman. Rootless Podman only shows the invoking user's containers.
- Inside each container: a POSIX `sh`. Containers without one (distroless, scratch) are reported as errors, not as "no PHP".

---

## Installation

```bash
cp container_crawler.py /usr/local/bin/container_crawler
chmod +x /usr/local/bin/container_crawler
```

No dependencies to install.

---

## Quick Start

```bash
# Scan every installed runtime for PHP
sudo container_crawler

# One runtime only, with a named log
sudo container_crawler --runtime podman --log /var/tmp/php_scan.csv

# Show only containers that have PHP or failed
sudo container_crawler --only-found

# Faster on hosts with many containers
sudo container_crawler --workers 4

# Run any command in every container instead of the PHP check
sudo container_crawler --cmd 'cat /etc/os-release'
```

---

## Console Output

Output is printed live: each container's header appears when its scan starts, and its details as soon as it finishes.

```
Found 3 running container(s) via docker, podman; host web01, log Container_Crawler-20261022_161332.log
[1/3] docker  3f2a1b9c0d1e  web-app  (php:8.2-fpm)
    package manager : dpkg
    php packages    : 3
        php-common  2:93
        php8.2-cli  8.2.12-1
        php8.2-fpm  8.2.12-1
    php binaries    : php8.2 8.2.12 (/usr/bin/php8.2)
[2/3] docker  9a8b7c6d5e4f  legacy  (php:7.4-cli)
    package manager : none
    php packages    : 0
    php binaries    : php 7.4.33 (/usr/local/bin/php)
[3/3] podman  1c2d3e4f5a6b  distro  (gcr.io/distroless/static)
    ERROR: OCI runtime exec failed: ... executable file not found in $PATH

=== Summary ===
Runtimes scanned      : docker, podman
Containers scanned    : 3
  with PHP            : 2
  without PHP         : 0
  errors (not checked): 1
PHP versions seen     : 7.4.33 (1 container), 8.2.12 (1 container)
Package managers      : dpkg (1), none (1)
Log file              : Container_Crawler-20261022_161332.log
```

Notes:

- `php packages` matches every installed package whose name starts with `php`, which includes PHP applications such as `phpMyAdmin`, not only the runtime.
- Official `php:*` images have no PHP packages. They are detected through the binary, so check `php binaries` as well as the package count.
- `PHP versions seen` is built from the binaries' reported versions.
- A container that could not be checked is shown as `ERROR` and counted under `errors`. It is never counted as "without PHP".

With `--only-found`, containers where nothing was found are not listed; per-container progress goes to stderr instead. The summary still counts them.

With `--json`, progress goes to stderr and one JSON object is written to stdout at the end: `tool`, `version`, `hostname`, `timestamp`, `containers[]` (each with `runtime`, `id`, `name`, `image`, `checks`, `cmd`), and `summary`.

---

## CSV Log

A CSV log is always written. The path is `--log FILE`, or `Container_Crawler-YYYYMMDD_HHMMSS.log` in the current directory by default. An existing file at the path is overwritten. The file is flushed after every container, so it is usable even if the scan is interrupted.

| Column | Description |
|--------|-------------|
| `hostname` | Short hostname of the host (domain stripped) |
| `runtime` | `docker` or `podman` |
| `container_id` | Short container ID |
| `container_name` | Container name |
| `image` | Image reference |
| `package_manager` | `dpkg`, `rpm`, `apk`, or `none` |
| `kind` | `package` or `binary` |
| `name` | Package name, or binary name |
| `version` | Package version, or the PHP version the binary reported |

One row per PHP package. Containers with PHP packages get `package` rows only. A container with PHP but no PHP packages (official `php:*` images) gets `binary` rows so it still appears in the log. Containers without PHP, and containers that errored, produce no rows.

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Scan completed with no errors |
| `1` | Usage or config error: bad arguments, no runtime on `PATH`, or the log file cannot be opened |
| `2` | Scan completed, but a runtime or container could not be checked |
| `3` | Unexpected internal error |
| `130` | Interrupted (Ctrl-C) |

---

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--log <file>` | `Container_Crawler-YYYYMMDD_HHMMSS.log` | CSV log path |
| `--check <name>` | `php` | Check to run; repeatable. Available: `php`. Not used when only `--cmd` is given. |
| `--cmd <command>` | none | Run `sh -c <command>` in every container and show its output and exit code. Writes no CSV rows. |
| `--runtime <name>` | `all` | `docker`, `podman`, or `all` (every installed runtime) |
| `--timeout <sec>` | `15` | Per-command timeout. Min: 1. |
| `--workers <n>` | `1` | Containers scanned in parallel. With more than 1, results print in completion order. Min: 1. |
| `--only-found` | off | List only containers where a check found something or failed |
| `--json` | off | Emit one JSON object on stdout at the end |
| `--version` | | Print `container_crawler <version>` and exit |

---

## Limitations

- Running containers only. Stopped containers and images are not scanned.
- Needs a shell in the container. Distroless and scratch images are reported as errors.
- A container's own `php` binary is executed (`php -v`) to read its version.
- Docker and rootful Podman are the supported setups. Rootless Podman works but only sees the invoking user's containers.
