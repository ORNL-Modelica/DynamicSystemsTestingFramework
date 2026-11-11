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

- **One line of counts:** the tests that changed, are new or were removed,
  and each `<Backend>/<os>` folder's baselines. A file whose numbers are equal
  and whose dates or key order differ is unchanged, and counted apart.
- **An overview table:** one row per test, largest change first, one column
  per backend/os folder. A cell is the test's largest change there, `new`,
  `removed`, `unchanged`, `+N new` for variables added, and `· settings` when
  the simulation, sampling or comparison settings changed. The last column is
  the change in accepted solver steps, in the first folder that records them.
- **One folded section per test:** each variable's change and when it
  happens, the settings that changed, and the solver statistics old → new. The
  times (CPU time, wall time) are on a line of their own: they measure the
  machine as much as the solver.
- **The limits,** last.

A short example, two tests of a library in two folders:

```text
**Baselines changed ca9352b..06bc0b1**: 1 test changed, 1 new, 0 removed (Dymola/linux: 1 changed; OpenModelica/linux: 1 changed, 1 new). 1 baseline unchanged, 0 of them in metadata only.

| Test | Dymola/linux | OpenModelica/linux | vs Dymola/linux | steps, Dymola/linux |
| --- | --- | --- | --- | --- |
| `HeatedRod` | 1.5 % | 0.2 % · settings | 1.3 % | -9.1 % |
| `Valve` | unchanged | new | 0.17 % | – |
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

`--format json` writes the same facts as data, with a number that is not
finite written as `null` beside a flag that says which.

## Next

- [CI](ci.md): run the tests on every pull request.
- [Baselines](baselines.md): accepting results, and the reference root.
