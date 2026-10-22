# Changelog

All notable changes to Container_Crawler will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `container_crawler.yaml`: Ansible playbook that runs the tool as root on every host in an inventory, fetches each host's CSV log to `Logs/Container_Crawler-<short hostname>.log` next to the playbook, and removes the remote log

## [0.2.0] - 2026-10-22

### Changed
- CSV log now has one row per inspected container, including containers with no PHP and containers that could not be checked, so a clean host no longer produces a header-only file. Columns changed: `kind`, `name` and `version` are replaced by `status`, `php_package_count`, `php_packages`, `php_binary_count`, `php_binaries`, `php_versions` and `error`, and a `timestamp` column is added.
- `--cmd` now also writes one CSV row per container

### Added
- A `runtime_error` CSV row when a runtime cannot list its containers, so a failed `ps` is distinguishable from a host with no containers

## [0.1.1] - 2026-10-22

### Fixed
- Hosts with the `podman-docker` package listed every container twice, once as `docker` and once as `podman`. A `docker` that is really podman (symlink or wrapper script) is now detected and skipped when scanning all runtimes, with a note on stderr and in the summary. `--runtime docker` still scans it, with a note.

## [0.1.0] - 2026-10-22

### Added
- Crawl every running Docker and Podman container and run checks inside each one
- `php` check: package manager, installed `php*` packages with versions (dpkg, rpm, apk), and PHP binaries with versions
- `--cmd` to run an arbitrary shell command in every container
- Live console output with a final summary block
- CSV log of PHP packages per container, written to `--log` or `Container_Crawler-YYYYMMDD_HHMMSS.log`
- `--json`, `--runtime`, `--timeout`, `--workers`, `--only-found`, `--version`
- Python 3.6 compatibility for RHEL 8 system python
