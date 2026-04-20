# Phase 6 — Reporter-as-IDE, MVP plan

**Scope**: first shippable MVP is **6.0 + 6.1 + 6.4**. Everything else in Phase 6 (6.2 tree-level controls, 6.3 multi-baseline picker, 6.5 edit/view toggle, 6.6 draft-tree preview) is post-MVP.

**Source of truth for commitments**: D66 in `docs/decisions.md`. This file is the implementation-oriented decomposition of the MVP — it's a working plan, not a decision record. Update it as the work progresses; retire it once the MVP is merged and D67 captures the as-built.

**Target duration**: ~3–4 focused weeks. Fits inside a single dedicated session arc with review checkpoints.

---

## 6.0 — Performance budget (precondition)

Do this first. Cheap, unblocks everything else by proving the payload is controllable.

**Target**: interactive.html for a 50-variable test stays under **~5 MB** embedded payload.

**Work**:
1. Measure the current size on a representative test (ModelicaTestingLib SimpleTest, or the FMU BouncingBall). Record the number.
2. Decide the decimation policy:
   - Cap embedded trajectory at `N` samples per variable (e.g., 2 000).
   - When the raw trajectory exceeds `N`, emit both a decimated inline copy *and* a full-resolution sidecar JSON (`<test>_full.json`) that the reporter lazy-fetches on demand.
3. Implement in `reporting/plot_comparison.py` (or wherever the template context is built). Add a config option `reporter.max_embedded_samples` with sensible default.
4. Add a test that asserts interactive.html size under a ceiling for a fixture with wide trajectories.

**Exit criteria**: 50-var fixture renders under the budget; size regression test green.

---

## 6.1 — Per-leaf detail panels

Bulk of the work. Six modes × (auto-derived UI + JS recompute) + two custom overrides.

**Architecture (from D66 / Q2)**:
- `ComparisonMode` stays pure compute. Do not add UI methods to the ABC.
- New module: `reporting/ui/mode_controls.py` with a registry keyed by mode `name`.
- **Default UI auto-derives** from each mode's typed Config dataclass. Required preparatory refactor: tighten Config types (e.g., `TubeConfig.tube_width_mode: Optional[str]` → `Optional[Literal["band", "rel", "absolute"]]`).
- **Custom overrides** register a hand-written panel for modes where auto-derivation is insufficient (today: `tube` with its conditional rel/abs/min fields, `range` with its min/max visual handles).

### 6.1.1 — Auto-derive machinery

- Introspection utility: takes a Config dataclass, produces a UI schema (field name, type, default, choices for `Literal`s, optional `metadata` for display hints).
- Renderer: schema → HTML form fragment (vanilla JS, no framework).
- Register auto-derived UI for the four simple modes.
- **Decision point for idea #46 (time-windowed leaves)**: at this checkpoint, evaluate whether the auto-derive generator can absorb a shared cross-mode `window: {start, end}` subschema without leaking into each mode's Config. If yes, include in the MVP (~½ day extra; two inputs per panel; trivial slice in `tree_eval.py`; patch path fits 6.4 whitelist). If it forces per-mode Config coupling, defer to post-MVP. Range-brush UI variant stays deferred either way.

### 6.1.2 — JS recompute ports

For the four simple modes, port the Python comparator's scoring logic to vanilla JS so the reporter can recompute pass/fail as the user drags. Accept small numerical drift — CLI is authoritative. Add a note in the UI that live preview is approximate.

- `nrmse` — already exists (today's slider). Just refactor into the new per-mode module.
- `tube` — per-sample `ref(t) - w⁻ ≤ actual(t) ≤ ref(t) + w⁺` check. Widths depend on `tube_width_mode`.
- `range` — signal-only min/max check. Trivial.
- `final-only` — check final sample against tolerance. Trivial.

### 6.1.3 — No live preview for numerical modes

- `event-timing` and `dominant-frequency` — UI reads values but shows no live pass/fail recompute. Instead, a badge: *"CLI-authoritative (run `modelica-testing run` to see new scores)"*.

### 6.1.4 — Custom overrides

- `tube` panel: dropdown for `tube_width_mode`, conditional fields for `rel` / `band` / `absolute`, optional time-varying `tube_points` editor (defer to 6.6 if it gets gnarly).
- `range` panel: two visual handles on the trajectory plot for min/max bounds; text inputs as fallback.

### 6.1.5 — Replace `n/a (mode=…)` cells

Sweep `reporting/templates/interactive.html` — wherever a per-variable row currently shows `n/a (mode=<name>)`, it now renders its per-mode panel. Leaves targeting soft_checks still render inside their `warn` visual wrapper (visible distinction).

**Exit criteria**: every one of the six modes has a working control panel in the interactive report; the four simple modes have live pass/fail preview; event-timing and dominant-frequency have CLI-authoritative-only UI with a clear tooltip; manual click-through against ModelicaTestingLib demo passes; golden-file snapshot captured.

---

## 6.4 — Full-fidelity `spec-update` round-trip

Closes the authoring loop. User drags controls → downloads patch → runs CLI → spec updated.

**Architecture (from D66 / Q3)**:
- Download payload = **RFC 6902 JSON-Patch** document (`{"op": "replace", "path": "...", "value": ...}` array).
- CLI `spec-update` reads the patch, applies via read-modify-write against the target `test_spec.json`, **preserves unknown keys** and the `description` / `info` / `metadata` convention for human context.
- Unknown-key preservation is a hard requirement — users escape-hatch via `metadata` and hand-authored notes must survive round-trips.

### 6.4.1 — Patch format definition

Define the set of `op`+`path` shapes the reporter can emit and `spec-update` can apply:
- Per-variable override: `replace /tests/<id>/comparison/variable_overrides/<var>/<field>`.
- Per-test tree-node value: `replace /tests/<id>/metrics/<json-pointer-to-node>/<field>`.
- Whitelist of paths writable by the reporter; reject writes outside the whitelist (avoid the reporter mutating structural shape — that's an authoring step, not a patch step).

### 6.4.2 — Reporter emits patches

Extend the existing "Download Tolerance Updates" button to emit full RFC 6902 patches. Reuse the download mechanism; just change the payload format.

### 6.4.3 — CLI accepts patches

Extend `cmd_spec_update` in `cli.py`:
- Accept either the legacy tolerance-dict format (for backward compat during one transition cycle) OR the new RFC 6902 format, auto-detect by shape.
- Apply read-modify-write. Use `json.load` + dict mutation + `json.dump`; do not use a third-party JSON-Patch library to keep deps minimal (small patch surface, easy to implement directly).
- Preserve key order and unknown keys (Python 3.7+ dicts do this naturally; the point is to *not* re-serialise via a canonicalising model).

### 6.4.4 — Validator rules from D66

Before writing the updated spec, re-validate against the D66 rules:
- Every tree has ≥ 1 leaf targeting primary outside `warn`.
- Soft_check-targeting leaves sit under `warn`.
- Leaves never target companion references.

Emit clear errors; do not partially write.

### 6.4.5 — JSON-Schema export

New CLI: `modelica-testing export-schema [--format json-schema]`. Derives the test-spec schema from the Config dataclasses (plus the MetricTree grammar). Writes to stdout or file. Useful downstream: validators, alternative authoring tools, LLM-generated specs. Serves the economy-of-tools principle.

### 6.4.6 — Tests

- Round-trip fidelity: read spec, apply patch, verify only the intended paths changed.
- Unknown-key preservation: spec with `metadata`, `description`, hand-authored comments — round-trip leaves them byte-identical.
- Validator rules: patch that would break a rule is rejected with a clear message.
- JSON-Schema export shape matches the Config dataclass fields.

**Exit criteria**: reporter → download → CLI-apply → rerun produces a spec the validator accepts; round-trip preserves `metadata`; JSON-Schema export available via CLI.

---

## Cross-cutting — baseline-role implementation (wired alongside 6.0–6.4)

D66 commits to three baseline roles (primary / companion / soft_check) but the code still uses a flat `add_named_baseline` API. The split lands alongside Phase 6 because the reporter needs to render them distinctly anyway.

**Work**:
1. `ReferenceStore` split — companion references and soft_checks get their own storage sections under `ReferenceResults/<backend>/<os>/companions/` and `.../soft_checks/`. One-off migration: existing named baselines become soft_checks.
2. New CLI commands:
   - `companion add <test> <name> <path>` — register an external file-path companion.
   - `companion freeze <test> <name>` — copy an external companion into ref storage.
   - `import-baseline <test> <name> <path>` — import another regression system's primary as a soft_check.
3. Validator rule wiring (mentioned above in 6.4.4).
4. Cross-backend chain (D65) updated to write into the soft_check slot explicitly. Terminology sweep in `simulators/cross_backend.py` — rename the CROSS_BACKEND_BASELINE_NAME constant's documentation to say "soft_check" explicitly.

**Exit criteria**: the three roles are distinct on disk, distinct in CLI, and distinct in the reporter UI. Validator enforces the rules from D66.

---

## Testing strategy (from D66 / Q8)

- **Python tests** cover the data contract exhaustively: patch schema, round-trip fidelity, unknown-key preservation, validator rules, JSON-Schema export.
- **Golden-file HTML snapshots**: for each mode, render the interactive.html against fixture data; hash the structural DOM (ignoring timestamps / paths); fail on diff. Add `pytest --update-golden` workflow.
- **Markdown QA checklist** at `docs/qa/reporter_checklist.md`: click-through scenarios for manual pre-release verification. Create this file as part of 6.1.
- **No JS unit test framework**. No Playwright E2E. Revisit only if the reporter becomes a regression source.

---

## Ordering + review checkpoints

Recommended sequencing inside a single session arc:

1. **6.0** (½ day) — measure, decimate, cap. **Checkpoint**: size regression test green.
2. **Baseline-role split — storage + CLI** (2–3 days) — lands the three-role storage shape before the reporter depends on it.
3. **6.1.1 auto-derive machinery** (1–2 days) — validates the UI-derivation contract.
4. **6.1.2–6.1.3 JS ports for simple modes + honest-no-preview for numerical modes** (2–3 days) — lands live preview breadth. **Checkpoint**: all six modes render working panels.
5. **6.1.4 custom overrides for tube and range** (2 days).
6. **6.4.1–6.4.3 patch format + CLI** (2 days).
7. **6.4.4–6.4.5 validator rules + schema export** (1 day).
8. **6.4.6 tests + QA checklist** (1 day). **Checkpoint**: the MVP closes the loop end-to-end on ModelicaTestingLib.
9. **D67** — record the as-built; retire this file.

Reviews land at the checkpoints. Scope creep prevention: if any step grows past 1.5× its estimate, pause and re-plan. 6.2/6.3/6.5/6.6 are deferred — do not sneak them in.

---

## Out of scope for this MVP (explicit)

- Tree-level authoring controls (6.2).
- Multi-baseline picker (6.3).
- Edit/view toggle (6.5).
- Draft-tree preview (6.6).
- Recommender (Phase 7).
- Dataset types (Phase 9).
- Tool rename.
- FMU-path semantic gap closure (D65 follow-on).
- Any ML.

If any of these emerge as critical-path during implementation, escalate rather than absorbing silently.
