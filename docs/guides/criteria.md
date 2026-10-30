# Criteria

A criterion is one check of a run: one variable, scored by one comparison
mode, against the test's baseline or against bounds that need none. A test
passes when its criteria do. The test files call criteria `metrics`: a test's
criteria are its `metrics` tree.

The modes, each with its fields, are on the
[comparison modes reference](../reference/comparison-modes.md):
{ref}`nrmse <mode-nrmse>` (the default),
{ref}`tube <mode-tube>`, {ref}`points <mode-points>`,
{ref}`range <mode-range>`, {ref}`event-timing <mode-event-timing>`,
{ref}`dominant-frequency <mode-dominant-frequency>` and
{ref}`scalar <mode-scalar>`.

## Without a tree

A test with no `metrics` scores every variable it tracks by `nrmse` against its
baseline, and passes when all of them pass. To score one variable another way,
give it an override, a mode and that mode's fields:

<!-- snippet: test_spec -->
```json
{
  "tests": [
    {
      "model": "MyLib.Examples.PipeTest",
      "variables": ["pipe.T[1]", "pipe.p[1]", "tank.level"],
      "comparison": {
        "tolerance": 0.01,
        "variable_overrides": {
          "pipe.p[1]": {"mode": "tube", "tube_width_mode": "rel", "tube_rel": 0.02},
          "tank.level": {"mode": "range", "min_value": 0.0, "max_value": 2.0}
        }
      }
    }
  ]
}
```

`pipe.T[1]` is scored by `nrmse` with the test's tolerance, `pipe.p[1]` must
stay within 2 % of its baseline, and `tank.level` within fixed bounds, which
needs no baseline at all.

A criterion with no tolerance of its own takes the test's
(`comparison.tolerance`), else the one stored with its baseline, else the
project's (`tolerance` in `testing.json`).

## A tree

`metrics` replaces that flat list with a tree of criteria and groups. A
criterion names its `metric` and `variable`, and holds its mode's fields; a
group names its `combinator` and holds `children`:

<!-- snippet: test_spec -->
```json
{
  "tests": [
    {
      "model": "MyLib.Examples.PipeTest",
      "variables": ["pipe.T[1]", "tank.level"],
      "metrics": {
        "combinator": "and",
        "children": [
          {"metric": "nrmse", "variable": "pipe.T[1]", "tolerance": 0.01,
           "window": {"start": 10.0}},
          {"metric": "range", "variable": "tank.level", "min_value": 0.0, "max_value": 2.0},
          {"metric": "scalar", "variable": "tank.level", "quantity": {"reduce": "max"},
           "tolerance": 0.05},
          {"combinator": "warn", "children": [
            {"metric": "tube", "variable": "pipe.T[1]", "tube_width_mode": "band", "tube_abs": 0.5}
          ]}
        ]
      }
    }
  ]
}
```

This test passes when `pipe.T[1]` matches its baseline after t = 10 s, the
tank level stays within its bounds, and its maximum is within 0.05 of the
baseline's. The tube under `warn` is advisory: when it fails, the reports say
so and the test still passes.

The groups are `and`, `or`, `k-of-n` (at least `k` children pass), `warn`
(never fails its parent) and `weighted` (a weighted sum of the children's
scores, held against a threshold); the [reference](../reference/comparison-modes.md#groups)
says how each decides.

A tree scores only what its criteria name. A variable the test tracks and no
criterion scores is still recorded and plotted, and each report names it in a
warning, so a new variable that needs a criterion does not go unnoticed. Under a
tree, `comparison.variable_overrides` scores nothing.

Every name in the result is a variable, `CPUtime` and `EventCounter` included,
and any criterion may score it. A name listed in `diagnostic_variables` —
`testing.json`'s, or a test entry's own — has its summary (final, min, max)
stored in the baseline. Its curve is stored too when the entry's `variables`
match it or a criterion needs it: any criterion but a `scalar`'s final, min
or max over the whole run, which is scored from the summary. A test that
checks its event count exactly, warns when its CPU time moves by more than
10%, and scores the maximum of `pump.speed` within ±0.02 of the baseline's —
read from the summary its listing keeps:

<!-- snippet: test_spec -->
```json
{
  "tests": [
    {
      "model": "MyLib.Examples.PipeTest",
      "variables": ["pipe.T[1]"],
      "diagnostic_variables": ["pump.speed"],
      "metrics": {
        "combinator": "and",
        "children": [
          {"metric": "nrmse", "variable": "pipe.T[1]", "tolerance": 0.01},
          {"metric": "scalar", "variable": "EventCounter",
           "quantity": {"reduce": "final"}, "tolerance": 0, "tolerance_mode": "abs"},
          {"metric": "scalar", "variable": "pump.speed",
           "quantity": {"reduce": "max"}, "tolerance": 0.02},
          {"combinator": "warn", "children": [
            {"metric": "scalar", "variable": "CPUtime",
             "quantity": {"reduce": "final"}, "tolerance": 0.1, "tolerance_mode": "rel"}
          ]}
        ]
      }
    }
  ]
}
```

A dotted `quantity` string names a statistic of the simulator's log, such as
`"translation.nonlinear_count"`, never a variable.

## A tolerance with two sides

A tolerance is one number, or a pair that sets each side on its own. With
`d = actual − target`, a check passes when `−lower ≤ d ≤ upper`; in time,
`lower` is how much earlier and `upper` how much later. A checkpoint that may
be 0.01 under its value and 0.05 over it, and 0.5 s early or 0.1 s late:

<!-- snippet: criterion -->
```json
{
  "metric": "points",
  "variable": "tank.level",
  "points": [
    {"time": 5.0, "value": 2.0,
     "tolerance": {"lower": 0.01, "upper": 0.05},
     "time_tolerance": {"lower": 0.5, "upper": 0.1}}
  ]
}
```

A number is both sides, so `0.02` and `{"lower": 0.02, "upper": 0.02}` score
the same. `nrmse` has one ceiling, so its tolerance is always one number, and
so is the base tolerance of a test or a project.

## An example with every metric

`ModelicaTestingLib.Examples.ThermostatBuildingTest`, in the repository's
Modelica example library, puts every metric and every group into one tree.
Four rooms in a row start at 10 °C, each with a heater and a thermostat that
holds it within 0.5 °C of 20 °C. At 6 h the setpoint steps to 22 °C, and from
9 h to 9.5 h room 3's door stands open. The run lasts 12 h, and each part of
it has its own criteria:

- the warm-up, 0–7200 s: `nrmse` on each room's temperature, `T[1]` to `T[4]`;
- the cycling, 9000–21500 s: an `or` of `event-timing` on heater 1's state,
  `heaterOn[1]`, and `dominant-frequency` on the cycle of `T[1]`; and a
  `k-of-n` that needs three of the four rooms to stay within 19–21 °C
  (`range`);
- the step, 21600–28800 s: a `tube` of ±0.3 °C around room 1's climb, and
  `points` at two moments of it;
- the whole run: a `scalar`, the energy heater 1 uses (the integral of
  `P[1]`), within 2 %;
- the late part, 28800–43200 s: a `weighted` group of three `range`
  criteria, rooms 1, 2 and 4 within 21.4–22.6 °C, weighted 0.5, 0.3 and 0.2;
- two `warn` groups: room 3 stays above 12 °C while its door is open and for
  an hour after, and
  `T[1]` matches the OpenModelica run, stored as a soft check named
  `openmodelica`.

The tree is the test's entry in the library's `test_spec.json`. To run it
with Dymola, from the repository's root (add `--backend OpenModelica` after
`run` to run it with OpenModelica):

```console
$ dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --filter ModelicaTestingLib.Examples.ThermostatBuildingTest --report
```

The rooms' plot on the test's page draws each window, band, tube and
checkpoint, with the spectrum of the cycle under it:

![The four rooms' temperatures with the criteria's windows, bands and checkpoints, and the cycle's spectrum below](../img/thermostat-report.png)

`event-timing` scores the model's events, not one variable's: every variable
of a result shares one time column, so heater 1's plot marks the switchings
of all four rooms, 62 of them between 9000 s and 21500 s. The window ends
100 s before the setpoint step, so the step's own event stays out of it.

Room 2 dips 0.05 °C under its late band, so its `range` criterion fails. Its
weight is 0.3, so the `weighted` group scores 0.3 × 0.052 = 0.016, under its
threshold of 0.5, and passes. The page says 17 / 18 criteria passed, and the
test passes.

To try it, copy the library, give room 3's heater 10 % more power (`Pmax`
from `{3000, 3200, 3500, 2800}` to `{3000, 3200, 3850, 2800}` in
`Examples/ThermostatBuildingTest.mo`), and run the command above with the
copy's `testing.json`, so the copy is scored against the baselines it
carries. With Dymola the test fails, with 13 / 18 criteria passed. The
rooms share their heat, so all four warm-up `nrmse` criteria fail (room 3's
NRMSE is 0.053, against a tolerance of 0.001), and so does `event-timing`,
which counts more switchings than the baseline holds. The `or` group still passes, because `dominant-frequency` still finds the
cycle at its period. The `k-of-n` band, the tube, the checkpoints and heater
1's energy (0.12 % off) all pass. The `weighted` group passes with all three
of its criteria now, since room 3's extra heat keeps room 2 inside its band.
Both `warn` groups pass, the OpenModelica comparison at 0.005 against 0.01.
The run also warns that the model's event count moved, from 453 to 497.

## Editing criteria with dstf author

`dstf author` is a browser tool for writing criteria against the data they
score. It needs the `author` extra, and opens a tab on the project's last run:

```console
$ dstf --config testing.json author
```

The Suite page is the table of the tests, as the dashboard has it, with a
queue that runs the selected tests again:

![dstf author's Suite page: the tests with their status, Criteria and times](../img/author-suite.png)

A test's page draws a plot per scored variable with its criteria under it. A
number — a statistic of the log, or a listed name's final, min or max — is
not on a plot: its criterion is on the page's Statistics card, and in the
report it is in the metric tree. A criterion opens into its fields beside the
plot it scores; its bounds, bands and checkpoints can also be dragged on the
plot, and Ctrl+click adds a row — a checkpoint, a tube's control point — that
settles on the data:

![A range criterion open under its plot, with its bounds as fields and as dashed lines](../img/author-editor.png)

Edits are staged as you make them and the count of pending edits is in the
header; Save writes them to `test_spec.json`, and Discard drops them. A test
is scored by its new criteria as soon as they are staged; run it again, from
the queue or with `dstf run`, to score a new run. Accept stores the selected
tests' results as their baselines, as `run --accept` does.

To apply an edit made elsewhere — a script, another tool — write it as a
JSON-Patch and apply it with `dstf spec update`.

## Next

- [CI](ci.md).
- [Baselines](baselines.md): soft checks, which a criterion can score against.
