# Changelog

All notable changes to MemPress will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-06-30

### Added
- `run_command(cmd)`: subprocess wrapper returning `(stdout, stderr, returncode, error_message)`; captures `OSError` so callers never see an uncaught exception
- `read_psi()`: parses `/proc/pressure/memory` by keyword and field name; returns all six PSI floats or an error entry on any parse or read failure
- `read_meminfo()`: parses `/proc/meminfo` by key name; returns `mem_total_mb` and `mem_available_mb` (converted from kB) or an error entry on missing/non-numeric keys
- `_read_vmstat()`: private helper that reads `/proc/vmstat` into a key-value dict; used by `sample_vmstat_file()` for both samples
- `sample_vmstat_file(delay, psi_mode)`: reads `/proc/vmstat` twice with `delay` seconds between samples and returns deltas for all required keys; includes `pgmajfault` and `oom_kill` deltas in PSI mode only
- `collect_vmstat_subprocess(samples, delay)`: runs `vmstat <delay> <samples>` and extracts `si`/`so` columns by header name; validates row count against `--samples`; skips malformed rows
- `collect_top_processes(top_n)`: runs `ps aux --sort=-%mem` and returns the top N lines; failure yields empty list and an error entry without affecting classification

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
