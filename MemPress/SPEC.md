# SPEC: MemPress Memory Pressure Diagnostics Tool

## 1) Purpose

Implement a Linux memory diagnostics CLI that determines whether a host is experiencing memory pressure, while prioritizing **false-positive avoidance** over early detection. The tool must support:

- Red Hat Enterprise Linux 8+
- Ubuntu 22.04+
- Debian 11+

The tool must provide both:

- Human-readable report output (default)
- Machine-readable JSON output (optional flag)

Detection uses PSI (Pressure Stall Information) when available, with automatic fallback to vmstat-based heuristics on kernels that predate PSI support (e.g., RHEL 8.0/8.1).

---

## 2) Inputs and Runtime Environment

### 2.1 Platform assumptions
- Linux only.
- `/proc/meminfo` is readable.
- `/proc/vmstat` is readable.
- `ps` is available.

### 2.2 Capability detection
On startup, check for PSI support:
- PSI is available if `/proc/pressure/memory` exists and is readable.
- If PSI is available, use the PSI probe path (§2.3).
- If PSI is unavailable, use the fallback probe path (§2.4).
- Do not mix probe paths mid-run. If PSI is detected but then fails to parse, set `unknown`; do not silently fall back.

### 2.3 PSI probe path (preferred)
Active when `/proc/pressure/memory` is readable.

Sources:
- `/proc/pressure/memory` for quantitative pressure stall averages.
- `/proc/meminfo` for memory totals and availability.
- `/proc/vmstat` sampled twice (with `--delay` seconds between samples) to compute deltas for: `pgscan_direct`, `pgscan_kswapd`, `pswpin`, `pswpout`, `pgmajfault`, `oom_kill`.
- `ps aux --sort=-%mem` for top memory consumers (informational).

### 2.4 Fallback probe path
Active when PSI is unavailable.

Sources:
- `vmstat <delay> <samples>` for swap activity (`si`, `so`).
- `/proc/meminfo` for memory totals and availability.
- `/proc/vmstat` sampled twice to compute deltas for: `pgscan_direct`, `pgscan_kswapd`, `pswpin`, `pswpout`.
- `ps aux --sort=-%mem` for top memory consumers (informational).

---

## 3) CLI Contract

Implement a single executable Python script.

### 3.1 Flags
- `--delay <int>`: seconds between `/proc/vmstat` samples (both modes) and vmstat interval (fallback only). Default: `1`. Min: `1`.
- `--samples <int>`: vmstat sample count (fallback mode only). Default: `5`. Min: `2`.
- `--min-avail-mb <int>`: absolute available-memory floor (both modes). Default: `1024`.
- `--min-avail-pct <float>`: minimum available memory percentage floor (both modes). Default: `10.0`.
- `--psi-some-warn <float>`: PSI `some.avg60` threshold for `watch` (PSI mode only). Default: `10.0`.
- `--psi-full-pressure <float>`: PSI `full.avg10` threshold for `pressure` (PSI mode only). Default: `5.0`.
- `--severe-swap-ops <int>`: severe swap intensity threshold, sum of all si+so samples (fallback mode only). Default: `50`.
- `--severe-direct-delta <int>`: severe direct reclaim threshold (both modes). Default: `10`.
- `--json`: emit JSON only.
- `--top-n <int>`: number of `ps` lines including header. Default: `6`. Min: `2`.

### 3.2 Exit codes
- `0`: successful execution with valid classification (`ok`, `watch`, `pressure`).
- `2`: execution completed but classification is `unknown` due to probe/parse issues.
- `1`: fatal runtime/configuration error (invalid arguments, unexpected fatal exception).

---

## 4) Data Model and Derived Signals

### 4.1 Capability flags
- `use_psi`: `true` if `/proc/pressure/memory` was detected and used.

### 4.2 Raw metrics: PSI path only
- `psi_some_avg10`, `psi_some_avg60`, `psi_some_avg300`: floats from the `some` line.
- `psi_full_avg10`, `psi_full_avg60`, `psi_full_avg300`: floats from the `full` line.
- `pgmajfault_delta`: delta of `pgmajfault` from `/proc/vmstat`.
- `oom_kill_delta`: delta of `oom_kill` from `/proc/vmstat`.

### 4.3 Raw metrics: fallback path only
- `swap_in_samples`: list of integers from vmstat `si` column.
- `swap_out_samples`: list of integers from vmstat `so` column.

### 4.4 Raw metrics: both paths
- `mem_total_mb`, `mem_available_mb`: parsed from `/proc/meminfo`.
- `direct_reclaim_delta`: delta of `pgscan_direct` from `/proc/vmstat`.
- `kswapd_reclaim_delta`: delta of `pgscan_kswapd` from `/proc/vmstat`.
- `pswpin_delta`, `pswpout_delta`: deltas of `pswpin`/`pswpout` from `/proc/vmstat`.
- `top_processes`: top N process lines from `ps`.

### 4.5 Derived signals: PSI path
- `psi_some_elevated`: `psi_some_avg60 >= psi_some_warn`.
- `psi_full_elevated`: `psi_full_avg10 >= psi_full_pressure`.
- `mem_available_pct`: `(mem_available_mb / mem_total_mb) * 100`, rounded to 1 decimal.
- `low_memory`: `mem_available_mb <= min_avail_mb OR mem_available_pct <= min_avail_pct`.
- `direct_reclaim_active`: `direct_reclaim_delta > 0`.
- `swap_active`: `pswpin_delta > 0 OR pswpout_delta > 0`.
- `oom_event`: `oom_kill_delta > 0`.
- `data_quality_ok`: all required probes succeeded and parsed (`/proc/pressure/memory`, `/proc/meminfo`, `/proc/vmstat`).

### 4.6 Derived signals: fallback path
- `swap_active`: `true` if any `si` or `so` sample > 0.
- `swap_ops_total`: sum of all `si` and `so` samples. This is a dimensionless aggregate of per-interval rate observations, not a total page count.
- `direct_reclaim_active`: `direct_reclaim_delta > 0`.
- `kswapd_active`: `kswapd_reclaim_delta > 0`.
- `mem_available_pct`: `(mem_available_mb / mem_total_mb) * 100`, rounded to 1 decimal.
- `low_memory`: `mem_available_mb <= min_avail_mb OR mem_available_pct <= min_avail_pct`.
- `severe_swap`: `swap_ops_total >= severe_swap_ops`.
- `severe_direct`: `direct_reclaim_delta >= severe_direct_delta`.
- `data_quality_ok`: all required probes succeeded and parsed (`/proc/meminfo`, `/proc/vmstat`, vmstat subprocess).

**Required vs. optional probes:** `ps` is informational. A `ps` failure must not set `unknown`; instead yield empty `top_processes` and record an entry in `errors`. All other probes listed above are required.

---

## 5) Classification Policy (False-Positive Averse)

Classification is ordered and mutually exclusive. Evaluate top-to-bottom; take the first matching status:

**`unknown` → `pressure` → `watch` → `ok`**

### 5.1 `unknown` (both paths)
Set `unknown` if `data_quality_ok` is false. Do not crash. Capture probe failure details in `errors`.

### 5.2 PSI path classification

#### `pressure`
- `psi_full_elevated` (full.avg10 ≥ psi_full_pressure), OR
- `psi_some_elevated` AND (`low_memory` OR `direct_reclaim_active`)

#### `watch`
- `psi_some_elevated` but pressure conditions not met, OR
- `low_memory`, OR
- `direct_reclaim_active`, OR
- `swap_active`

#### `ok`
All of the following are true:
- NOT `psi_some_elevated`
- NOT `psi_full_elevated`
- NOT `low_memory`

Note: `oom_event` does not change the status classification but must always appear in `reasons` when true, regardless of status.

### 5.3 Fallback path classification

#### `pressure`
- `(swap_active AND direct_reclaim_active)` OR
- `(direct_reclaim_active AND low_memory)` OR
- `(swap_active AND low_memory AND (severe_swap OR severe_direct))`

#### `watch`
- `swap_active` only (pressure conditions not met), OR
- `direct_reclaim_active` only, OR
- `low_memory` only, OR
- `kswapd_active` only, OR
- `swap_active AND kswapd_active` (pressure conditions not met), OR
- `low_memory AND kswapd_active` (pressure conditions not met)

#### `ok`
All of the following are true:
- NOT `swap_active`
- NOT `direct_reclaim_active`
- NOT `low_memory`
- NOT `kswapd_active`

---

## 6) Confidence Scoring

| status | use_psi | data_quality_ok | confidence |
|--------|---------|-----------------|------------|
| unknown | any | false | low |
| pressure | true | true | high |
| pressure | false | true | medium |
| watch | true | true | medium |
| watch | false | true | low |
| ok | true | true | high |
| ok | false | true | medium |

Rationale: PSI is a direct kernel measurement of task stall time. Fallback heuristics infer pressure indirectly from side effects, so confidence is one tier lower for the same status.

---

## 7) Human-Readable Output Requirements

Default mode prints a structured report with labelled sections:

1. **Header**: hostname, timestamp UTC, probe mode (`PSI` or `Fallback`)
2. **Memory availability**: MemAvailable, MemTotal, percentage, threshold status
3. **PSI metrics** (PSI mode only): `some.avg10/60/300` and `full.avg10/60/300`, with warn/pressure thresholds annotated
4. **Swap activity**: pswpin/pswpout deltas (PSI mode) or si/so samples (fallback)
5. **Kernel reclaim activity**: direct_reclaim_delta, kswapd_reclaim_delta, pgmajfault_delta (PSI mode only)
6. **Top memory processes**: top N lines from ps, or note if ps failed
7. **Final assessment**:
   - Status and confidence
   - Why: bullet-point reasons covering which signals fired and which thresholds were crossed
   - Impact guidance per status:
     - `ok`: System memory is adequate. No action required.
     - `watch`: One or more mild indicators present. Monitor for escalation; check top processes.
     - `pressure`: Active memory pressure detected. Investigate top memory consumers; consider adding RAM or reducing workload.
     - `unknown`: Probe failure prevented classification. No definitive conclusion can be drawn. See errors below.

When `oom_event` is true, call it out explicitly in the assessment regardless of status.

---

## 8) JSON Output Contract

When `--json` is provided, output only valid JSON with no trailing text:

```json
{
  "version": "3.0",
  "timestamp_utc": "ISO-8601",
  "host": "string",
  "probe_mode": "psi|fallback",
  "status": "ok|watch|pressure|unknown",
  "confidence": "low|medium|high",
  "summary": "string",
  "signals": {
    "use_psi": "bool",
    "psi_some_avg10": "float|null",
    "psi_some_avg60": "float|null",
    "psi_some_avg300": "float|null",
    "psi_full_avg10": "float|null",
    "psi_full_avg60": "float|null",
    "psi_full_avg300": "float|null",
    "psi_some_elevated": "bool|null",
    "psi_full_elevated": "bool|null",
    "swap_active": "bool",
    "swap_in_samples": "int[]|null",
    "swap_out_samples": "int[]|null",
    "swap_ops_total": "int|null",
    "direct_reclaim_delta": "int|null",
    "kswapd_reclaim_delta": "int|null",
    "direct_reclaim_active": "bool",
    "kswapd_active": "bool|null",
    "pgmajfault_delta": "int|null",
    "oom_kill_delta": "int|null",
    "oom_event": "bool|null",
    "pswpin_delta": "int|null",
    "pswpout_delta": "int|null",
    "mem_total_mb": "int",
    "mem_available_mb": "int",
    "mem_available_pct": "float",
    "low_memory": "bool",
    "severe_swap": "bool|null",
    "severe_direct": "bool|null",
    "data_quality_ok": "bool"
  },
  "thresholds": {
    "min_avail_mb": "int",
    "min_avail_pct": "float",
    "psi_some_warn": "float|null",
    "psi_full_pressure": "float|null",
    "severe_swap_ops": "int|null",
    "severe_direct_delta": "int"
  },
  "reasons": ["string"],
  "errors": ["string"],
  "top_processes": ["string"]
}
```

Fields marked `null` are set to `null` (not omitted) when not applicable to the active probe mode; the schema is the same regardless of probe mode.

---

## 9) Error Handling Requirements

- Wrap all file reads and subprocess calls with explicit error handling.
- For subprocess failures, capture: command executed, exit code, stderr snippet.
- For file read failures, capture: file path, exception type, message.
- Validate parsed structures before indexing.
- On malformed vmstat lines (fallback), skip the malformed row; if fewer than `--samples` valid rows remain, set `unknown`.
- If PSI file read or parse fails after PSI was detected, set `unknown`. Do not silently switch to fallback mode.
- Never raise an uncaught traceback in normal execution paths.

---

## 10) Parsing Robustness Requirements

### 10.1 /proc/pressure/memory (PSI mode)
- Identify `some` and `full` lines by the leading keyword, not by line number.
- Extract `avg10`, `avg60`, `avg300` values by field name (`avg10=<value>`) not by column position.
- If either line or any expected field is absent, set `unknown`.

### 10.2 /proc/meminfo (both modes)
- Locate `MemTotal:` and `MemAvailable:` by key name, not by line number or column index.
- Parse values as positive integers (kB). Convert to MB for display and threshold comparison.
- If either key is missing or non-numeric, set `unknown`.

### 10.3 /proc/vmstat (both modes)
- Locate all required keys by name. Sample the file twice with `--delay` seconds between samples; compute delta = second − first.
- Required keys (both modes): `pgscan_direct`, `pgscan_kswapd`, `pswpin`, `pswpout`.
- Additional required keys (PSI mode): `pgmajfault`, `oom_kill`.
- If any required key is absent in either sample, set `unknown`.

### 10.4 vmstat subprocess (fallback mode only)
- Do not use fixed line offsets. Detect data rows by token shape: a line is a data row if all tokens are numeric and the token count matches the header.
- Require at least `--samples` valid data rows; if fewer, set `unknown`.
- Handle extra header lines and blank lines gracefully.

---

## 11) Non-Functional Requirements

- Python 3.9+ compatible.
- Standard library only (no third-party dependencies).
- Readable, maintainable function decomposition.
- Avoid hardcoded magic values except defaults exposed by flags.
- Keep output deterministic for the same sampled data.

---

## 12) Acceptance Tests (Behavioral)

### PSI mode

**Case A: Healthy system**
Input: `psi_some_avg60=0.0`, `psi_full_avg10=0.0`, memory above thresholds, no direct reclaim
Expected: status `ok`, confidence `high`, exit `0`, reasons note no signals fired

**Case B: Sustained some-stall, no corroboration**
Input: `psi_some_avg60=15.0` (above warn threshold), `psi_full_avg10=0.0`, memory above thresholds, no direct reclaim
Expected: status `watch`, confidence `medium`, exit `0`

**Case C: Full-stall**
Input: `psi_full_avg10=8.0` (above pressure threshold)
Expected: status `pressure`, confidence `high`, exit `0`

**Case D: Some-stall + low memory**
Input: `psi_some_avg60=15.0`, memory below threshold, `psi_full_avg10=0.0`
Expected: status `pressure`, confidence `high`, exit `0`

**Case E: Some-stall + direct reclaim**
Input: `psi_some_avg60=15.0`, `direct_reclaim_delta > 0`, memory above thresholds
Expected: status `pressure`, confidence `high`, exit `0`

**Case F: OOM event on otherwise ok system**
Input: `oom_kill_delta=1`, PSI averages near 0, memory above thresholds
Expected: status `ok`, confidence `high`, `oom_event: true` in signals, OOM noted in `reasons`

### Fallback mode

**Case G: Healthy system**
Input: no swap activity, `direct_reclaim_delta=0`, memory above thresholds, `kswapd_reclaim_delta=0`
Expected: status `ok`, confidence `medium`, exit `0`

**Case H: Swap only**
Input: swap active, memory above thresholds, no direct reclaim, no kswapd
Expected: status `watch`, confidence `low`, exit `0`

**Case I: Direct reclaim only**
Input: `direct_reclaim_delta > 0`, no swap, memory not low
Expected: status `watch`, confidence `low`, exit `0`

**Case J: kswapd active only**
Input: `kswapd_reclaim_delta > 0`, no swap, no direct reclaim, memory not low
Expected: status `watch`, confidence `low`, exit `0`

**Case K: Low memory only**
Input: memory below threshold, no swap, no reclaim
Expected: status `watch`, confidence `low`, exit `0`

**Case L: Swap + direct reclaim**
Input: swap active, `direct_reclaim_delta > 0`
Expected: status `pressure`, confidence `medium`, exit `0`

**Case M: Direct reclaim + low memory**
Input: `direct_reclaim_delta > 0`, memory below threshold
Expected: status `pressure`, confidence `medium`, exit `0`

**Case N: Swap + low memory + severe_swap**
Input: `swap_ops_total >= severe_swap_ops`, memory below threshold
Expected: status `pressure`, confidence `medium`, exit `0`

### Error handling

**Case O: Required probe failure**
Input: vmstat subprocess unavailable (fallback), or `/proc/meminfo` unreadable (either mode)
Expected: status `unknown`, non-empty `errors`, exit `2`, no traceback

**Case P: PSI parse failure after detection**
Input: `/proc/pressure/memory` exists but contains malformed data
Expected: status `unknown`, non-empty `errors`, exit `2`; no silent fallback to heuristics

### Output format

**Case Q: JSON mode**
Expected: valid JSON only on stdout, no trailing text, all schema fields present, `probe_mode` matches active path, `null` for inapplicable fields, status/confidence constrained to allowed enums

---

## 13) Suggested Internal Architecture

### Capability detection
- `detect_capabilities() -> dict`: check `/proc/pressure/memory`; return `{"use_psi": bool}`

### Collection
- `run_command(cmd: list[str]) -> tuple[str, str, int, str | None]`: subprocess wrapper; returns (stdout, stderr, returncode, error_message)
- `read_psi() -> dict`: parse `/proc/pressure/memory`; returns raw PSI floats or raises on failure
- `read_meminfo() -> dict`: parse `/proc/meminfo`; returns `mem_total_mb`, `mem_available_mb`
- `sample_vmstat_file(delay: int, psi_mode: bool) -> dict`: read `/proc/vmstat` twice, return deltas for the required key set
- `collect_vmstat_subprocess(samples: int, delay: int) -> dict`: fallback only; returns `swap_in_samples`, `swap_out_samples`
- `collect_top_processes(top_n: int) -> list[str]`: run ps; never raises, returns empty list on failure

### Derivation and classification
- `derive_signals(raw: dict, capabilities: dict, thresholds: dict) -> dict`: compute all derived signals
- `classify(signals: dict, capabilities: dict, thresholds: dict) -> tuple[str, str, list[str]]`: returns (status, confidence, reasons); dispatches to PSI or fallback logic internally

### Rendering
- `render_human(result: dict) -> str`
- `render_json(result: dict) -> str`
- `main()`
