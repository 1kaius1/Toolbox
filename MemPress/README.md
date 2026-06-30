# MemPress

A Linux memory pressure diagnostics CLI for sysadmins and automation pipelines.

MemPress probes the kernel for memory pressure signals and classifies the host as `ok`, `watch`, `pressure`, or `unknown`. It produces either a human-readable report or structured JSON suitable for ingestion by monitoring systems and scripts.

---

## Requirements

- Linux (Red Hat Enterprise Linux 8+, Ubuntu 22.04+, Debian 11+)
- Python 3.9+
- Standard system utilities: `ps` (and `vmstat` on systems without PSI support)
- Root is not required; `/proc/pressure/memory`, `/proc/meminfo`, and `/proc/vmstat` are world-readable on all supported distributions

---

## Installation

Copy the script to any directory on your `PATH`:

```bash
cp mempress.py /usr/local/bin/mempress
chmod +x /usr/local/bin/mempress
```

No dependencies to install. No virtual environment needed.

---

## Quick Start

```bash
# Human-readable report
mempress

# JSON output for scripts and monitoring
mempress --json

# Adjust sampling window
mempress --delay 2 --samples 10

# Tighten memory threshold (warn below 2 GB or 15%)
mempress --min-avail-mb 2048 --min-avail-pct 15.0
```

---

## Probe Modes

MemPress automatically selects the best available detection method:

| Mode | When used | Confidence |
|------|-----------|------------|
| **PSI** (preferred) | Kernel exposes `/proc/pressure/memory` (RHEL 8.2+, Ubuntu 22.04+) | Higher: direct kernel measurement of task stall time |
| **Fallback** | PSI unavailable (RHEL 8.0/8.1, older Debian) | Lower: inferred from swap rates and reclaim counters |

The active probe mode is reported in every output (`probe_mode` in JSON; labelled in the human report header).

---

## Status Values

| Status | Meaning |
|--------|---------|
| `ok` | No significant pressure signals detected. |
| `watch` | One mild indicator present. Monitor; no immediate action required. |
| `pressure` | Two or more corroborating signals confirm active memory pressure. Investigate. |
| `unknown` | A required probe failed. Classification was not possible. |

### Confidence

| Confidence | Meaning |
|------------|---------|
| `high` | PSI mode result with clean data. |
| `medium` | Fallback mode result, or PSI `watch`. |
| `low` | Fallback `watch`, or probe-quality issues. |

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Ran successfully; status is `ok`, `watch`, or `pressure` |
| `1` | Fatal error (invalid arguments or unexpected exception) |
| `2` | Ran but could not classify; status is `unknown` |

---

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--delay <int>` | `1` | Seconds between `/proc/vmstat` samples (both modes); vmstat interval in fallback mode. Min: 1. |
| `--samples <int>` | `5` | vmstat sample count (fallback mode only). Min: 2. |
| `--min-avail-mb <int>` | `1024` | Absolute available-memory floor in MB. |
| `--min-avail-pct <float>` | `10.0` | Available memory percentage floor. |
| `--psi-some-warn <float>` | `10.0` | PSI `some.avg60` threshold for `watch` (PSI mode only). |
| `--psi-full-pressure <float>` | `5.0` | PSI `full.avg10` threshold for `pressure` (PSI mode only). |
| `--severe-swap-ops <int>` | `50` | Aggregate swap ops threshold for `pressure` (fallback mode only). |
| `--severe-direct-delta <int>` | `10` | Direct reclaim delta threshold for severe classification. |
| `--top-n <int>` | `6` | Number of `ps` lines to include in output (including header). Min: 2. |
| `--json` | off | Emit JSON only; no human-readable output. |

---

## Human-Readable Output

```
=== MemPress Memory Pressure Report ===
Host: webserver-01   Time: 2026-06-30T14:22:05Z   Probe: PSI

--- Memory Availability ---
Available: 3,241 MB / 15,872 MB (20.4%)   [OK, above thresholds]

--- PSI Metrics ---
some: avg10=0.12  avg60=0.08  avg300=0.03   [warn threshold: 10.0%]
full: avg10=0.00  avg60=0.00  avg300=0.00   [pressure threshold: 5.0%]

--- Swap Activity ---
pswpin delta: 0   pswpout delta: 0

--- Kernel Reclaim Activity ---
Direct reclaim delta: 0   kswapd delta: 4   pgmajfault delta: 12

--- Top Memory Processes ---
USER       PID  %MEM  VSZ     RSS    COMMAND
postgres  1234   8.2  ...
...

--- Final Assessment ---
Status:     ok
Confidence: high
Why:
  - PSI some.avg60 (0.08%) is below warn threshold (10.0%)
  - PSI full.avg10 (0.00%) is below pressure threshold (5.0%)
  - Available memory (20.4%) is above thresholds
Impact: System memory is adequate. No action required.
```

---

## JSON Output

```json
{
  "version": "3.0",
  "timestamp_utc": "2026-06-30T14:22:05Z",
  "host": "webserver-01",
  "probe_mode": "psi",
  "status": "ok",
  "confidence": "high",
  "summary": "Memory is adequate. No pressure signals detected.",
  "signals": {
    "use_psi": true,
    "psi_some_avg10": 0.12,
    "psi_some_avg60": 0.08,
    "psi_full_avg10": 0.0,
    "low_memory": false,
    "data_quality_ok": true,
    ...
  },
  "thresholds": { ... },
  "reasons": ["PSI some.avg60 (0.08%) below warn threshold (10.0%)", ...],
  "errors": [],
  "top_processes": ["USER  PID  %MEM ...", "postgres  1234  8.2 ..."]
}
```

Fields not applicable to the active probe mode are set to `null` rather than omitted; the schema is the same regardless of probe mode.

---

## Use in Automation

```bash
# Simple shell gate
if ! mempress --json | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d['status']=='ok' else 1)"; then
    echo "Memory pressure detected; aborting deployment"
    exit 1
fi

# Feed into monitoring (e.g., Prometheus textfile collector)
mempress --json > /var/lib/node_exporter/textfile/mempress.json

# Cron check with alert on pressure
*/5 * * * * mempress --json | tee /tmp/mempress.json | \
    python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(2 if d['status']=='pressure' else 0)" || \
    mail -s "Memory pressure on $(hostname)" ops@example.com < /tmp/mempress.json
```

---

## Tuning Thresholds

The defaults are conservative by design.

**PSI thresholds** (`--psi-some-warn`, `--psi-full-pressure`): PSI values are percentages of wall-clock time tasks spent stalled. A system idling between requests may briefly show `some.avg10 > 0` during GC pauses; `some.avg60 >= 10%` is a more reliable watch signal. `full.avg10 >= 5%` means all useful work was blocked for 5% of the last 10 seconds, which is genuine pressure.

**Memory floor** (`--min-avail-mb`, `--min-avail-pct`): Both conditions use OR; either one being true sets `low_memory`. On systems with very large RAM, `--min-avail-mb` may need raising; on systems with limited RAM, `--min-avail-pct` may be more useful.

---

## Technical Reference

- [SPEC.md](SPEC.md): full behavioural specification, data model, and classification policy
- [ARCHITECTURE.md](ARCHITECTURE.md): design decisions and rationale
- [CHANGELOG.md](CHANGELOG.md): version history
