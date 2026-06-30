# Changelog

All notable changes to MemPress will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-06-30

### Added
- `detect_capabilities()`: probes `/proc/pressure/memory` at startup and returns `{"use_psi": bool}`; called first inside `run()` to gate subsequent probe-path selection

## [0.1.0] - 2026-06-30

### Added
- Initial implementation of `mempress.py`, a single-file Python 3.9+ CLI with no third-party dependencies
- PSI (Pressure Stall Information) probe path via `/proc/pressure/memory` for direct kernel measurement of task stall time
- Automatic fallback to vmstat-based heuristics on kernels without PSI support (e.g., RHEL 8.0/8.1)
- `/proc/meminfo` direct file read for `MemAvailable`/`MemTotal`, replacing the `free -m` subprocess
- `/proc/vmstat` dual-sample delta collection for `pgscan_direct`, `pgscan_kswapd`, `pswpin`, `pswpout`, `pgmajfault`, `oom_kill`
- Classification policy with four statuses: `ok`, `watch`, `pressure`, `unknown`
- Confidence scoring tied to probe mode: PSI produces higher-confidence results than fallback heuristics
- Human-readable report with labelled sections: header, memory availability, PSI metrics, swap activity, kernel reclaim activity, top processes, final assessment with impact guidance
- JSON output mode (`--json`) with schema version `3.0`; all derived signals included; inapplicable fields set to `null`
- OOM event detection via `oom_kill` delta; reported in reasons regardless of classification status
- Informational `ps aux` top-process listing; `ps` failure does not affect classification
- Configurable thresholds for all key signals via CLI flags
- Exit codes: `0` (classified), `2` (unknown), `1` (fatal/config error)
- 17 behavioural acceptance test cases covering both probe paths, all watch branches, OOM, and error conditions
