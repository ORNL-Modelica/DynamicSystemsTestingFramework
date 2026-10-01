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
from ..reporting.status import NOT_RUN, exit_code, nothing_ran


def _build_config(args: argparse.Namespace) -> Config:
    """Build Config from parsed CLI arguments."""
    kwargs = {}
    if args.source_path:
        kwargs["source_path"] = Path(args.source_path)
    if hasattr(args, "config") and args.config:
        kwargs["config_file"] = Path(args.config)
    if hasattr(args, "reference_root") and args.reference_root:
        kwargs["reference_root"] = Path(args.reference_root)
    if hasattr(args, "backend") and args.backend:
        kwargs["backend_name"] = args.backend
    if hasattr(args, "backend_path") and args.backend_path:
        kwargs["backend_path"] = args.backend_path
    if hasattr(args, "show_ide") and args.show_ide:
        kwargs["show_ide"] = True
    if hasattr(args, "work_dir") and args.work_dir:
        kwargs["work_dir"] = Path(args.work_dir)
    if hasattr(args, "parallel") and args.parallel:
        kwargs["parallel"] = args.parallel
    if hasattr(args, "batch_size") and args.batch_size is not None:
        kwargs["batch_size"] = args.batch_size
    if hasattr(args, "dymola_interface") and args.dymola_interface:
        kwargs["dymola_interface_path"] = Path(args.dymola_interface)
    # `is not None`, NOT truthiness — `--tolerance 0` is the strictest
    # possible setting and Config/comparator honor an explicit 0.0
    # (comparator.py resolve chain, config.py file-tolerance precedence).
    if hasattr(args, "tolerance") and args.tolerance is not None:
        kwargs["tolerance"] = args.tolerance
    if hasattr(args, "default_points") and args.default_points:
        kwargs["default_points"] = args.default_points
    if hasattr(args, "timeout") and args.timeout:
        kwargs["timeout"] = args.timeout
    if hasattr(args, "test_spec") and args.test_spec:
        kwargs["test_spec_file"] = Path(args.test_spec)
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
    §5)."""
    comps = list(comparisons)
    if nothing_ran(comps):
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
    """Check if a comparison matches the active review filters."""
    if "all" in filters:
        return True
    if "sim-failed" in filters and not comp.sim_success:
        return True
    if "no-baseline" in filters and not comp.has_reference:
        return True
    # A failing test matches "failed" even without a baseline —
    # baseline-free modes (range / declared events / declared peaks)
    # carry a real verdict (review 2026-07-06, finding 5).
    if "failed" in filters and comp.sim_success and not comp.passed:
        return True
    if "warnings" in filters and comp.warnings:
        return True
    return bool(
        "passed" in filters and comp.sim_success and comp.passed and not comp.warnings
    )
