# Releasing

How a version of DSTF gets to PyPI. The distribution is
`dynamic-systems-testing-framework`; the command and the import name are
`dstf` (D124).

## Once, on the package index

`.github/workflows/publish.yml` publishes by *trusted publishing*: the index
accepts an upload from this repository's workflow and from nothing else, and
no password or token is stored anywhere. Each index has to be told which
workflow to trust — on PyPI and on TestPyPI, under the account that will own
the project: **Publishing → Add a new pending publisher** (a publisher can be
added before the project exists; the first upload creates it).

| Field | PyPI | TestPyPI |
|---|---|---|
| PyPI project name | `dynamic-systems-testing-framework` | `dynamic-systems-testing-framework` |
| Owner | `ORNL-Modelica` | `ORNL-Modelica` |
| Repository name | `DynamicSystemsTestingFramework` | `DynamicSystemsTestingFramework` |
| Workflow name | `publish.yml` | `publish.yml` |
| Environment name | `pypi` | `testpypi` |

The two environment names are GitHub environments of this repository
(Settings → Environments). They are created on first use; adding a required
reviewer to `pypi` there makes every release wait for a person's approval.

## Once, on Read the Docs

The documentation site is built by Read the Docs from `.readthedocs.yaml`:
Ubuntu 24.04, Python 3.12, the project installed with its `docs` extra, and
`docs/conf.py` with every warning an error. Import the repository on
readthedocs.org under the account that will own the project, and under
**Versions** activate the tags (a rule "activate new tags as they come"
saves doing it per release); `stable` then follows the newest tag. The site
publishes the two JSON schemas at `schemas/testing.schema.json` and
`schemas/test_spec.schema.json` under each version's root, which is what a
user's `$schema` points at.

## Each release

1. Set `version` in `pyproject.toml`, then `uv lock` (the lock records the
   project's own version; CI's `uv sync --locked` fails on a stale one).
   Commit both, merge to `main`.
2. Rehearse, if anything about the packaging changed: Actions → **publish** →
   Run workflow → `testpypi`. Then, from anywhere:

   ```bash
   uv tool install --index https://test.pypi.org/simple/ \
     --index-strategy unsafe-best-match dynamic-systems-testing-framework
   dstf --version
   ```

   (TestPyPI holds few of the dependencies; the second flag lets them come
   from PyPI.) A version can be uploaded to an index **once** — a second
   rehearsal of the same version is refused, so rehearse a change of the
   packaging, not every release.
3. The documentation builds with every warning an error: the `docs` job of
   the `quality` workflow is green on the commit, or locally

   ```bash
   uv sync --extra dev --extra docs --locked --inexact
   uv run sphinx-build -W --keep-going -b html docs docs/_build/html
   ```

4. Read `README.md` as the project page it becomes, on the commit to be
   tagged: the index freezes the page with the version, and a broken image
   or a link that only works inside the repository stays on that version's
   page for good. An image is an absolute URL; so is every link.
5. Tag the commit on `main` and push the tag by name:

   ```bash
   git tag -a v1.0.1 -m "DSTF v1.0.1" <commit>
   git push origin v1.0.1
   ```

   The workflow builds, refuses a tag that does not name the built version,
   checks that the page renders, installs the wheel in a clean environment
   and starts it, then publishes to PyPI.
6. Read the Docs builds the tag: check that the new version is listed on the
   site, builds green, and is what `stable` shows.

## What is published

The wheel is `src/dstf` — the code, the report templates with their bundled
plotly, the authoring tool's scripts. The source archive is that plus
`README.md`, `LICENSE` and `pyproject.toml`; the tests, the docs and the
example libraries stay in the repository. The README is the project page, so
an image in it is an absolute URL.

`v1.0.0` is a tag of the repository and was not published: the first version
on PyPI is 1.0.1, the same code with the metadata the index needs.
