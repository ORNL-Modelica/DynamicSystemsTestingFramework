"""`discovery.library_state`: what a project's source files looked like at
one moment — the stamp of each, the state a Queue click's items carry, and
what differs between two states."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dstf.discovery import library_state
from dstf.discovery.library_state import (
    LibraryChanges,
    LibraryState,
    following,
    source_stamps,
    sweep,
)


def _library(root: Path) -> Path:
    lib = root / "Lib"
    (lib / "Sub" / "Deep").mkdir(parents=True)
    for rel in ("package.mo", "A.mo", "Sub/package.mo", "Sub/B.mo", "Sub/Deep/C.mo"):
        (lib / rel).write_text("model X end X;", encoding="utf-8")
    (lib / "package.order").write_text("A\nSub\n", encoding="utf-8")
    (lib / "Sub" / "notes.txt").write_text("not a source", encoding="utf-8")
    (lib / "Sub" / "result.mat").write_bytes(b"\0")
    return lib


def _config(source_type: str, source_path: Path, dependencies=()):
    return SimpleNamespace(
        source_type=source_type,
        source_path=source_path,
        library_dir=source_path,
        dependencies=[str(d) for d in dependencies],
    )


def _is_mo(name: str) -> bool:
    return name.endswith(".mo")


def test_the_sweep_stamps_the_named_files_spelled_as_the_scan_spells_them(tmp_path):
    lib = _library(tmp_path)
    stamps = sweep([lib], _is_mo)
    assert set(stamps) == set(lib.rglob("*.mo"))
    st = os.stat(lib / "A.mo")
    assert stamps[lib / "A.mo"] == (st.st_mtime_ns, st.st_size)


def test_the_sweep_on_threads_finds_what_the_walk_on_one_finds(tmp_path, monkeypatch):
    lib = _library(tmp_path)
    one = sweep([lib], _is_mo)
    # Every listing is "slow": the directories fan out over the threads.
    monkeypatch.setattr(library_state, "_SLOW_LISTING_SECONDS", -1.0)
    used: list[int] = []
    real = library_state.ThreadPoolExecutor

    def counted(*args, **kwargs):
        used.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(library_state, "ThreadPoolExecutor", counted)
    assert sweep([lib], _is_mo) == one
    assert used == [1]


def test_a_root_that_is_one_file_is_stamped_itself_and_a_missing_one_is_nothing(
    tmp_path,
):
    one = tmp_path / "Single.mo"
    one.write_text("package Single end Single;", encoding="utf-8")
    assert set(sweep([one, tmp_path / "gone"], _is_mo)) == {one}


def test_a_modelica_project_sweeps_its_library_and_its_path_dependencies(tmp_path):
    lib = _library(tmp_path)
    other = tmp_path / "deps" / "Other"
    other.mkdir(parents=True)
    (other / "package.mo").write_text("package Other end Other;", encoding="utf-8")
    (other / "package.order").write_text("", encoding="utf-8")
    single = tmp_path / "deps" / "Single.mo"
    single.write_text("package Single end Single;", encoding="utf-8")
    config = _config(
        "modelica", lib, [other, "Modelica", tmp_path / "deps" / "Single.mo"]
    )
    stamps = source_stamps(config)
    assert stamps is not None
    assert set(stamps) == {
        *lib.rglob("*.mo"),
        lib / "package.order",
        other / "package.mo",
        other / "package.order",
        single,
    }


def test_a_dependency_named_by_its_package_file_is_swept_as_its_directory(tmp_path):
    lib = _library(tmp_path)
    other = _library(tmp_path / "deps")
    config = _config("modelica", lib, [other / "package.mo"])
    stamps = source_stamps(config)
    assert stamps is not None and other / "Sub" / "B.mo" in stamps


def test_a_julia_project_sweeps_its_sources_and_the_rest_sweep_nothing(tmp_path):
    (tmp_path / "src").mkdir()
    for rel in ("Project.toml", "src/Lib.jl", "src/data.csv", "simulate.py"):
        (tmp_path / rel).write_text("", encoding="utf-8")
    stamps = source_stamps(_config("julia", tmp_path))
    assert stamps is not None
    assert set(stamps) == {tmp_path / "Project.toml", tmp_path / "src" / "Lib.jl"}
    # One process per test reads the source as it is: nothing to compare.
    assert source_stamps(_config("python", tmp_path)) is None
    assert source_stamps(_config("fmu", tmp_path)) is None


def test_the_state_after_an_unchanged_sweep_is_the_same_object(tmp_path):
    lib = _library(tmp_path)
    first = following(None, sweep([lib], _is_mo))
    assert first.serial == 1
    assert following(first, sweep([lib], _is_mo)) is first


@pytest.mark.parametrize("what", ["changed", "added", "removed"])
def test_a_sweep_that_differs_is_the_next_state_and_names_what_differs(tmp_path, what):
    lib = _library(tmp_path)
    first = following(None, sweep([lib], _is_mo))
    target = {
        "changed": lib / "A.mo",
        "added": lib / "Sub" / "New.mo",
        "removed": lib / "Sub" / "B.mo",
    }[what]
    if what == "removed":
        target.unlink()
    else:
        target.write_text("model X Real x; end X;", encoding="utf-8")
    second = following(first, sweep([lib], _is_mo))
    assert second is not first and second.serial == 2
    expected = {"changed": (), "added": (), "removed": (), what: (target,)}
    assert second.changes_since(first) == LibraryChanges(**expected)
    assert second.changes_since(first)
    assert not second.changes_since(second)


def test_the_changes_span_every_state_between_the_two(tmp_path):
    a, b, c = Path("/lib/A.mo"), Path("/lib/B.mo"), Path("/lib/C.mo")
    s1 = LibraryState(1, {a: (1, 10), b: (1, 10)})
    s3 = LibraryState(3, {a: (2, 11), c: (1, 5)})
    assert s3.changes_since(s1) == LibraryChanges(
        changed=(a,), added=(c,), removed=(b,)
    )
