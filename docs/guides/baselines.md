# Baselines

A baseline is a result you decided is right. Each run is compared with it, so
a change in the model's behaviour shows as a failure until you either fix the
model or accept the new result. This page is about that decision, and about
where baselines live.

## Accept a run

`--accept` stores the results of the tests it runs as their new baselines,
and scores nothing:

```console
$ dstf --config testing.json run --accept
```

Accept a few tests by naming them with `--filter` (globs, a comma-separated
list, or `@file` with one pattern per line) or `--package`:

```console
$ dstf --config testing.json run --filter "MyLib.Examples.Pipe*" --accept
```

A test whose simulation failed is not stored: the accept names it, and its
exit code is 1. Nor is a test the comparison refused, such as one that scored
nothing. A
test that declares nothing to score stores no baseline: it passes by
simulating. When a stored baseline is large, or no longer holds a variable the
one it replaced held, the accept says so and stores it anyway.

`dstf compare --accept` stores the last run's results without simulating
again.

## Review one by one

`-i` runs the tests, then shows each in turn and lets you decide:

```console
$ dstf --config testing.json run -i failed
```

`failed` limits the review to the tests that failed; `no-baseline`,
`sim-failed`, `warnings` and `passed` are the other categories, and with no
category every test is shown. For each test, `a` accepts its result, `s` skips
it, `d` shows its criteria in detail, `p` opens its plots, `v` adds variables
to track, and `q` ends the review.

## Run again what failed

After fixing a few tests, run only those and still get a report of the whole
suite: `--merge` scores every test, the others on their last results.

```console
$ dstf --config testing.json run --filter @rerun.txt --merge --report
$ dstf --config testing.json run --rerun failed,sim-failed --report
```

`--rerun` picks the tests by what the last run made of them, and merges.

## Where baselines live

Under the reference root, one file per test, in a folder per backend and
operating system:

```text
ReferenceResults/
├── testing.json
├── test_spec.json
├── Dymola/
│   ├── linux/
│   │   ├── ref_0001.json
│   │   └── ref_0002.json
│   └── windows/
│       └── ...
└── OpenModelica/
    └── linux/
        └── ...
```

Two simulators, or one simulator on two systems, give slightly different
numbers, so each keeps its own baselines. A first run on a system that has no
folder yet reports every test `NO_REF` until its results are accepted there.

A test's number never changes and is never reused. Each `ref_NNNN.json` holds
its model's name, the simulation settings and statistics of the run, the time
points and each tracked variable's values, and the comparison settings that
were in effect when it was accepted, so its tolerances travel with it. Its
`status` is `active`, `skip` (left out of comparison for now) or `obsolete`
(to be removed). `dstf manifest show` lists the tests and their numbers, and
`dstf manifest cleanup` lists the obsolete files, removing them with
`--apply`.

How many points a baseline keeps of its run is the
{ref}`baseline_points <key-testing-baseline-points>` setting; a test is
compared at its baseline's points, so that is also how finely it is checked.

## Soft checks

A soft check is a second stored result of a test, under a name: the same
model run by another simulator, an earlier release, a measurement. It is
added from a reference file this tool wrote, and the test must have its own
baseline first:

```console
$ dstf --config testing.json soft-check add MyLib.HeatExchanger openmodelica ../OpenModelica/linux/ref_0003.json
$ dstf --config testing.json soft-check list MyLib.HeatExchanger
```

A criterion scores against it by naming it in `against`, under a `warn` group:
a soft check can warn about a drift, never fail the test.

<!-- snippet: test_spec -->
```json
{
  "tests": [
    {
      "model": "MyLib.HeatExchanger",
      "variables": ["T"],
      "metrics": {
        "combinator": "and",
        "children": [
          {"metric": "nrmse", "variable": "T", "tolerance": 0.01},
          {"combinator": "warn", "children": [
            {"metric": "nrmse", "variable": "T", "against": "openmodelica", "tolerance": 0.05}
          ]}
        ]
      }
    }
  ]
}
```

A tree that scores a soft check outside `warn` is refused where it is
written (`dstf spec update`, the authoring tool), and a hand-written one is
refused where it is scored: `run` and `compare` fail that test with the
rule's sentence and score none of its criteria. A soft check is stored for
one backend and OS; where the test runs with another, its criterion under
`warn` is not scored, says `no soft check 'openmodelica' is stored for
Dymola/linux`, and fails nothing. There, `dstf spec update` and the
authoring tool write such a tree and say `'openmodelica' is not stored for
Dymola/linux: it is not scored here`.

Accepting a new primary result never touches a soft check.

## Companions

A companion is data drawn on a test's plots and never scored: rig
measurements, an analytic solution, another library's run. Register a CSV or
JSON file, and say which of its variables draws on which of the test's:

```console
$ dstf --config testing.json companion add MyLib.HeatExchanger rig ./rig.csv \
    --map "unitTests.x[1]=T_in" --map "unitTests.x[2]=T_out"
```

Both names are checked when you register, so a typo is refused at once. The
companion points at the file where it is; `dstf companion freeze` copies the
data in beside the baseline, so it travels with the repository. On a test's
page the companion is ticked on in the plot's overlay picker.

## Export

`dstf baseline export` writes every baseline of a partition to one JSON file,
or a CSV summary, for other tools:

```console
$ dstf --config testing.json baseline export --format csv --output baselines.csv
```

## Next

- [Criteria](criteria.md): score a test by more than one tolerance.
- [CI](ci.md).
