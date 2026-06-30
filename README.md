# Toolbox

A collection of sysadmin and power-user tools: CLI diagnostics, GUI utilities, scripts, and other things I find interesting and useful. Each tool is standalone and covers a specific task. CLI tools produce parsable output suitable for automation pipelines; GUI tools are built for interactive use.

---

## Design goals

- **Standalone tools.** Each tool runs without installing dependencies beyond the language runtime it targets. No package managers, no virtualenvs, no containers required to use a tool.
- **Type-appropriate output.** CLI tools support machine-readable output (typically JSON) alongside human-readable output, so they can be consumed by scripts and monitoring agents. GUI tools are built for interactive use and are not expected to emit structured output.
- **Explicit exit codes.** CLI tools use documented, stable exit codes. Scripts can branch on exit code without parsing output.
- **Conservative by default.** Diagnostic tools favor false-negative avoidance over aggressive alerting. A tool should not cry wolf.

---

## Repository layout

```
Toolbox/
  CHANGELOG.md          # repo-level changes (new tools added, structural changes)
  README.md             # this file
  shared/               # code shared across tools
    Python/             # shared Python modules
    Go/                 # shared Go packages
    BASH/               # shared shell functions/libraries
  <ToolName>/           # one directory per tool
    README.md           # usage, options, output format, exit codes
    CHANGELOG.md        # version history for this tool
    SPEC.md             # behavioral specification (if applicable)
    PLANNING.md         # implementation plan (if applicable)
    ARCHITECTURE.md     # design decisions (if applicable)
    <toolname>.<ext>    # the tool itself
```

The root `CHANGELOG.md` tracks repo-level events only: new tools added, tools removed, changes to the shared library, or structural reorganization. It does not duplicate the per-tool changelogs.

---

## Tools

| Tool | Type | Language | What it does |
|------|------|----------|-------------|
| [MemPress](MemPress/README.md) | CLI | Python 3.9+ | Classifies Linux memory pressure as `ok`, `watch`, `pressure`, or `unknown` using PSI or vmstat heuristics |

---

## Adding a new tool

1. Create a subdirectory named after the tool (`PascalCase` by convention).
2. Put the tool's executable or entry point directly in that directory.
3. Write a `README.md` covering: what it does, requirements, usage, all flags, output format, and exit codes.
4. Write a `CHANGELOG.md` using [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.
5. Add a row to the Tools table above and an entry to the root `CHANGELOG.md`.
6. If the tool shares code with others, put that code in `shared/` under the appropriate language subdirectory.

Optional but encouraged for non-trivial tools:
- `SPEC.md`: full behavioural specification written before implementation
- `ARCHITECTURE.md`: design decisions and the reasoning behind them
- `PLANNING.md`: implementation plan and phase breakdown

---

## Shared code

The `shared/` directory holds modules and libraries used by more than one tool. Each language gets its own subdirectory (`Python/`, `Go/`, `BASH/`). A tool that depends on shared code should document that dependency in its own `README.md`.

---

## Conventions

**Languages:** Tools can be written in any language, but Python 3.9+ and Bash are preferred for portability on RHEL 8+/Ubuntu 22.04+ targets. Go is acceptable for tools that benefit from single-binary distribution.

**Tool types:** Each tool defines its own type (CLI, GUI, Service, or similar). There is no requirement that every tool be a CLI or produce parsable output. The type determines what conventions apply: CLI tools should follow the output mode and exit code conventions below; GUI tools follow platform UI guidelines appropriate for the toolkit they use.

**Output modes (CLI tools):** The default output is human-readable. Machine-readable output (JSON) is enabled via a flag (typically `--json`). The JSON schema should be stable and versioned.

**Exit codes (CLI tools):** Document exit codes in the tool's `README.md`. At minimum, distinguish success, classification/operational failure, and fatal/config error. Do not reuse the same exit code for different failure categories.

**No root required:** Tools should run as an unprivileged user wherever possible. If elevated permissions are needed, say so explicitly in the `README.md`.

**Versioning:** Tool versions follow `MAJOR.MINOR.PATCH`. Increment `PATCH` for bug fixes, `MINOR` for new flags or output fields that are backwards compatible, `MAJOR` for breaking changes to the CLI or output schema.
