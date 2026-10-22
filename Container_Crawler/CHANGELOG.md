# Changelog

All notable changes to Container_Crawler will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-22

### Added
- Crawl every running Docker and Podman container and run checks inside each one
- `php` check: package manager, installed `php*` packages with versions (dpkg, rpm, apk), and PHP binaries with versions
- `--cmd` to run an arbitrary shell command in every container
- Live console output with a final summary block
- CSV log of PHP packages per container, written to `--log` or `Container_Crawler-YYYYMMDD_HHMMSS.log`
- `--json`, `--runtime`, `--timeout`, `--workers`, `--only-found`, `--version`
- Python 3.6 compatibility for RHEL 8 system python
