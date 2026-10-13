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
3. Tag the commit on `main` and push the tag by name:

   ```bash
   git tag -a v1.0.1 -m "DSTF v1.0.1" <commit>
   git push origin v1.0.1
   ```

   The workflow builds, refuses a tag that does not name the built version,
   checks that the page renders, installs the wheel in a clean environment
   and starts it, then publishes to PyPI.

## What is published

The wheel is `src/dstf` — the code, the report templates with their bundled
plotly, the authoring tool's scripts. The source archive is that plus
`README.md`, `LICENSE` and `pyproject.toml`; the tests, the docs and the
example libraries stay in the repository. The README is the project page, so
an image in it is an absolute URL.

`v1.0.0` is a tag of the repository and was not published: the first version
on PyPI is 1.0.1, the same code with the metadata the index needs.
