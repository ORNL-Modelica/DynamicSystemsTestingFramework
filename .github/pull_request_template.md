## What and why

<!-- One paragraph: what changes for a user or a contributor, and why. -->

## Checklist

- [ ] Tests cover the change, and the quality workflow is green: `ruff check`,
      `ruff format --check`, `mypy` and the test suite.
- [ ] The documentation builds: `sphinx-build -W --keep-going -b html docs docs/_build/html`.
- [ ] `CHANGELOG.md` has an entry under the unreleased version for a change a
      user can see (a command, a key, a report, a fix).
- [ ] Working notes and other development-only files are removed (or moved out
      of the public tree) before merge.
