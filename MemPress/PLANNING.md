# MemPress: Implementation Plan

## Goal

Deliver `mempress.py`, a single-file Python 3.9+ CLI (stdlib only) that classifies Linux memory pressure as `ok`, `watch`, `pressure`, or `unknown`, with both human-readable and JSON output modes. Full behavioral contract is in [SPEC.md](SPEC.md).

---

## File Layout

```
MemPress/
  mempress.py       # the main script
  SPEC.md
  PLANNING.md
  CHANGELOG.md
  ARCHITECTURE.md
  README.md
```

---

## Implementation Phases

### Phase 1: Skeleton and argument parsing

- `main()` entry point with `if __name__ == "__main__"` guard
- `argparse` setup covering all flags from SPEC §3.1
- Validate arg constraints (min values for `--delay`, `--samples`, `--top-n`) and exit `1` on violation
- Stub `run(args)` that will hold the execution logic

### Phase 2: Capability detection

- `detect_capabilities() -> dict`: attempt to open `/proc/pressure/memory`; return `{"use_psi": bool}`
- First call inside `run(args)`; gates which probe path executes

### Phase 3: Collection layer

Each probe is an independent function returning a `dict` of raw metrics plus an `errors` list. None of these functions should raise.

Functions to implement:

| Function | Probe source |
|----------|-------------|
| `read_psi()` | `/proc/pressure/memory` |
| `read_meminfo()` | `/proc/meminfo` |
| `sample_vmstat_file(delay, psi_mode)` | `/proc/vmstat` × 2 |
| `collect_vmstat_subprocess(samples, delay)` | `vmstat` subprocess |
| `collect_top_processes(top_n)` | `ps aux --sort=-%mem` |
| `run_command(cmd)` | subprocess helper |

Parse all values by key/field name, never by line number or column index (see SPEC §10).

### Phase 4: Signal derivation

- `derive_signals(raw, capabilities, thresholds) -> dict`
- Computes all derived booleans and scalars defined in SPEC §4.5/§4.6
- Sets `data_quality_ok` based on which required probes succeeded
- Returns a flat dict of all signals; classification reads only from this dict

### Phase 5: Classification

- `classify(signals, capabilities, thresholds) -> tuple[str, str, list[str]]`
  - Returns `(status, confidence, reasons)`
  - Evaluates in order: `unknown → pressure → watch → ok`
  - Dispatches internally to PSI logic (SPEC §5.2) or fallback logic (SPEC §5.3)
  - Confidence is determined by the table in SPEC §6
  - `oom_event` is appended to reasons regardless of status when true

Keep PSI and fallback classification as separate helper functions.

### Phase 6: Rendering

- `render_human(result) -> str`: section-based report per SPEC §7; PSI section only when `use_psi` is true
- `render_json(result) -> str`: serialise the full result dict per SPEC §8; `null` for inapplicable fields, never omit

### Phase 7: Wiring

Connect phases 2–6 inside `run(args)`:

1. `detect_capabilities()`
2. Collect probes appropriate to the active path; merge errors
3. `derive_signals()`
4. `classify()`
5. Assemble the full result dict (version, timestamp, host, probe_mode, all signals, thresholds, reasons, errors, top_processes)
6. Render and print
7. `sys.exit()` with the correct code per SPEC §3.2

Wrap the body of `run()` in a top-level try/except; print a message and exit `1` on unexpected exceptions.

### Phase 8: Validation against acceptance tests

Work through SPEC §12 cases manually or with a lightweight mock layer:

- Cases A–F: PSI path
- Cases G–N: fallback path
- Cases O–P: error handling
- Case Q: JSON output schema

---

## Testing Approach

No test framework dependency. Validate behaviour using one of:

1. Live system: run against the real host; verify exit codes and output structure
2. Controlled mocks: patch the probe file paths and subprocess calls via environment variables or a thin shim to inject known metric values for each §12 case
3. JSON diff: capture `--json` output and assert required fields and enum values (Cases A-Q)

---

## Definition of Done

- `mempress.py` is executable (`chmod +x`) and runs under Python 3.9+
- All §12 cases produce the expected status, confidence, and exit code
- `--json` output parses as valid JSON with all schema fields present
- No uncaught tracebacks in any execution path
- No third-party imports
