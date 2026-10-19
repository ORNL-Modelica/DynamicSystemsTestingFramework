## What and why

<!-- One paragraph: what changes for a user or a contributor, and why. -->

## Checklist

- [ ] Tests cover the change, and the quality workflow is green: `ruff check`,
      `ruff format --check`, `mypy` and the test suite.
- [ ] The documentation builds: `sphinx-build -W --keep-going -b html docs docs/_build/html`.
- [ ] `CHANGELOG.md` has an entry under the unreleased version for a change a
      user can see (a command, a key, a report, a fix).
- [ ] The documentation says what the code now does: the guides, the
      generated reference, `README.md` and `CLAUDE.md`.
- [ ] The authoring tool and the report agree on the change, or the
      description says where they differ on purpose and why.
- [ ] Comments and docstrings match the code: none names a removed function,
      key or behaviour.
- [ ] Nothing private is added: no personal paths, names, emails, credentials
      or machine-specific settings, in code, tests, fixtures or docs.
- [ ] Working notes and other development-only files are removed (or moved out
      of the public tree) before merge.
