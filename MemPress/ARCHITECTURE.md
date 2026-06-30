# MemPress - Architecture Decisions

Significant design decisions, the reasoning behind each, and alternatives considered.

---

## ADR-001: PSI as primary probe, vmstat heuristics as fallback

The original spec inferred memory pressure indirectly from vmstat `si`/`so` samples, `/proc/vmstat` direct-reclaim deltas, and `free -m` output. These are side effects of pressure, not a direct measurement.

`/proc/pressure/memory` (PSI) is a kernel-maintained metric reporting the fraction of wall-clock time during which tasks were stalled waiting for memory. It measures impact on running workloads directly. MemPress uses PSI as the primary signal when available, falling back to vmstat heuristics only when PSI is absent.

On RHEL 8.2+ and Ubuntu 22.04+ (kernel >= 4.18.0-193 and >= 5.15 respectively), classification is more accurate and the kernel's own time-averaging reduces noise. On RHEL 8.0/8.1 (kernel 4.18 without the PSI backport), the fallback path runs automatically. PSI-mode confidence is rated higher than fallback-mode confidence for the same status (see ADR-005).

Alternatives: PSI-only with no fallback was rejected because RHEL 8.0/8.1 hosts are in scope. Vmstat heuristics alone were rejected because PSI is strictly better where available.

---

## ADR-002: Single Python script, standard library only

The tool is intended for sysadmin use on production Linux hosts. Dependency management (pip, virtualenvs, package installation) creates deployment friction.

MemPress ships as a single executable Python script (`mempress.py`) with no third-party imports. Python 3.9+ is the minimum version; it is available on all target distributions (RHEL 8, Ubuntu 22.04, Debian 11).

Deployment is `scp mempress.py host:/usr/local/bin/ && chmod +x`. No installer needed. Stdlib covers file reads, subprocess calls, JSON serialisation, and argument parsing without issue.

---

## ADR-003: No silent fallback after PSI detection

A naive implementation might detect PSI, attempt to parse `/proc/pressure/memory`, hit a permission or format error, and silently switch to vmstat heuristics. The result would carry no indication that the expected primary probe failed.

If PSI is detected at startup but subsequently fails to parse, MemPress sets status to `unknown` and records the error. It does not switch probe modes mid-run.

The result always identifies which probe path produced it (`probe_mode` in JSON, mode label in the human report). Operators are not misled into treating a degraded result as a normal one. If `/proc/pressure/memory` disappears mid-run (possible on a container restart), the user gets `unknown` with an error rather than a silently downgraded result.

---

## ADR-004: /proc/meminfo instead of `free -m`

The original spec used `free -m` (a subprocess) to get `MemTotal` and available memory. `/proc/meminfo` exposes the same data as a file.

MemPress reads `/proc/meminfo` directly in both probe paths, locating `MemTotal` and `MemAvailable` by key name.

This eliminates one subprocess call and its associated parse fragility. `MemAvailable` (kernel 3.14+) is the kernel's own estimate of memory that can be freed without swapping, more accurate than any userspace calculation of free + reclaimable. Parsing by key name rather than by line number or column index handles layout differences across distributions and kernel versions.

---

## ADR-005: Confidence tier coupled to probe mode

PSI measures task stall time directly. The fallback path infers pressure from side effects (swap I/O rates, direct reclaim page counts). The two sources are not equivalent in reliability.

Confidence is one tier lower in fallback mode than in PSI mode for the same classification status:

| status | PSI | Fallback |
|--------|-----|---------|
| ok | high | medium |
| watch | medium | low |
| pressure | high | medium |

A consumer checking the `confidence` field can distinguish a fallback `ok` from a PSI-confirmed `ok` without also parsing `probe_mode`.

---

## ADR-006: `ps` failure is non-fatal

The top-process listing from `ps` is informational context for a human operator. It is not an input to the classification algorithm. Requiring `ps` to succeed would trigger `unknown` on systems where `ps` is restricted or absent.

A `ps` failure yields an empty `top_processes` list and an entry in `errors`, but does not set `data_quality_ok = false` and does not change the classification.

Classification is not blocked by `ps` unavailability (e.g., restricted container environments). The `errors` list records the failure so operators know the process list is missing.

---

## ADR-007: Strict evaluation order for classification

Without a defined evaluation order, the classification conditions for `watch` and `ok` can overlap. A system with `kswapd_active` would match both `watch` and a naive `ok` definition.

MemPress evaluates statuses top-to-bottom and takes the first match: `unknown -> pressure -> watch -> ok`. `ok` is a catch-all reached only when no higher-priority condition matches.

Classification is deterministic for any combination of signal values. The `ok` conditions in the fallback path explicitly exclude `kswapd_active`, but the evaluation order provides a second guarantee. Any new signal combination that warrants `watch` will be caught correctly as long as it is added before the `ok` check.
