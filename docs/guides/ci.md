# Run it in CI

A CI job installs dstf and the simulator, runs the suite, and keeps the JUnit
file and the work directory for whoever looks at a failure. dstf's exit code
fails the job when a test fails.

## A GitHub Actions job

This job runs a project whose tests are Python functions, such as the Python
example, on every push. `pipx` is already on GitHub's Ubuntu runners:

```yaml
name: regression

on: [push, pull_request]

jobs:
  dstf:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dstf and what the tests import
        run: |
          pipx install dynamic-systems-testing-framework
          python -m pip install numpy scipy
      - name: Run the suite
        run: |
          dstf --config Resources/ReferenceResults/testing.json run --no-browser --report-format junit --output results.xml
      - name: Keep the results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: dstf-results
          path: |
            results.xml
            testing_output/
```

- **The runner's system is part of the baselines' address.** An Ubuntu runner
  is compared with the `<Backend>/linux/` baselines, so the repository needs
  those: accept them once on Linux, or in a first CI run with `--accept`, and
  commit them.
- **The simulator is the job's to install.** FMPy and Python install with pip
  (`pipx install "dynamic-systems-testing-framework[fmpy]"` for FMPy);
  OpenModelica, Dymola and Julia need their own install step and, for Dymola,
  a licence on the runner. `dstf check` in a step of its own says whether the
  simulator was found, before the run starts:

  ```console
  $ dstf --config Resources/ReferenceResults/testing.json check
  ```

- **`if: always()`** keeps the files when the run step fails, which is when
  they are wanted. `testing_output/` holds `dashboard.html`, `verdicts.json`
  and each test's result files; add `--report` to the run for a page per test.
- A test that is slow on the runner can be given more time with `--timeout`,
  with `timeout` in its `simulation` block, or for one simulator with
  `timeout` in a `backends` block of its entry ([Add a
  test](add-a-test.md#a-test-that-does-not-run-on-one-simulator)). The most
  specific wins: the backend and OS's block, the backend's, the entry's
  `simulation.timeout`, then `--timeout`. A test that timed out says which
  of them stopped it (`the limit is backends.OpenModelica.timeout in
  test_spec.json: raise it there`): raising another does nothing.
- **A test known to fail on the runner's simulator** can be marked `skip`
  there, with its reason: it is not run, reads `SKIPPED`, and fails no job.
  The JUnit file has it as a skipped test case with that reason.

## A pull request runs only what it touches

On a Modelica library whose suite is long, a pull request can run only the
tests that use a class it changed ([Run only those
tests](review.md#run-only-those-tests)), and `main` the whole suite:

```yaml
name: regression

on:
  pull_request:
  push:
    branches: [main]

jobs:
  dstf:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      # install dstf and the simulator as above
      - name: Run the tests the pull request touches
        if: github.event_name == 'pull_request'
        env:
          BASE: origin/${{ github.base_ref }}
        run: |
          dstf --config Resources/ReferenceResults/testing.json run --no-browser --changed-since "$BASE" --report-format junit --output results.xml
      - name: Run the suite
        if: github.event_name == 'push'
        run: |
          dstf --config Resources/ReferenceResults/testing.json run --no-browser --report-format junit --output results.xml
      - name: Keep the results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: dstf-results
          path: |
            results.xml
            testing_output/
```

- **`fetch-depth: 0`**: the checkout fetches one commit by default, and the
  base branch is then unknown to git.
- **What is compared.** A pull request's checkout is the merge of its branch
  into the base, so `origin/<base>` against it is what the pull request
  changes. When the base branch has moved on since that merge commit was
  made, the base's later changes count too: more tests, never fewer.
- **Nothing to run is a pass.** When the pull request changes no class a
  test uses, the run exits 0 and writes nothing: no work directory and no
  `results.xml`. `upload-artifact` only warns about a missing file; a step
  that reads `results.xml` must allow it to be missing.
- **Files DSTF keeps are no change.** A pull request that changes only
  test_spec.json, testing.json or a stored baseline changes no class
  ([what is not compared](review.md#what-changed-since-a-revision)): the
  selected job prints `Nothing changed` and passes without running
  anything. The full run on `main` is what checks it.
- The selected run is not a full run, and says so on the dashboard, in
  `verdicts.json` and in `dstf summary` (`2/2 · 2 of 14 tests run`). The
  JUnit file does not: it is a plain suite of the tests that ran. The run
  on `main` is the check.

## Exit codes

`dstf run` and `dstf compare` exit with:

| Code | When |
|---|---|
| 0 | Every test that ran passed. A test with no baseline yet (`NO_REF`), a test that did not run beside tests that did, and a test marked `skip` for the run's backend and OS — `SKIPPED`, or run anyway under `--include-skipped` and still failing — do not fail the run. Also a run with nothing to run: `--changed-since` found no test a change touches, `--rerun` no test in its categories, or every test chosen is marked `skip`. |
| 1 | A test failed, failed to simulate or timed out; or no test ran at all; or an accept skipped a test not marked `skip`; or, with `--changed-since`, the simulator could not be asked what the tests use. |
| 2 | The command line, `testing.json` or `test_spec.json` is wrong: the message names what. With `--changed-since`, also when git cannot answer, the project is not Modelica, or the backend cannot say what a test uses. |

The rule for 0 and 1 is `exit_code` in `dstf/reporting/status.py`, and the
config error's 2 is `main` in `dstf/cli/main.py`; a command line the parser
refuses exits 2 as well. `dstf check` exits 0 when the project's backend is
usable, and 1 when it is not. Any command stopped with Ctrl-C prints
`Interrupted.` and exits 130, the shell's code for an interrupt.

A failure under a `warn` group never changes the exit code, and neither does
a test marked `skip`: the JUnit file writes such a test that still fails as a
`<skipped>` with `still fails: FAIL` in its message, so a CI server that reads
the file agrees with the exit code.
