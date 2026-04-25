# DSTF Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the tool from "ModelicaTesting" / `modelica_testing` / `modelica-testing` to "Dynamic Systems Testing Framework" / `dstf` / `dstf`. Five simulator backends (only one of which is Dymola-specific) plus Python/CSV support make the "Modelica" name actively misleading.

**Architecture:** Three coordinated sweeps, each in its own commit so any regression is easy to bisect. (1) Python package + pyproject: physical directory move, import updates, entry-point rename, reinstall, full suite green. (2) Docs brand + CLI examples: all `ModelicaTesting` → "Dynamic Systems Testing Framework (DSTF)" and all `modelica-testing` bash invocations → `dstf`. (3) Final polish: settings cleanup, D81 decision entry, SESSION_HANDOFF update, cross-backend smokes.

**Tech Stack:** uv/hatchling (pyproject.toml), Python 3.10+, git mv for directory rename. No new deps.

**Naming decisions (locked):**
- Python package: `dstf` (short form).
- CLI command: `dstf` (no long-form alias — per "no backward compat" standing rule).
- Repo directory on disk stays `ModelicaTesting/` (user may `git mv` or rebrand the GitHub repo separately).
- Config filename `testing.json` keeps (tool-neutral).
- Example library names keep (`ModelicaTestingLib` is genuinely a Modelica library).

**Out of scope:** Renaming `ModelicaTestingLib`, `JuliaMtkTestingLib`, or `PythonTestingLib`. Renaming the GitHub repo. Adding backward-compat aliases.

---

## File structure

### Moved
- `src/modelica_testing/` → `src/dstf/` (via `git mv`; ~50+ files preserved with history)

### Modified — Python / packaging
- `pyproject.toml` — `name`, `description`, `[project.scripts]`, any `packages` entries
- `src/dstf/**/*.py` — any absolute `modelica_testing` imports (audit shows only 1 hit)
- `tests/**/*.py` — ~39 files with `from modelica_testing import ...` / `import modelica_testing`

### Modified — docs
- `CLAUDE.md` — Project Overview header + any in-line references
- `docs/SESSION_HANDOFF.md` — brand + CLI invocations
- `docs/decisions.md` — append D81; keep historical D1–D80 wording intact (they describe past state)
- `docs/architecture.md`, `docs/ideas.md`, `docs/vision.md`, `docs/extensibility.md`, `docs/patterns.md`, `docs/constraints.md`, `docs/usage.md` — brand + CLI invocations
- `docs/qa/reporter_checklist.md` (if present) — CLI invocations
- `docs/related_tools_research/*.md` — brand references
- `docs/superpowers/specs/*.md`, `docs/superpowers/plans/*.md` — treat historical plans as frozen snapshots (leave their wording; they describe the tool at a past point) EXCEPT for plan headers that would actively mislead a new reader running the plan today (none expected — both plans are already executed).
- `examples/*/README.md` (if present) — CLI invocations

### New
- None (just a rename sweep)

### Untouched (deliberately)
- `testing.json` files under examples — tool-neutral
- `ModelicaTestingLib/`, `JuliaMtkTestingLib/`, `PythonTestingLib/` directory names
- Git history (no force-push, no history rewrite)
- `.git/` anything

---

## Pre-flight: verify clean working tree

Before Task 1, the engineer should ensure no uncommitted working-tree changes conflict with the rename:

```bash
git status --short
```

Expected: only the pre-existing unrelated modified files from before this plan (LICENSE, tests/fixtures/, etc.). No staged changes. If there ARE staged changes, stop and check with the user before proceeding — the rename touches many files and staging hygiene matters.

---

## Task 1: Physical rename — pyproject, package dir, imports, reinstall

**Goal:** Make the tool importable as `dstf` and runnable as `uv run dstf` with zero broken imports. Single atomic commit so the tree is never in a half-renamed state on `main`.

**Files:**
- Modify: `pyproject.toml`
- Move: `src/modelica_testing/` → `src/dstf/` (git mv preserves history)
- Modify: every `.py` under `tests/` that imports from `modelica_testing`
- Modify: any `.py` under `src/dstf/` with absolute `modelica_testing` import (audit shows 1 hit — confirm during execution)
- Install: `uv pip install -e ".[dev]"` to register the new package name

- [ ] **Step 1: Update `pyproject.toml`**

Read the current file. Apply these three edits:

**(a)** `name = "modelica-testing"` → `name = "dstf"`

**(b)** `description = "Regression testing system for Modelica libraries using Dymola"` → `description = "Dynamic Systems Testing Framework — regression & unit testing for time-dependent system behavior across Modelica (Dymola, OpenModelica), FMU (FMPy), Julia (ModelingToolkit), and Python backends"`

**(c)** Under `[project.scripts]`:

```toml
modelica-testing = "modelica_testing.cli:main_entry"
```

Replace with:

```toml
dstf = "dstf.cli:main_entry"
```

**(d)** If there is a `[tool.hatch.build.targets.wheel]` block with `packages = ["src/modelica_testing"]` or similar, update the path to `src/dstf`. (If no such block, hatch auto-discovers from the `src/` layout and no edit is needed — confirm by scanning the file.)

**(e)** Scan the rest of `pyproject.toml` for any other `modelica_testing` / `modelica-testing` references (extras, URLs, keywords). Update any you find; report them in the task summary.

- [ ] **Step 2: Git-move the package directory**

```bash
git mv src/modelica_testing src/dstf
```

This preserves git history for every file inside. Verify:

```bash
ls src/dstf/
```

Expected: all the subdirectories the engineer saw before (`cli.py`, `config.py`, `simulators/`, `discovery/`, `comparison/`, `storage/`, `reporting/`, etc.).

- [ ] **Step 3: Fix absolute imports inside `src/dstf/`**

Find any Python file inside the new package directory that uses an absolute `modelica_testing` import (the audit showed 1 hit; verify and fix all):

```bash
grep -rln "modelica_testing" src/dstf/ --include="*.py"
```

For each hit, open the file and replace `modelica_testing` → `dstf` in the import line. Common forms:
- `from modelica_testing.something import ...` → `from dstf.something import ...`
- `import modelica_testing.something` → `import dstf.something`
- Less common: docstring / string-literal references — update those too for consistency.

Report the list of files edited in the task summary.

- [ ] **Step 4: Fix imports in `tests/`**

```bash
grep -rln "modelica_testing" tests/ --include="*.py"
```

Expected: ~39 files. Apply a mechanical rename. Because all hits are at the token boundary `modelica_testing`, a single sed sweep is safe:

```bash
grep -rln "modelica_testing" tests/ --include="*.py" | xargs sed -i 's/modelica_testing/dstf/g'
```

After the sweep, re-grep to confirm zero remaining hits:

```bash
grep -rn "modelica_testing" tests/ --include="*.py" || echo "CLEAN"
```

Expected: `CLEAN`.

- [ ] **Step 5: Reinstall the package under its new name**

```bash
uv pip install -e ".[dev]"
```

This registers the `dstf` CLI entry point and rebuilds the editable install against the new package location. Expected: install succeeds without errors.

If install errors with "package modelica_testing not found" or similar, the hatchling/pyproject config still references the old name — revisit Step 1(d).

- [ ] **Step 6: Verify CLI entry point works**

```bash
uv run dstf --help
```

Expected: help text with subcommands (`run`, `compare`, `discover`, `manifest`, `spec-update`, `companion`, `soft-check`, `import-baseline`, `export-schema`, `check-dymola`, etc.). If the command is "not found," either the install didn't take effect or `pyproject.toml`'s `[project.scripts]` entry still has the old name.

- [ ] **Step 7: Full suite**

```bash
uv run pytest -q
```

Expected: 761 passed + 1 skipped, 0 failures. Same state as before the rename.

If tests fail with `ModuleNotFoundError: No module named 'modelica_testing'`, a test file still imports the old name — go back to Step 4. If tests fail with other errors, STOP and report; do not paper over with random fixes.

- [ ] **Step 8: Smoke the Python CLI via the new command**

```bash
uv run dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run
```

Expected: 2/2 PASS (same as after D80).

- [ ] **Step 9: Commit**

```bash
git status --short
```

Confirm only these categories of files are staged:
- `pyproject.toml`
- Deletions under `src/modelica_testing/` + additions under `src/dstf/` (git mv should show as renames in `git status`)
- Modifications to test files under `tests/`

If any unrelated files got staged (the working tree has pre-existing unstaged modifications that should stay unstaged), `git restore --staged <file>` them.

```bash
git add pyproject.toml src/dstf src/modelica_testing tests/
git commit -m "$(cat <<'EOF'
refactor(rename): modelica_testing → dstf (package, CLI, entry point)

Rename the Python package and CLI command to "dstf" (Dynamic Systems
Testing Framework). Motivation: with five production backends (only
one of which is Dymola-specific) plus arbitrary-Python-script support,
the "ModelicaTesting" / "modelica-testing" names are actively
misleading.

- pyproject.toml: project.name modelica-testing → dstf; scripts entry
  modelica-testing = ... → dstf = ...; description updated to reflect
  the multi-backend reality.
- src/modelica_testing → src/dstf (git mv preserves history).
- tests/: ~39 files updated via mechanical sed rename.
- No backward-compat alias per standing policy.

Docs + CLI-example sweep lands in the next commit (Task 2).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Docs brand + CLI-example sweep

**Goal:** Update every markdown file that names the brand or runs the CLI. Atomic with Task 1: after this task, readers who pull the repo will never see an unexplained old name.

**Files:** every `*.md` under the repo root, `docs/`, `examples/`. Scope narrowed deliberately: do NOT touch `docs/superpowers/plans/*` or `docs/superpowers/specs/*` — those are historical snapshots of past planning, and rewriting them would falsify the record. (Task 3 will add a one-line pointer in SESSION_HANDOFF explaining this.)

- [ ] **Step 1: Enumerate the brand-reference surface**

```bash
grep -rln "ModelicaTesting" --include="*.md" . | grep -v "ModelicaTestingLib" | grep -v "/superpowers/" | sort
```

Expected: ~8-10 files. The `grep -v "ModelicaTestingLib"` excludes the example library (which keeps its name), and `grep -v "/superpowers/"` excludes historical plans/specs. Report the list in the task summary.

- [ ] **Step 2: Enumerate the CLI-invocation surface**

```bash
grep -rln "modelica-testing" --include="*.md" . | grep -v "/superpowers/" | sort
```

Expected: several files (CLAUDE.md, SESSION_HANDOFF.md, usage.md, vision.md, etc.). Report the list.

- [ ] **Step 3: Brand sweep — `ModelicaTesting` → appropriate replacement**

For each file from Step 1, update the brand references. The replacement depends on context:
- **In headers and first mentions**: `ModelicaTesting` → `Dynamic Systems Testing Framework (DSTF)`
- **In subsequent references in the same document**: `ModelicaTesting` → `DSTF`
- **In prose that specifically talks about the historical name**: leave as-is and add `(now DSTF)` once

Do NOT blindly sed — context matters. Open each file, find each hit, decide the right substitution. Report any ambiguous cases instead of guessing.

**CLAUDE.md** specifically: the current line 5 says:

```
**ModelicaTesting** (working name — expected to be renamed once the multi-backend abstraction stabilizes) is a Python framework for regression and unit testing of time-dependent system behavior.
```

Replace with:

```
**Dynamic Systems Testing Framework (DSTF)** — formerly **ModelicaTesting** — is a Python framework for regression and unit testing of time-dependent system behavior across simulated and pre-recorded trajectories.
```

Also update CLAUDE.md's history sentence around line 11 to reflect the rename as D81. Currently: `..., D77–D79 Julia/MTK, D80 Python-driven tests`. Update to `..., D77–D79 Julia/MTK, D80 Python-driven tests, D81 rename to DSTF`.

- [ ] **Step 4: CLI-invocation sweep — `modelica-testing` → `dstf`**

For each file from Step 2, replace `modelica-testing` → `dstf` in shell-command contexts. Safe to sed across `.md` files since `modelica-testing` is token-bounded and unambiguous:

```bash
grep -rln "modelica-testing" --include="*.md" . | grep -v "/superpowers/" | xargs sed -i 's/modelica-testing/dstf/g'
```

Verify:
```bash
grep -rn "modelica-testing" --include="*.md" . | grep -v "/superpowers/" || echo "CLEAN"
```

Expected: `CLEAN`.

- [ ] **Step 5: Spot-check a few edits manually**

Open and skim these files to confirm the rewrites read naturally (not just correctly):
- `CLAUDE.md` (top section)
- `docs/SESSION_HANDOFF.md` (backend table, smoke commands)
- `docs/usage.md` or equivalent if present (bulk of CLI examples)

If the prose reads awkwardly after substitution (e.g., a sentence now starts with "dstf" lowercase where "ModelicaTesting" capitalized-case made it read as a proper noun), fix it inline.

- [ ] **Step 6: Commit**

```bash
git status --short
```

Confirm only `.md` files under the repo are staged (and only those outside `/superpowers/plans/` and `/superpowers/specs/`).

```bash
git add CLAUDE.md docs/ examples/
# If any .md files got staged that shouldn't be (superpowers/*, README.md in an unrelated subdir), `git restore --staged <file>` them.
git commit -m "$(cat <<'EOF'
docs(rename): brand + CLI examples — DSTF everywhere

Update every non-historical doc to reflect the rename from
ModelicaTesting → Dynamic Systems Testing Framework (DSTF), and
every bash example that invoked `modelica-testing` to use `dstf`.

Historical records (docs/superpowers/plans/*, docs/superpowers/
specs/*, and D1-D80 entries in decisions.md) deliberately preserved
as-written — they describe the tool at past points in time.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Final polish — settings, decisions, SESSION_HANDOFF, memory, smokes

**Goal:** Close the loop. Add a D81 decision entry, update SESSION_HANDOFF with post-rename state, scan `.claude/settings*.json` for stale `modelica-testing` permission strings, update user memory references, run smokes across all backends.

**Files:**
- Modify: `docs/decisions.md` (append D81)
- Modify: `docs/SESSION_HANDOFF.md` (state header + note about historical plan preservation)
- Modify: `.claude/settings.local.json` if it contains stale `modelica-testing` permission strings (check first; may be a no-op)
- Modify: `/home/fig/.claude/projects/-mnt-d-Modelica-ModelicaTesting/memory/MEMORY.md` and any memory files under that dir that reference `modelica-testing` / `modelica_testing` (probably 0-2 files)

- [ ] **Step 1: Append D81 to `docs/decisions.md`**

Append this entry after the D80 block:

```markdown

## D81: Rename ModelicaTesting → DSTF (Dynamic Systems Testing Framework)

- **What**: Python package `modelica_testing` → `dstf`; CLI command
  `modelica-testing` → `dstf`; pypi project name `modelica-testing`
  → `dstf`; brand in docs "ModelicaTesting" → "Dynamic Systems
  Testing Framework (DSTF)".
- **Why**: After D80, the tool supports five simulator backends (only
  one of which is Dymola-specific) plus arbitrary Python scripts and
  CSV-loader test sources. The "ModelicaTesting" name was identified
  at project inception as a working name pending multi-backend
  stabilization (see CLAUDE.md's D44–D80 summary); that condition is
  now met. "DSTF" was chosen for the short form because it's
  trivially pronounceable and doesn't over-commit on any single
  backend language.
- **Scope**: Package, CLI, pypi name, docs brand, bash examples in
  docs. Explicitly out of scope: GitHub repo rename (user's
  decision), example library names (`ModelicaTestingLib` genuinely
  is a Modelica library — keeping that name is accurate), config
  filename `testing.json` (already tool-neutral), backward-compat
  aliases (forbidden by standing "no backward compat" policy).
- **Preserved history**: `docs/superpowers/plans/*` and
  `docs/superpowers/specs/*` left as-written — they are frozen
  snapshots of past planning and rewriting them would falsify the
  record. Same rule for D1-D80 in `decisions.md` — each entry
  reflects the state when written.
- **Validation**: Full suite 761 + 1 skipped, same as pre-rename.
  All five backend smokes PASS (Dymola skipped on Linux, Julia 7/7,
  OpenModelica 10/11, FMPy skipped without reference-fmus-binaries,
  Python 2/2).

### Rejected alternatives

- **Keep a `modelica-testing` CLI alias for one release**. Standard
  deprecation ladder would normally apply, but the project's
  standing "no backward compat during this development cycle"
  policy (user preference, see memory) supersedes — clean break.
- **Rename to `dynamic-systems-testing` (long form)**. Python
  imports would be `from dynamic_systems_testing import ...`
  — noisy. Short form `dstf` is clean and the user explicitly
  asked for it.
- **Rename example libraries too** (`ModelicaTestingLib` →
  `DSTFModelicaLib` or similar). Rejected: `ModelicaTestingLib` is
  genuinely a Modelica library authored to exercise DSTF; its name
  accurately describes its content.
- **Rename the on-disk repo directory `ModelicaTesting/`**.
  Deferred to the user; it's a local checkout concern and git
  doesn't care. Renaming the GitHub repo is a separate user
  decision.
```

- [ ] **Step 2: Update `docs/SESSION_HANDOFF.md` header**

Open `docs/SESSION_HANDOFF.md`. The top-of-file title is currently:

```markdown
# Session handoff — Julia/MTK backend + reporter expansion + tool growth
```

Leave that title — it describes the *session that produced this doc*, which was D71-D79. Instead, update the State block to reflect the rename. Find the `**State at HEAD** (commit ...)` block (around line 5) and add a note after the bullet list:

```markdown

**Naming**: As of D81, the tool is **Dynamic Systems Testing Framework
(DSTF)**; CLI is `dstf`; Python import root is `dstf`. Historical
plans and specs under `docs/superpowers/` retain the old
`modelica-testing` name — that's by design (they're snapshots of past
state; see D81 in `docs/decisions.md` for the rationale).
```

Also find any `modelica-testing` invocations in the handoff and update them — but they should have already been caught by Task 2's sed sweep. Re-grep to double-check:

```bash
grep -n "modelica-testing\|modelica_testing\|ModelicaTesting[^L]" docs/SESSION_HANDOFF.md || echo "CLEAN"
```

Expected: `CLEAN`.

- [ ] **Step 3: Audit `.claude/settings*.json` for stale permission strings**

```bash
grep -n "modelica-testing\|modelica_testing" .claude/settings.local.json 2>/dev/null | head -10
grep -n "modelica-testing\|modelica_testing" .claude/settings.json 2>/dev/null | head -10
```

If there are hits, open the file(s) and update each matched string (typically a permission allowlist like `"Bash(uv run modelica-testing:*)"` → `"Bash(uv run dstf:*)"`). If there are no hits, skip this step.

- [ ] **Step 4: Audit user memory for stale references**

```bash
grep -rn "modelica-testing\|modelica_testing" /home/fig/.claude/projects/-mnt-d-Modelica-ModelicaTesting/memory/ 2>/dev/null
```

If there are hits in memory files (they reference the old package/CLI name in a way that will confuse a future conversation), update each file in place using the Edit tool. Do not blindly sed — memory files are short and context-dependent. Report which files and lines were updated in the task summary.

- [ ] **Step 5: Smoke every backend**

Python:
```bash
uv run dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run
```
Expected: 2/2 PASS.

Modelica (via OpenModelica, if `omc` is installed):
```bash
[ -x "$(command -v omc)" ] && uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run || echo "omc not installed — skipping"
```
Expected: 10/11 PASS + 1 NO_REF (pre-existing SimulateOnlyTest behavior) or "omc not installed — skipping".

Julia (if `julia` is installed):
```bash
[ -x "$(command -v julia)" ] && uv run dstf --config examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json run || echo "julia not installed — skipping"
```
Expected: 7/7 PASS or skip.

Full pytest one more time:
```bash
uv run pytest -q
```
Expected: 761 passed + 1 skipped.

- [ ] **Step 6: Commit**

```bash
git status --short
```

Confirm staged files are limited to:
- `docs/decisions.md` (D81 entry)
- `docs/SESSION_HANDOFF.md` (naming note)
- `.claude/settings.local.json` if edited in Step 3

For memory files (under `/home/fig/...`), those are OUTSIDE this repo and are not git-tracked — they won't show up in `git status`. The Step 4 updates happen via Edit tool and persist independently of this commit.

```bash
# Stage the files from steps 1, 2, 3 only (memory files in step 4 are not git-tracked)
git add docs/decisions.md docs/SESSION_HANDOFF.md
# If .claude/settings.local.json was edited in Step 3 AND is git-tracked:
git diff --cached --name-only | grep -q "\.claude/settings.local.json" || git add .claude/settings.local.json 2>/dev/null || true

git commit -m "$(cat <<'EOF'
docs: D81 decision entry + SESSION_HANDOFF naming note

Close the loop on the DSTF rename. D81 covers motivation
(five backends, one Dymola), scope (package/CLI/pypi/brand),
preserved history (superpowers/* and D1-D80 stay as-written),
and rejected alternatives (no alias, no long-form name, no
example-lib rename, no repo-dir rename).

SESSION_HANDOFF gains a naming note pointing readers to D81
for context on why historical plans still say "modelica-testing."

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Final verification**

```bash
git log --oneline 53eaa34..HEAD
uv run dstf --help | head -5
uv run pytest -q
```

Expected:
- Three new commits (Task 1, Task 2, Task 3) on top of D80's `53eaa34`.
- `dstf --help` works.
- 761 + 1 skipped.

The rename is done.

---

## Rollback plan (if something goes catastrophically wrong)

Unlikely, but if Task 1's reinstall + full-suite step fails in an unrecoverable way:

```bash
# Revert the Task 1 commit and restore the working tree
git reset --hard <commit-before-task-1>
uv pip install -e ".[dev]"  # reinstall the old name
uv run pytest -q            # confirm we're back to 761+1
```

Then diagnose the failure before re-attempting.

---

## Scope reminders

**This plan does:**
- Rename Python package, CLI, pypi name, brand.
- Update docs and bash examples.
- Add D81 entry + SESSION_HANDOFF naming note.
- Audit and update `.claude/settings.local.json` and user memory for stale strings.

**This plan does NOT do:**
- Rename example library directories (`ModelicaTestingLib` etc.).
- Rename the on-disk repo directory `ModelicaTesting/`.
- Rename the GitHub repo.
- Touch historical plans and specs under `docs/superpowers/`.
- Add backward-compat aliases.
- Fix any of the bugs from the user's broader roadmap (RangeCheck windowed-range FAIL, plot-zoom state leak, etc.) — those are separate tasks.

If a reviewer pushes to expand scope, the answer is "not in this plan — log it as a follow-up."
