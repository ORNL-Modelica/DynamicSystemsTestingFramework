# Concepts

The words the rest of this site uses, each in a few lines.

## Test

A test is one model simulated with its own settings, and the variables of its
result that are recorded and scored. The model is whatever the backend runs: a
Modelica class, an FMU, a Julia model, a Python function.

A test is found in one of two ways, or both:

- **in the library's source**, by a [recognizer](#recognizer): a Modelica model
  that holds a `UnitTests` component is a test, and the component's `x={...}`
  names what it tracks;
- **in `test_spec.json`**, an entry per model. The entry can declare a test on
  its own, or add to one a recognizer found: the two are merged by the model's
  name.

A test that declares nothing to score passes when its simulation completes.

## Run

`dstf run` simulates the tests and scores each result. Each test ends with one
word:

| Word | Meaning |
|---|---|
| `PASS` | Every criterion that can fail the test passed. |
| `FAIL` | A criterion failed, or the test could not be scored. |
| `SIM_FAIL` | The simulation failed or timed out: there is nothing to score. |
| `NO_REF` | The test has no baseline yet, and nothing it declares can be scored without one. |
| `NOT RUN` | The test has no result: it was not run, or its run did not finish. |

`dstf compare` scores the last run's results again without simulating.

## Baseline

A baseline is a result you accepted (`dstf run --accept`): the trajectories a
later run is compared against. A test has up to three kinds:

- **primary** — the one `--accept` writes. Criteria score against it unless
  they name another.
- **soft check** — another stored result under a name, such as a second
  simulator's run. A criterion names it with `against`, and sits under a
  `warn` group: a soft check can warn, never fail a test.
- **companion** — data drawn on a test's plots and never scored: measurements,
  an analytic solution, another library's run.

## Criterion and the criteria tree

A criterion scores one variable by one comparison mode: how far the run is
from its baseline (`nrmse`, `tube`, `points`, ...), or whether it stays within
bounds that need no baseline (`range`). The test files call criteria
`metrics`.

The criteria of a test form a tree. A group (`and`, `or`, `k-of-n`, `warn`,
`weighted`) combines its children; the root's verdict is the test's. A test
with no tree scores every tracked variable by its own override, or by `nrmse`,
and all must pass.

A criterion that had nothing to compare is **not scored**: its variable is
missing from the run or from the baseline, its window holds none of the
samples, there are too few samples for its metric (a spectrum needs four), or
the soft check it names is not stored. It counts as a failed criterion — it
fails the test, or warns under `warn` — and says why: `points criterion not
scored — no actual samples to check (empty trajectory or window)`. In
`dstf author` nothing of it is drawn on the plot to drag or click; its fields,
and its window's ⌖, still edit it.

## Backend

A backend is the simulator dstf drives: Dymola, OpenModelica, FMPy, Julia or
Python, or one a plug-in adds. Each finds its own install; `dstf check` says
where, and by which rule.

## Recognizer

A recognizer is a rule that finds tests in the library's source files. The
bundled one finds Modelica models with a `UnitTests` component. A project can
declare its own in `testing.json`, for a convention of its own.

## Variable names

A variable is named as the simulator's result file names it. A variable of a
`UnitTests` component is `unitTests.x[1]`, `unitTests.x[2]`, ... by its place
in `x={...}`; the text written there (`pipe.T[1]`) is its expression, which the
reports show as its label. Every key that names a variable — an override, a
criterion, a plot — uses the result-file name.

## Work directory and reference root

Two places, kept apart:

- **The reference root** is what you keep in version control: `testing.json`,
  `test_spec.json`, and the baselines, one file per test under
  `<reference_root>/<Backend>/<os>/`. Different simulators and operating
  systems give slightly different results, so each has its own baselines.
- **The work directory** is what a run writes and the next run replaces: the
  results, `dashboard.html`, `verdicts.json`, `status.json` and the per-test
  report pages. It can be deleted at any time.
