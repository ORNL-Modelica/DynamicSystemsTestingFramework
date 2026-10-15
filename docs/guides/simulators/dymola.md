# Dymola

## Install

Install Dymola and its licence as Dassault Systèmes describes. dstf runs it
two ways:

- **workers that stay open** (the default): each worker is one Dymola,
  driven through Dymola's Python interface, which loads the library once and
  runs test after test;
- **`--batch`**: each worker runs a generated `.mos` script, with no Python
  interface.

The Python interface ships with Dymola (`Modelica/Library/python_interface`
in its install folder). dstf takes the one of the Dymola it found, and no
other's, so a project that names an older Dymola is not driven through a
newer one's interface.

Put `dymola` on `PATH`, or name it in the Dymola block's `executables`;
otherwise Dymola's standard install folders are searched, newest first.

## Check

When it is found (the paths are this machine's):

```text
Dymola
  executable: /usr/local/bin/dymola
    found by: search: PATH
  Python interface: /opt/dymola-2026x-x86_64/Modelica/Library/python_interface/dymola-2026.0-py3-none-any.whl
    found by: search: beside the executable
  interface format: .whl
  sys.path entry: /home/you/.cache/dstf/dymola-interface/dymola-2026.0-py3-none-any
  import ok: True
  module: dymola.dymola_interface
  interfaces in the standard folders (newest first):
    /opt/dymola-2026x-x86_64/Modelica/Library/python_interface

OK — Dymola is ready.
```

When the block names an executable that is not there:

```text
Dymola
  executable: not found — the configured executables: /opt/dymola-2030x/bin64/dymola does not exist
  Python interface: /opt/dymola-2026x-x86_64/Modelica/Library/python_interface/dymola-2026.0-py3-none-any.whl
    found by: search: the standard folder /opt
    taken from /opt/dymola-2026x-x86_64: no Dymola executable was found
  interface format: .whl
  ...

FAIL — Dymola is not ready. Fix one of:
  1. Install Dymola, or set "executables" in the Dymola block of testing.json's "backends" to where it is
```

## Settings

The Dymola block takes `executables` and three settings of its own — whether
Dymola's window is shown, how many tests one `--batch` session runs, and
where the Python interface is: {ref}`backend-dymola`.

<!-- snippet: testing -->
```json
{
  "source_path": "../..",
  "backends": {
    "Dymola": {
      "executables": ["C:\\Program Files\\Dymola 2026x\\bin64\\Dymola.exe"],
      "show_ide": false
    }
  }
}
```

Every Dymola run records its CPU time and its translation log, so a test's
statistics — the size of its system, its number of states — are stored with
its baseline, and a run whose number of states differs carries a warning.
