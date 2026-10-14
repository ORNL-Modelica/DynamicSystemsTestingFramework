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
  or with `timeout` in its `simulation` block.

## Exit codes

`dstf run` and `dstf compare` exit with:

| Code | When |
|---|---|
| 0 | Every test that ran passed. A test with no baseline yet (`NO_REF`), and a test that did not run beside tests that did, do not fail the run. |
| 1 | A test failed, failed to simulate or timed out; or no test ran at all; or an accept skipped a test. |
| 2 | The command line, `testing.json` or `test_spec.json` is wrong: the message names what. |

The rule for 0 and 1 is `exit_code` in `dstf/reporting/status.py`, and the
config error's 2 is `main` in `dstf/cli/main.py`; a command line the parser
refuses exits 2 as well. `dstf check` exits 0 when the project's backend is
usable, and 1 when it is not.

A failure under a `warn` group never changes the exit code.
