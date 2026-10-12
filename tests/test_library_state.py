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


@pytest.fixture(params=["one thread", "threads"])
def walk(request, monkeypatch) -> str:
    """Both walks of :func:`sweep`: the listing of every root is "slow"
    for the second."""
    if request.param == "threads":
        monkeypatch.setattr(library_state, "_SLOW_LISTING_SECONDS", -1.0)
    return request.param


def test_a_sub_package_that_is_a_symbolic_link_is_swept_through_the_link(
    tmp_path, walk
):
    lib = _library(tmp_path)
    kept = tmp_path / "elsewhere" / "Shared"
    (kept / "Inner").mkdir(parents=True)
    (kept / "package.mo").write_text("package Shared end Shared;", encoding="utf-8")
    (kept / "Inner" / "D.mo").write_text("model D end D;", encoding="utf-8")
    os.symlink(kept, lib / "Sub" / "Shared")
    os.symlink(kept / "Inner" / "D.mo", lib / "Linked.mo")  # a file, linked
    stamps = sweep([lib], _is_mo)
    assert set(stamps) == set(lib.rglob("*.mo")) | {
        lib / "Sub" / "Shared" / "package.mo",
        lib / "Sub" / "Shared" / "Inner" / "D.mo",
    }
    st = os.stat(kept / "Inner" / "D.mo")
    assert stamps[lib / "Sub" / "Shared" / "Inner" / "D.mo"] == (
        st.st_mtime_ns,
        st.st_size,
    )
    assert stamps[lib / "Linked.mo"] == (st.st_mtime_ns, st.st_size)
    # An edit behind the link is a change of the library.
    (kept / "Inner" / "D.mo").write_text("model D Real x; end D;", encoding="utf-8")
    assert sweep([lib], _is_mo) != stamps


def test_a_link_that_leads_back_ends_the_walk_and_a_dangling_one_is_skipped(
    tmp_path, walk
):
    lib = _library(tmp_path)
    plain = sweep([lib], _is_mo)
    os.symlink(lib, lib / "Sub" / "Deep" / "up")  # to the root
    os.symlink(lib / "Sub", lib / "Sub" / "again")  # to a directory above it
    os.symlink(tmp_path / "nowhere", lib / "Sub" / "gone")  # dangling
    os.symlink(tmp_path / "nowhere.mo", lib / "Gone.mo")
    stamps = sweep([lib], _is_mo)
    # Every file once by its own spelling; `Sub` once more through the
    # link to it, where the link to itself inside it ends the walk.
    assert set(stamps) == set(plain) | {
        lib / "Sub" / "again" / "package.mo",
        lib / "Sub" / "again" / "B.mo",
        lib / "Sub" / "again" / "Deep" / "C.mo",
    }
    assert sweep([lib], _is_mo) == stamps  # the same spelling every time


def test_of_two_links_to_one_directory_the_first_by_name_is_the_one_walked(tmp_path):
    (tmp_path / "real").mkdir()
    for name in ("b", "a", "c"):
        os.symlink(tmp_path / "real", tmp_path / name)
    listed = [str(tmp_path / name) for name in ("b", "c", "a")]
    assert library_state._followed(listed, set()) == [str(tmp_path / "a")]


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
