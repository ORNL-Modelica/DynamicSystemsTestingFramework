"""``dstf migrate-variable-names`` — see ``storage/migrate_names.py``.
Deleted at the v1.0 tag with it."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict

from .common import _build_config


def cmd_migrate_variable_names(args: argparse.Namespace) -> int:
    from ..storage.migrate_names import (
        EXPRESSION_ADDED,
        MIGRATED,
        RENAMED,
        REORDERED,
        SKIPPED,
        UNKNOWN_MODEL,
        migrate,
    )

    config = _build_config(args)
    report = migrate(config, apply=args.apply)
    root = config.reference_root
    assert root is not None
    # Nothing is written while a file could not be read, whatever --apply
    # said: the decisions were made from an incomplete picture.
    wrote = args.apply and not report.unreadable
    verb = "rewritten" if wrote else "would rewrite"

    def partition_of(path):
        return "/".join(path.relative_to(root).parts[:2])

    per: dict[str, Counter] = {}
    for outcome in report.files:
        per.setdefault(partition_of(outcome.path), Counter())[outcome.status] += 1
    crlf: dict[str, list] = defaultdict(list)
    for path in report.crlf:
        crlf[partition_of(path)].append(path)
    for partition, counts in sorted(per.items()):
        print(
            f"{partition}: {counts[RENAMED]} {verb}, "
            f"{counts[EXPRESSION_ADDED]} gained an expression field only, "
            f"{counts[REORDERED]} had their expression moved beside the name, "
            f"{counts[MIGRATED]} already migrated, "
            f"{counts[SKIPPED]} skipped, {counts[UNKNOWN_MODEL]} unknown model"
        )
        for path in crlf[partition]:
            print(f"  CRLF: {path.relative_to(root)}")
    if report.crlf:
        print(
            f"{len(report.crlf)} file(s) held CRLF line endings and are written LF, "
            "as the store writes every baseline — each shows as a whole-file diff"
        )
    for outcome in report.files:
        if outcome.status in (SKIPPED, UNKNOWN_MODEL):
            rel = outcome.path.relative_to(root)
            print(f"  {outcome.status}: {rel}: {'; '.join(outcome.reasons)}")
    for soft in report.orphan_soft_checks:
        print(f"orphan soft-checks, no primary beside them: {soft.relative_to(root)}")

    print(f"test_spec.json: {len(report.spec_rewritten)} key(s) {verb}")
    for model_id, old, new, occurrences in report.spec_rewritten:
        times = f" ({occurrences} occurrences)" if occurrences > 1 else ""
        print(f"  {model_id}: {old!r} -> {new!r}{times}")
    for line in report.spec_skipped:
        print(f"  left alone: {line}")
    if report.spec_rewritten:
        print(
            "  (the whole spec is re-serialized — the same bytes a Save from "
            "`dstf author` writes)"
        )
    for model_id in report.spec_not_considered:
        print(f"  no UnitTests variables here, so no key was considered: {model_id}")

    for line in report.expression_differs:
        print(f"expression differs, left as stored: {line}")
    if report.reaccept:
        print("re-accept needed — these have no stored data of their own now:")
        for model_id, variable in report.reaccept:
            print(f"  {model_id}: {variable}")

    if report.unreadable:
        print(
            f"{len(report.unreadable)} file(s) could not be read; NOTHING is "
            "written while one stands — every file's outcome is decided before "
            "any file is written"
        )
    if not args.apply:
        print("dry run — nothing written; pass --apply to write")
    return 0 if report.clean else 1
