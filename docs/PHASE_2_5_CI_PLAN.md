# Phase 2.5 — GitHub Actions CI plan

**Status**: deferred until the repo goes public (or until you decide to spend
private-repo Action minutes). All groundwork from Phase 2.4 is in place;
this is a ~30-minute job when you're ready.

## Goal

Prove the FMPy path runs end-to-end on a clean GitHub-hosted runner with
**no Modelica/Dymola** present. This closes the eval report's Gap F
("CI-ready" was aspirational pre-FMPy).

## Pre-conditions (all satisfied)

- `examples/fmu/testing.json` + `test_spec.json` committed.
- Linux baselines committed under
  `examples/fmu/ReferenceResults/FMPy/linux/ref_000{1,2,3}.json`.
- `scripts/fetch_reference_fmus.py` is idempotent (skips download when the
  pinned version is already extracted) and writes to a gitignored dir.
- `pyproject.toml` declares `fmpy = ["fmpy>=0.3"]` as the `fmpy` extra.
- `requires-python = ">=3.10"`.

## Decisions (locked)

1. **Runner OS**: `ubuntu-latest` only. Baselines are partitioned by OS
   (`ReferenceResults/FMPy/<os>/`); CVode results differ enough across
   platforms that matrixing requires committing macOS / Windows baselines
   separately. Out of scope for first cut.
2. **Triggers**: `push` to `main`, `pull_request`, `workflow_dispatch`
   (manual rerun).
3. **Caching**: cache `examples/fmu/reference-fmus-binaries/` keyed on
   `scripts/fetch_reference_fmus.py` hash (saves ~17 MB download per run).
   uv has its own setup-uv cache action; use it.
4. **Scope**: one workflow file, one job with two steps —
   `pytest -q` first (catches unrelated regressions cheaply), then the
   FMPy e2e CLI run. Keeping them serial in one job means a single green
   check covers both.
5. **Artifacts on failure**: upload `testing_output/fmu/FMPy/linux/reports/`
   so a failed run is debuggable without rerunning locally.

## The recipe

```yaml
# .github/workflows/fmpy-ci.yml
name: FMPy end-to-end

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

jobs:
  fmpy-e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true

      - name: Set up Python
        run: uv python install 3.12

      - name: Install package + extras
        run: uv sync --extra dev --extra fmpy

      - name: Cache Reference-FMUs
        uses: actions/cache@v4
        with:
          path: examples/fmu/reference-fmus-binaries
          key: refmus-${{ hashFiles('scripts/fetch_reference_fmus.py') }}

      - name: Fetch Reference-FMUs (cache miss only)
        run: uv run python scripts/fetch_reference_fmus.py

      - name: Pytest
        run: uv run pytest -q

      - name: FMPy end-to-end
        run: uv run modelica-testing --config examples/fmu/testing.json run

      - name: Upload report on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: fmpy-report
          path: testing_output/fmu/FMPy/linux/reports/
          if-no-files-found: ignore
```

## Pin versions before shipping

The action versions above (`@v4`, `@v3`) drift. Before committing, check
the latest stable tag for each:
- `actions/checkout`
- `actions/cache`
- `actions/upload-artifact`
- `astral-sh/setup-uv`

## Validation when you're ready

1. Drop the YAML in `.github/workflows/fmpy-ci.yml`.
2. Either flip the repo to public, or push a branch and use
   `workflow_dispatch` (consumes private-repo minutes — the FMPy e2e
   takes ~1 minute of runner time, plus install/cache).
3. Confirm the workflow shows green; click into the run to verify each
   step.
4. If `pytest` step works but `FMPy end-to-end` fails because Linux
   CVode produces slightly different numbers than the dev-machine
   baselines, regenerate baselines on a Linux runner via
   `workflow_dispatch` + an `--accept` variant, commit, re-push.

## When to revisit

- Repo goes public.
- You want CI signal on PRs before then (worth the private-repo minutes).
- Phase 3+ work breaks the comparator and we want a regression net.
