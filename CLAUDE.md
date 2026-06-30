# Toolbox - Claude Code Project Context

## Project

Toolbox is a personal monorepo of sysadmin and power-user tools for Linux. Tools may be
CLI utilities, GUI applications, background services, or scripts. Each tool lives in its
own subdirectory and is self-contained. The repo targets Red Hat Enterprise Linux 8+,
Ubuntu 22.04+, and Debian 11+. Cross-platform compatibility is not a goal.

The full project conventions are documented in [README.md](README.md).

---

## Repository Layout

```
Toolbox/
  CLAUDE.md             # this file
  CHANGELOG.md          # repo-level changes only (new tools, structural changes)
  CONTRIBUTING.md       # contributor workflow
  LICENSE               # AGPL-3.0-or-later
  README.md
  shared/               # code shared across multiple tools
    Python/
    Go/
    BASH/
  <ToolName>/           # one directory per tool
    CLAUDE.md           # tool-specific context for Claude Code sessions (if needed)
    README.md
    CHANGELOG.md
    SPEC.md             # behavioural specification (non-trivial tools)
    PLANNING.md         # implementation plan (non-trivial tools)
    ARCHITECTURE.md     # design decisions (non-trivial tools)
    <toolname>.<ext>    # the tool itself
```

Root `CHANGELOG.md` tracks only repo-level events. Per-tool changelogs track tool
versions. Do not duplicate entries across both.

---

## Per-Tool Conventions

Every tool directory must contain:
- `README.md`: what it does, requirements, usage, flags, output format, exit codes
- `CHANGELOG.md`: keepachangelog format, updated with every change

Non-trivial tools should also have `SPEC.md`, `PLANNING.md`, and `ARCHITECTURE.md`.
A tool-specific `CLAUDE.md` is appropriate when the tool has enough domain context
that repeating it every session would be costly.

When adding a new tool: create its directory, add the required docs, add a row to the
Tools table in the root `README.md`, and add an entry to the root `CHANGELOG.md`.

---

## Code Style and Formatting

- Never use emojis in any file: code, documentation, comments, or commit messages
- Never use em-dashes or en-dashes; use a regular hyphen where a dash is needed
- Use standard ASCII in code and comments; UTF-8 is acceptable in user-facing strings
- Use underscores in all filenames, not hyphens or spaces
- Comments explain WHY, not WHAT; simple operations need no comment

### License Headers

Every new source file must include the SPDX license identifier as the first or second
line (after the shebang if present):

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
```

```go
// SPDX-License-Identifier: AGPL-3.0-or-later
```

```bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
```

Never omit the license header from a new source file. Never change an existing license
header without explicit instruction.

---

## Language Conventions

### Python

- Target Python 3.9+ (RHEL 8 ships 3.9; no f-string or typing features beyond 3.9)
- Prefer stdlib; discuss any third-party dependency before introducing it
- Format with `black`; lint with `flake8` or `ruff`
- Type hints encouraged but not required for internal/private functions

### Go

- Format with `gofmt` or `goimports`
- Vet with `go vet ./...`
- Handle all errors explicitly; never discard an error return value

### BASH

- `set -euo pipefail` at the top of every script
- Quote all variable expansions: `"${var}"` not `$var`
- Use `[[ ]]` for conditionals, not `[ ]`
- Use `shellcheck` before committing

---

## Commits and Branch Workflow

### Branches

- Never commit directly to `master`
- Never push directly to `master`
- All changes go through a feature branch and pull request
- Branch naming: `type/short-description` (e.g. `feat/psi-probe`, `fix/json-schema`)
- One logical change per branch

### Commit Messages

Follow Conventional Commits:

```
type(scope): short imperative description under 72 characters

Optional body explaining WHY this change was made. Wrap at 72 characters.
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

- No emoji in commit messages
- Imperative mood: "add feature" not "added feature"
- `CHANGELOG.md` must be updated in the same commit as the code change it describes

### Pull Requests

- Target branch: `master`
- All tests must pass before a PR is suggested
- PR description must summarize what changed and why
- Keep PRs small and focused

---

## Scope Control

- Only modify files directly relevant to the task
- Related files (e.g. tests for changed code, docs for changed behaviour) may be
  updated when clearly necessary
- Never refactor, reformat, or restructure code that was not part of the request
- Never rename variables, functions, or files opportunistically
- If improvements outside scope are noticed, mention them; do not make them unilaterally

### Before Making Changes

1. Read the tool's `CLAUDE.md` (if present), `SPEC.md`, and `ARCHITECTURE.md`
2. Review existing code style in the affected files
3. Check `CHANGELOG.md` format and current version
4. Confirm the approach before starting on non-trivial changes

### Destructive Operations

- Always confirm before deleting any file
- Always confirm before overwriting a file not explicitly part of the task
- Never delete directories without explicit instruction
- When in doubt, ask

---

## Dependencies

- Never introduce a new dependency without discussing it with the user first
- Prefer stdlib solutions over third-party packages
- If a dependency is needed, explain why and wait for approval before adding it
- Document new dependencies in the appropriate file (`go.mod`, `requirements.txt`, etc.)

---

## Tool-Type Conventions

### CLI Tools

- Use `argparse` (Python) or `flag`/`cobra` (Go) for argument parsing
- Support `--help` with clear usage examples
- Support `--version` returning the version string
- Use documented, stable exit codes; distinguish success, operational failure, and
  fatal/config error; do not reuse the same code for different failure categories
- Write errors to stderr, data to stdout
- Support machine-readable output (typically `--json`) in addition to human-readable
- Default output is human-readable

### GUI Tools

- Use an appropriate cross-desktop toolkit (GTK, Qt, or similar)
- Provide keyboard shortcuts for common actions
- Never hardcode font sizes, colors, or paths
- Document the toolkit dependency and installation steps in the tool's `README.md`

### Services and Daemons

- Handle `SIGTERM` and `SIGINT` for graceful shutdown
- Handle `SIGHUP` for configuration reload where appropriate
- Provide a systemd unit file
- Log to stderr by default; support configurable log destination
- Document all runtime flags and environment variables in the tool's `README.md`

---

## Documentation

- Keep documentation current with code changes; a behaviour change without a doc update
  is an incomplete change
- Update `CHANGELOG.md` in the same commit as the change it describes
- Update `ARCHITECTURE.md` when structural or design decisions change
- Update `PLANNING.md` when goals, milestones, or open questions change
- Update `SPEC.md` when the behavioural contract changes

---

## Error Handling and Logging

- Handle all errors explicitly; no ignored return values, no silent failures
- Provide clear, actionable error messages: what failed and why
- Use appropriate log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
- Make log verbosity configurable via flag or environment variable
- Do not log sensitive data (credentials, tokens, PII)
- Exit gracefully on fatal errors with a non-zero status code

---

## Security

- Never commit credentials, tokens, API keys, or secrets of any kind
- Use environment variables or files under `~/.config/app_name/` for sensitive runtime data
- Validate and sanitize all user input at system boundaries
- Use prepared statements for all database queries
- Apply the principle of least privilege for file permissions and process capabilities
- Do not store sensitive data in plain text without explicit instruction
