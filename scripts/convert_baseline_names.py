#!/usr/bin/env python3
"""Rename a library's baseline files from numbers to codes of their model ids.

A baseline file used to be numbered (ref_0001.json, ref_0002.json, ...), and
the number was kept in a ".id_high_water.json" beside it. A baseline file is
now named from its model id: ref_<code>.json, where the code is the first 12
hex digits of sha256 over the model id (UTF-8). This script converts
reference roots from the old names to the new ones.

Usage:
    python scripts/convert_baseline_names.py ROOT [ROOT ...]
        [--keep BACKEND/OS/ref_NNNN.json ...] [--dry-run]

A ROOT is a reference root: the folder that holds <Backend>/<os>/ folders
(testing.json's reference_root). Give EVERY root at once whose companions
point into one another: a companion's path to another library's baseline is
rewritten to that baseline's new name.

What it does, in each <Backend>/<os>/ folder:

  * renames each ref_*.json to ref_<code>.json, and deletes its "test_id"
    line -- a text edit: line endings and every other byte are kept;
  * renames soft_checks/ref_NNNN/ and companions/ref_NNNN/ with their file;
  * rewrites an external companion's path to a converted file's new name;
  * deletes .id_high_water.json.

It refuses -- and writes nothing at all, in any root -- when:

  * one model is held by two files of one folder, and no --keep names the
    one to keep (the others are deleted, with their role folders);
  * two model ids share a code;
  * a file holding a "test_id" is written on one line (only that line can be
    deleted; reformat the file with one key per line);
  * a companion's path names a numbered baseline outside the roots given,
    or one on a Windows drive (D:\\...) when it runs on another system;
  * a file is unreadable, holds no model id, or is named by another model's
    code; a role folder belongs to no baseline.

Everything is checked before anything is written. Run it on a committed tree.
A second run says "nothing to do".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

HIGH_WATER = ".id_high_water.json"
ROLE_DIRS = ("soft_checks", "companions")
#: A new name. Every other ref_*.json is an old one: a code can be all digits
#: (TRANSFORM...HeXeAlternate -> ref_121677875640.json), so old and new are
#: never told apart by a digit pattern.
NEW_NAME = re.compile(r"^ref_[0-9a-f]{12}\.json$")
TEST_ID_LINE = re.compile(rb'^[ \t]*"test_id":[ \t]*"[^"\r\n]*",[ \t]*\r?\n', re.M)


def new_name(model_id: str) -> str:
    """``ref_<code>.json``.

    The same rule as ``dstf.storage.names.baseline_file``, written out here so
    the script runs with any Python 3.10+, without DSTF installed (a library
    owner may convert from another environment than the one that runs dstf).
    tests/test_convert_baseline_names.py and tests/test_baseline_names.py
    pin both to the same literal codes.
    """
    return f"ref_{hashlib.sha256(model_id.encode('utf-8')).hexdigest()[:12]}.json"


def is_old_name(name: str) -> bool:
    return (
        name.startswith("ref_") and name.endswith(".json") and not NEW_NAME.match(name)
    )


class Refused(Exception):
    pass


@dataclass
class FileOp:
    old: Path
    new: Path
    old_bytes: bytes
    new_bytes: bytes

    def changes(self) -> bool:
        return self.old != self.new or self.old_bytes != self.new_bytes


@dataclass
class Plan:
    files: list[FileOp] = field(default_factory=list)
    deletes: list[tuple[Path, str]] = field(default_factory=list)
    dir_renames: list[tuple[Path, Path]] = field(default_factory=list)
    meta_edits: list[tuple[Path, str, str, bytes]] = field(default_factory=list)
    high_water: list[Path] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    settled: set[Path] = field(default_factory=set)

    def empty(self) -> bool:
        return not (
            any(op.changes() for op in self.files)
            or self.deletes
            or self.dir_renames
            or self.meta_edits
            or self.high_water
        )


def strip_test_id(path: Path, raw: bytes, data: dict) -> bytes:
    """``raw`` without its "test_id" line; every other byte kept."""
    if "test_id" not in data:
        return raw
    if raw.rstrip(b"\r\n").count(b"\n") == 0:
        raise Refused(
            f"{path}: is one line -- its test_id cannot be deleted as a line; "
            "reformat the file with one key per line"
        )
    lines = TEST_ID_LINE.findall(raw)
    if len(lines) != 1:
        raise Refused(
            f"{path}: holds test_id but {len(lines)} lines read as it "
            '(a line `"test_id": "...",` is what this script deletes)'
        )
    out = TEST_ID_LINE.sub(b"", raw, count=1)
    after = json.loads(out)
    want = {k: v for k, v in data.items() if k != "test_id"}
    if after != want or list(after) != list(want):
        raise Refused(f"{path}: deleting the test_id line changed something else")
    return out


def folders(root: Path) -> list[Path]:
    """The ``<Backend>/<os>/`` folders of ``root`` that hold baselines."""
    out = []
    for backend in sorted(
        p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
    ):
        for os_dir in sorted(p for p in backend.iterdir() if p.is_dir()):
            if (
                any(os_dir.glob("ref_*.json"))
                or (os_dir / HIGH_WATER).exists()
                or any((os_dir / r).is_dir() for r in ROLE_DIRS)
            ):
                out.append(os_dir)
    return out


def plan_folder(
    folder: Path, keep: set[Path], plan: Plan, model_of: dict[Path, str]
) -> None:
    by_model: dict[str, list[Path]] = {}
    for f in sorted(folder.glob("ref_*.json")):
        try:
            data = json.loads(f.read_bytes())
        except (OSError, ValueError) as e:
            plan.problems.append(f"{f}: unreadable ({e})")
            continue
        mid = data.get("model_id") if isinstance(data, dict) else None
        if not isinstance(mid, str) or not mid:
            plan.problems.append(f"{f}: holds no model_id")
            continue
        if NEW_NAME.match(f.name) and f.name != new_name(mid):
            plan.problems.append(
                f"{f}: is named by another code than its model's ({new_name(mid)}, {mid})"
            )
            continue
        model_of[f.resolve()] = mid
        by_model.setdefault(mid, []).append(f)

    for mid, files in by_model.items():
        if len(files) > 1:
            kept = [f for f in files if f.resolve() in keep]
            if len(kept) != 1:
                plan.problems.append(
                    f"{folder}: {mid} is held by {', '.join(f.name for f in files)} "
                    "-- name the one to keep with --keep"
                )
                continue
            plan.settled.add(kept[0].resolve())
            for f in files:
                if f is not kept[0]:
                    plan.deletes.append(
                        (f, f"a second file of {mid}; {kept[0].name} is kept")
                    )
                    for r in ROLE_DIRS:
                        if (folder / r / f.stem).exists():
                            plan.problems.append(
                                f"{folder / r / f.stem}: belongs to {f.name}, which this run deletes"
                            )
            files = kept
        (f,) = files
        raw = f.read_bytes()
        try:
            new_bytes = strip_test_id(f, raw, json.loads(raw))
        except Refused as e:
            plan.problems.append(str(e))
            continue
        target = folder / new_name(mid)
        if target.exists() and target.resolve() != f.resolve():
            plan.problems.append(f"{target}: exists beside {f.name}")
            continue
        plan.files.append(FileOp(f, target, raw, new_bytes))
        for r in ROLE_DIRS:
            src, dst = folder / r / f.stem, folder / r / target.stem
            if src != dst and src.exists():
                if dst.exists():
                    plan.problems.append(f"{dst}: exists beside {src.name}")
                else:
                    plan.dir_renames.append((src, dst))

    stems = {p.stem for p in folder.glob("ref_*.json")}
    for r in ROLE_DIRS:
        role = folder / r
        for d in sorted(role.iterdir()) if role.is_dir() else []:
            if d.is_dir() and d.name not in stems:
                plan.problems.append(f"{d}: a role folder of no baseline in {folder}")

    if (folder / HIGH_WATER).exists():
        plan.high_water.append(folder / HIGH_WATER)


def on_a_windows_drive(path: str) -> bool:
    """Whether ``path`` is a Windows drive or share path (``D:\\...``,
    ``D:/...``, ``\\\\server\\share``) on a system that is not Windows: one this
    run can neither resolve nor rewrite. A POSIX path never starts with a
    drive, and one that starts with ``/`` is not taken for a share."""
    return (
        sys.platform != "win32"
        and not path.startswith("/")
        and bool(PureWindowsPath(path).drive)
    )


def under(path: Path, roots: list[Path]) -> bool:
    return any(path == r or r in path.parents for r in roots)


def plan_companion_paths(
    plan: Plan, roots: list[Path], all_folders: list[Path], model_of: dict[Path, str]
) -> None:
    resolved_roots = [r.resolve() for r in roots]
    for folder in all_folders:
        co = folder / "companions"
        if not co.is_dir():
            continue
        for meta in sorted(co.glob("*/*.json")):
            raw = meta.read_bytes()
            try:
                entry = json.loads(raw)
            except ValueError:
                continue  # not metadata: a frozen companion's data
            if not isinstance(entry, dict) or entry.get("kind") != "external":
                continue
            path = entry.get("path")
            if not isinstance(path, str):
                continue
            if on_a_windows_drive(path):
                # Read as POSIX, a `D:/` path is relative to the folder and a
                # `D:\` one has no ref name: the companion was left naming
                # the old file, said as a note or not at all.
                if is_old_name(PureWindowsPath(path).name):
                    plan.problems.append(
                        f"{meta}: its path {path} is on a Windows drive -- run the "
                        "convert on Windows, or make the path relative to its "
                        "baseline's folder"
                    )
                continue
            if not is_old_name(Path(path).name):
                continue
            # DSTF reads a relative path against the baseline's folder.
            p = Path(path)
            target = (p if p.is_absolute() else folder / p).resolve()
            if not under(target, resolved_roots):
                plan.problems.append(
                    f"{meta}: its path {path} names a baseline outside the roots given "
                    "-- give that baseline's root in the same run"
                )
                continue
            mid = model_of.get(target)
            if mid is None:
                if target.exists():
                    plan.problems.append(
                        f"{meta}: its path {path} names a file this run does not convert"
                    )
                else:
                    plan.notes.append(
                        f"{meta}: its path {path} names no file; left as it is"
                    )
                continue
            new_path = path[: len(path) - len(p.name)] + new_name(mid)
            old_tok, new_tok = json.dumps(path).encode(), json.dumps(new_path).encode()
            if raw.count(old_tok) != 1:
                plan.problems.append(
                    f"{meta}: its path is written {raw.count(old_tok)} times"
                )
                continue
            out = raw.replace(old_tok, new_tok)
            if json.loads(out) != {**entry, "path": new_path}:
                plan.problems.append(
                    f"{meta}: rewriting its path changed something else"
                )
                continue
            plan.meta_edits.append((meta, path, new_path, out))


def make_plan(roots: list[Path], keep_args: list[str]) -> Plan:
    plan = Plan()
    all_folders = [f for r in roots for f in folders(r)]
    keep: set[Path] = set()
    for k in keep_args:
        hits = [r / k for r in roots if (r / k).is_file()]
        if len(hits) > 1:
            plan.problems.append(f"--keep {k}: names a file under {len(hits)} roots")
        elif not hits:
            # A run that already settled it deleted the others and renamed
            # this one; a duplicate it did not settle is refused by name.
            plan.notes.append(f"--keep {k}: no such file (already converted?)")
        else:
            keep.add(hits[0].resolve())
    model_of: dict[Path, str] = {}
    for folder in all_folders:
        plan_folder(folder, keep, plan, model_of)
    for k in sorted(keep - plan.settled):
        plan.problems.append(f"--keep {k}: settles no second file of a model")
    owners: dict[str, set[str]] = {}
    for mid in set(model_of.values()):
        owners.setdefault(new_name(mid), set()).add(mid)
    for name, mids in sorted(owners.items()):
        if len(mids) > 1:
            plan.problems.append(
                f"{name}: is the name of {len(mids)} model ids ({', '.join(sorted(mids))})"
            )
    plan_companion_paths(plan, roots, all_folders, model_of)
    return plan


def apply(plan: Plan) -> None:
    # The deleted duplicates first: their names are never a target.
    for path, _why in plan.deletes:
        path.unlink()
    for op in plan.files:
        if not op.changes():
            continue
        tmp = op.new.with_name(op.new.name + ".convert.tmp")
        tmp.write_bytes(op.new_bytes)
        tmp.replace(op.new)
        if op.old != op.new:
            op.old.unlink()
    for src, dst in plan.dir_renames:
        src.rename(dst)
    for meta, _old, _new, out in plan.meta_edits:
        moved = next(
            (dst / meta.name for src, dst in plan.dir_renames if meta.parent == src),
            meta,
        )
        tmp = moved.with_name(moved.name + ".convert.tmp")
        tmp.write_bytes(out)
        tmp.replace(moved)
    for hw in plan.high_water:
        hw.unlink()


def describe(plan: Plan) -> list[str]:
    """One line per change the plan makes."""
    lines = [f"  delete {p} ({why})" for p, why in plan.deletes]
    for op in plan.files:
        if op.old != op.new:
            lines.append(f"  rename {op.old} -> {op.new.name}")
        elif op.old_bytes != op.new_bytes:
            lines.append(f"  edit   {op.old} (its test_id line)")
    lines += [f"  rename {src} -> {dst.name}" for src, dst in plan.dir_renames]
    lines += [
        f"  edit   {meta}: path {old} -> {new}" for meta, old, new, _ in plan.meta_edits
    ]
    lines += [f"  delete {hw}" for hw in plan.high_water]
    return lines


def main(argv: list[str] | None = None) -> int:
    summary, _, rest = (__doc__ or "").partition("\n\n")
    ap = argparse.ArgumentParser(
        prog="convert_baseline_names.py",
        description=summary,
        epilog=rest,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "roots", nargs="+", type=Path, metavar="ROOT", help="a reference root"
    )
    ap.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="BACKEND/OS/ref_NNNN.json",
        help="of a model held by two files in one folder, the file to keep (relative to its root)",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="say what would change; write nothing"
    )
    args = ap.parse_args(argv)
    for r in args.roots:
        if not r.is_dir():
            print(f"{r}: no such directory", file=sys.stderr)
            return 2
    plan = make_plan(args.roots, args.keep)
    renamed = sum(op.old != op.new for op in plan.files)
    edited = sum(op.old_bytes != op.new_bytes for op in plan.files)
    print(
        f"{renamed} files renamed, {edited} test_id lines deleted, "
        f"{len(plan.deletes)} second files deleted, {len(plan.dir_renames)} role folders renamed, "
        f"{len(plan.meta_edits)} companion paths rewritten, {len(plan.high_water)} {HIGH_WATER} deleted"
    )
    for n in plan.notes:
        print(f"  note: {n}")
    if plan.problems:
        print(
            f"REFUSED -- {len(plan.problems)} problem(s); nothing written:",
            file=sys.stderr,
        )
        for p in plan.problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    if plan.empty():
        print("nothing to do")
        return 0
    if args.dry_run:
        print("\n".join(describe(plan)))
        print("dry run: nothing written")
        return 0
    apply(plan)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
