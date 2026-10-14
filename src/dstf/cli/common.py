"""Shared CLI plumbing: config building, test filtering, review-category
parsing. Everything here has callers in more than one command module."""

import argparse
import fnmatch
import sys
from collections.abc import Iterable
from pathlib import Path

from ..comparison.types import TestComparison
from ..config import Config, ConfigError
from ..discovery.test_registry import TestModel
from ..reporting.status import (
    FAIL,
    NO_REF,
    NOT_RUN,
    PASS,
    SIM_FAIL,
    classify,
    exit_code,
    nothing_ran,
)

#: The flags that reach :class:`Config`, by the attribute each parses to:
#: the Config field it sets, and whether its value is a path. A command
#: offers the ones it reads (`cli.parser`), so one the command line did not
#: have is simply absent here.
_CONFIG_FLAGS: dict[str, tuple[str, bool]] = {
    "source_path": ("source_path", True),
    "config": ("config_file", True),
    "reference_root": ("reference_root", True),
    "test_spec": ("test_spec_file", True),
    "backend": ("backend_name", False),
    "backend_path": ("backend_path", False),
    "work_dir": ("work_dir", True),
    "parallel": ("parallel", False),
    "tolerance": ("tolerance", False),
    "timeout": ("timeout", False),
}


def _build_config(args: argparse.Namespace) -> Config:
    """Build Config from parsed CLI arguments.

    A project with no testing.json is read on defaults, and none is written
    beside it — unless the command declared, in the tree, that it may
    create a file of the project (``cli.parser``: ``creates=True``, which
    the parser puts on the namespace). Only such a command starts one.

    A flag that was given is passed on whatever its value — ``is not
    None``, NOT truthiness: ``--tolerance 0`` is the strictest possible
    setting and Config/comparator honor an explicit 0.0, and a value that
    cannot be meant (``--timeout 0``) is the parser's to refuse, never this
    function's to drop."""
    kwargs: dict = {"write_default": getattr(args, "may_create_project", False)}
    for attr, (field, is_path) in _CONFIG_FLAGS.items():
        value = getattr(args, attr, None)
        if value is not None:
            kwargs[field] = Path(value) if is_path else value
    if getattr(args, "default_points", False):
        kwargs["default_points"] = True
    return Config(**kwargs)


def _split_filter_list(spec: str) -> list[str]:
    """Split a comma-separated filter list on commas NOT inside ``[]``.

    review 2026-07-06 finding 64: naive comma-splitting broke fnmatch
    character classes like ``Test[A,B]*``. Small scanner: a comma inside an
    open ``[`` class belongs to the class. No escape mechanism — an
    unclosed ``[`` makes the rest of the string one pattern (fnmatch treats
    an unmatched ``[`` as a literal anyway).
    """
    parts: list[str] = []
    buf: list[str] = []
    in_class = False
    for ch in spec:
        if ch == "," and not in_class:
            parts.append("".join(buf))
            buf = []
            continue
        if ch == "[" and not in_class:
            in_class = True
        elif ch == "]" and in_class:
            in_class = False
        buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _resolve_filter_patterns(spec: str) -> list[str]:
    """Resolve a --filter argument into a list of glob patterns.

    Accepts:
      - "Foo.Bar.*"           — single glob
      - "Foo.A,Foo.B,Foo.C"   — comma-separated globs (commas inside []
                                character classes are part of the class)
      - "@path/to/file.txt"   — file with one pattern per line; '#' starts a comment
    """
    if spec.startswith("@"):
        path = Path(spec[1:])
        if not path.exists():
            # ConfigError, not FileNotFoundError: a mistyped @file is a user
            # error and main() turns it into one clean "Config error: ..."
            # line and exit 2, never a traceback.
            raise ConfigError(f"Filter file not found: {path}")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ConfigError(f"Cannot read filter file {path}: {e}") from e
        patterns = []
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                patterns.append(line)
        return patterns
    return _split_filter_list(spec)


def _filter_tests(
    tests: list[TestModel],
    pattern: str | None = None,
    package: str | None = None,
) -> list[TestModel]:
    """Filter tests by glob/list/file on model_id, plus optional package prefix."""
    filtered = tests
    if package:
        # review 2026-07-06 finding 65: match the package itself or children
        # across a dot boundary — "MyLib.Fluid" must not also select
        # "MyLib.FluidExperimental.*" (raw startswith did).
        filtered = [
            t
            for t in filtered
            if t.model_id == package or t.model_id.startswith(package + ".")
        ]
    if pattern:
        patterns = _resolve_filter_patterns(pattern)
        # review 2026-07-06 finding 63: fnmatchcase — fnmatch.fnmatch is
        # case-insensitive on Windows, so the same filter selected different
        # tests across the two OS partitions this project maintains.
        filtered = [
            t
            for t in filtered
            if any(fnmatch.fnmatchcase(t.model_id, p) for p in patterns)
        ]
    return filtered


def _find_orphan_manifest_entries(config, all_tests: list) -> dict[str, str]:
    """Return {test_key: model_id} for manifest entries whose model_id is no
    longer in the discovered tests. These usually indicate renamed/removed
    models — they don't break correctness (compare_all skips them) but their
    work dirs and report dirs accumulate on disk.
    """
    manifest_path = config.work_dir / "batch_manifest.json"
    if not manifest_path.exists():
        return {}
    from ..backends import BatchManifest

    manifest = BatchManifest.load(manifest_path)
    discovered = {t.model_id for t in all_tests}
    return {
        tk: entry["model_id"]
        for tk, entry in manifest.manifest.items()
        if entry["model_id"] not in discovered
    }


_VALID_REVIEW_FILTERS = {
    "all",
    "failed",
    "no-baseline",
    "warnings",
    "sim-failed",
    "passed",
}


def _exit_code(comparisons: Iterable[TestComparison]) -> int:
    """`run` and `compare`'s exit code on every route (``exit_code``), and
    when it is 1 because nothing ran, ONE stderr line saying so: a junit or
    html report, or ``--report``'s pages, exited 1 without a word (D145
    §5). With no test in scope at all there is nothing to count, and the
    line says that instead of "(0 NOT RUN)"."""
    comps = list(comparisons)
    if not comps:
        print("Nothing ran: no test in scope.", file=sys.stderr)
    elif nothing_ran(comps):
        print(
            f"Nothing ran: no test in scope has a result ({len(comps)} "
            f"{NOT_RUN}) — exit 1.",
            file=sys.stderr,
        )
    return exit_code(comps)


def _parse_review_filter(filter_str: str) -> set[str]:
    """Parse and validate the review filter string."""
    filters = {f.strip() for f in filter_str.split(",") if f.strip()}
    invalid = filters - _VALID_REVIEW_FILTERS
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Invalid review filter(s): {', '.join(sorted(invalid))}. "
            f"Valid: {', '.join(sorted(_VALID_REVIEW_FILTERS - {'all'}))}"
        )
    if "all" in filters:
        return {"all"}
    return filters


def _should_review(comp, filters: set[str]) -> bool:
    """Check if a comparison matches the active review filters (`-i` and
    `--rerun`). A category selects by the ONE classifier's status
    (``reporting.status.classify``), the status every surface shows."""
    if "all" in filters:
        return True
    status = classify(comp)
    # NOT RUN too: nothing succeeded (D145).
    if "sim-failed" in filters and status in (SIM_FAIL, NOT_RUN):
        return True
    # A test the comparison FAILED is "failed" and nothing else, baseline or
    # not, so "no-baseline" does not select it: one the comparison refused,
    # or one with no reference that fails a baseline-free criterion (range /
    # declared events / declared peaks — review 2026-07-06, finding 5).
    # "failed" selects both. The review loop then offers the second
    # [a]ccept — accepting it writes a reference — and the first none.
    if "no-baseline" in filters and not comp.has_reference and status != FAIL:
        return True
    if "failed" in filters and status == FAIL:
        return True
    if "warnings" in filters and comp.warnings:
        return True
    # NO_REF too: nothing was scored, and `compare_one` marks it passed.
    return bool("passed" in filters and status in (PASS, NO_REF) and not comp.warnings)
