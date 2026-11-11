# Summarize a change for review

A pull request that accepts new baselines changes files of numbers nobody
reads line by line. `dstf baseline diff` reads them for the reviewer: which
baselines changed between two revisions of the reference root, and by how
much. Its Markdown is written to be pasted into the pull request.

## What changed between two revisions

Name the older revision, then the newer one, and where the reference root is:

```console
$ dstf baseline diff main HEAD --reference-root Resources/ReferenceResults
```

Leave the newer revision out to compare with the files on disk, tracked or
not: what `dstf run --accept` leaves before you `git add` it.

```console
$ dstf baseline diff main --config testing.json -o baselines.md
```

`--config` reads only the reference root from testing.json (its
`reference_root`, or its own folder when that is named `ReferenceResults`),
never the library, so a checkout that holds only the references is enough.
git is only read: the command writes nothing to the repository.

## What it prints

By default, three things:

- **One line of counts:** the tests that changed, are new or were removed,
  and each `<Backend>/<os>` folder's baselines. A test is new only when no
  folder held a baseline of it at the older revision, and removed only when
  none holds one at the newer; a test that gained a baseline in one folder
  and kept it in another is a changed test. A file whose numbers are equal
  and whose dates or key order differ is unchanged, and counted apart.
- **An overview table:** one row per test, largest change first, one column
  per backend/os folder, then the `vs` column with `--against`, then the
  change in accepted solver steps, in the first folder that records them.
- **The limits,** last.

What a cell of a folder's column can say:

| Cell | Meaning |
| --- | --- |
| `1.8 %` | the largest change of a variable there, as a percent of the old baseline's range |
| `2.9e-05 % of value` | the old series is constant: the change is a percent of its value |
| `0.004 absolute` | the old series is zero: the change is the difference itself |
| `0` | compared, and no stored sample moved |
| `–` | nothing compared: no finite point on either side, or no time |
| `+N new`, `−N removed` | N variables only the newer, or only the older, baseline holds |
| `· settings` | the simulation settings, the sampling setting or precision, or the comparison settings changed |
| `· not finite` | a point is NaN or infinite on one side and not the same on the other |
| `new`, `removed` | the baseline is only in the newer, or only in the older, revision |
| `unchanged` | the test's baseline there did not change |
| `cannot be read` | the file cannot be decoded at one revision; it is not compared |
| `two baselines` | two files of the folder hold the test at one revision; neither is compared |
| empty | the folder holds no baseline of the test |

The `vs` column holds the largest change of any other folder against the
`--against` one, chosen as a cell's is (the largest percent, else the
largest absolute difference), with the folder it is in, and `· not finite`
when any of them holds a non-finite point. The steps column reads
`unchanged` where that folder's baseline did not change, and `–` where it
records no steps.

A baseline written before DSTF recorded its sampling holds none: when the
newer one does, nothing is compared there, and the detail says `sampling
first recorded (<setting>, <precision>)`. The point counts a run produced
are listed beside the settings, never as one.

`--details` adds, after the table, a folded section for every test, and
`--details NAME ...` only for the named ones, each a short name or a full
model id:

```console
$ dstf baseline diff main HEAD --config testing.json --details NSSS_Test
```

A section holds each variable's change and when it happens, the settings
that changed, and the solver statistics old → new. The times (CPU time, wall
time) are on a line of their own: they measure the machine as much as the
solver. A GitHub pull-request body holds 65,536 characters; past that the
Markdown is still printed whole, and a note on stderr says its length.

A short example, two tests of a library in two folders:

```text
**Baselines changed ca9352b..06bc0b1**: 2 tests changed, 0 new, 0 removed (Dymola/linux: 1 changed; OpenModelica/linux: 1 changed, 1 new). 1 baseline unchanged, 0 of them in metadata only.

| Test | Dymola/linux | OpenModelica/linux | vs Dymola/linux | steps, Dymola/linux |
| --- | --- | --- | --- | --- |
| `HeatedRod` | 1.5 % | 0.20 % · settings | 1.3 % | -9.1 % |
| `Valve` | unchanged | new | 0.17 % | unchanged |
```

## How a change is measured

Baselines are paired by model id inside each folder, never by file name, so
a renamed file holding the same data is no change. For each variable the
change is the largest difference between the two baselines, with the new one
read on the old one's time grid, as a percent of the old baseline's range (of
its value where the old baseline is constant: the scale the `nrmse` score
divides by), and the time it happens. At an event, a time stored twice, the value
just before is compared with the value just before, and the one after with the
one after.

`--against Dymola/linux` adds a column: every other folder's newer baseline
scored against that folder's newer one, by the same rule.

## The limits

- Only the stored samples are compared: what a run did between two of them
  is not seen.
- The old baseline's points outside the new one's time span are counted, not
  compared.
- A baseline that cannot be read at one revision, or a model held by two
  files of one folder, is named and not compared.
- It prints facts. Whether a change is acceptable is the reviewer's call.

`--format json` writes every fact, every test's detail included, with a
number that is not finite written as `null` beside a flag that says which.

## Next

- [CI](ci.md): run the tests on every pull request.
- [Baselines](baselines.md): accepting results, and the reference root.
