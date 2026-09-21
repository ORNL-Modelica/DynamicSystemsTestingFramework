"""``dstf migrate-variable-names`` — see ``storage/migrate_names.py``.
Deleted at the v1.0 tag with it."""

from __future__ import annotations

import argparse
from collections import Counter

from .common import _build_config


def cmd_migrate_variable_names(args: argparse.Namespace) -> int:
    from ..storage.migrate_names import (
        EXPRESSION_ADDED,
        MIGRATED,
        RENAMED,
        SKIPPED,
        UNKNOWN_MODEL,
        migrate,
    )

    config = _build_config(args)
    report = migrate(config, apply=args.apply)
    root = config.reference_root
    assert root is not None
    verb = "rewritten" if args.apply else "would rewrite"

    per: dict[str, Counter] = {}
    for outcome in report.files:
        partition = "/".join(outcome.path.relative_to(root).parts[:2])
        per.setdefault(partition, Counter())[outcome.status] += 1
    for partition, counts in sorted(per.items()):
        print(
            f"{partition}: {counts[RENAMED]} {verb}, "
            f"{counts[EXPRESSION_ADDED]} gained an expression field only, "
            f"{counts[MIGRATED]} already migrated, "
            f"{counts[SKIPPED]} skipped, {counts[UNKNOWN_MODEL]} unknown model"
        )
    for outcome in report.files:
        if outcome.status in (SKIPPED, UNKNOWN_MODEL):
            rel = outcome.path.relative_to(root)
            print(f"  {outcome.status}: {rel}: {'; '.join(outcome.reasons)}")

    print(f"test_spec.json: {len(report.spec_rewritten)} key(s) {verb}")
    for model_id, old, new in report.spec_rewritten:
        print(f"  {model_id}: {old!r} -> {new!r}")
    for line in report.spec_skipped:
        print(f"  left alone: {line}")
    if report.spec_rewritten:
        print(
            "  (the whole spec is re-serialized — the same bytes a Save from "
            "`dstf author` writes)"
        )

    for line in report.expression_differs:
        print(f"expression differs, left as stored: {line}")
    if report.reaccept:
        print("re-accept needed — these have no stored data of their own now:")
        for model_id, variable in report.reaccept:
            print(f"  {model_id}: {variable}")

    if not args.apply:
        print("dry run — nothing written; pass --apply to write")
    return 0 if report.clean else 1
