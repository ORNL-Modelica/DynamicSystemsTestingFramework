"""`dstf uses --since`'s facts (`discovery.uses`): the project's source
roots and their top packages, the class a differing file is asked as, and
what differs between a revision and the disk — over real temporary git
repositories. What the command prints is tests/test_uses_cli.py's."""

from __future__ import annotations

import json
import os

import pytest

from conftest import GitRepo
from dstf.config import Config
from dstf.discovery.uses import (
    NO_CLASS,
    NO_TOP,
    Changes,
    changed_since,
    class_of,
    source_roots,
)
from dstf.storage.git_revisions import GitError

# --------------------------------------------------------------------------
# From a file to a class
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "deleted", "expected"),
    [
        ("A/B/C.mo", False, "Top.A.B.C"),
        ("X.mo", False, "Top.X"),
        ("A/B/package.mo", False, "Top.A.B"),
        ("package.mo", False, "Top"),
        # deleted: the package that held it
        ("A/B/C.mo", True, "Top.A.B"),
        ("X.mo", True, "Top"),
        ("A/B/package.mo", True, "Top.A"),
        ("A/package.mo", True, "Top"),
        # the root's own package.mo gone: the top cannot be named
        ("package.mo", True, None),
        # a name that is no plain identifier: the nearest package above
        # whose names all are (a quoted class is stored under another
        # spelling)
        ("A/'my model'.mo", False, "Top.A"),
        ("A/My Pkg/B/C.mo", False, "Top.A"),
        ("A/B/C-1.mo", True, "Top.A.B"),
        ("1A/package.mo", False, "Top"),
        # no class stores it
        ("Resources/Data/table.txt", False, None),
        ("Resources/C-Sources/f.c", True, None),
        ("A/B/C.mo.bak", False, None),
        ("A/B/C.mos", False, None),
    ],
)
def test_the_class_a_file_is_asked_as(path, deleted, expected):
    assert class_of("Top", path, deleted) == expected


# --------------------------------------------------------------------------
# The source roots
# --------------------------------------------------------------------------


def _config(refs, **keys) -> Config:
    (refs / "testing.json").write_text(
        json.dumps({"backend": "OpenModelica", **keys}), encoding="utf-8"
    )
    return Config(config_file=refs / "testing.json")


def test_roots_are_named_by_what_their_file_declares(tmp_path):
    lib = tmp_path / "Lib 2.0"
    lib.mkdir()
    (lib / "package.mo").write_text("within ;\npackage Lib\nend Lib;\n")
    dep = tmp_path / "Dep 1.0"
    dep.mkdir()
    (dep / "package.mo").write_text("within ;\npackage Dep\nend Dep;\n")
    (tmp_path / "One.mo").write_text(
        "within ;\npackage Uno\n  model M end M;\nend Uno;\npackage Due\nend Due;\n"
    )
    config = _config(
        tmp_path,
        source_path="Lib 2.0",
        dependencies=["Dep 1.0/package.mo", "One.mo", "Modelica"],
    )
    assert [(r.path, r.tops, r.one_file) for r in source_roots(config)] == [
        (lib, ("Lib",), False),
        (dep, ("Dep",), False),
        (tmp_path / "One.mo", ("Uno", "Due"), True),
    ]


def test_a_root_whose_name_cannot_be_read_or_is_taken_has_none(tmp_path):
    """Its files cannot be asked as classes of its own: a change there
    counts every test."""
    for name, text in [
        ("Lib", "within ;\npackage Lib\nend Lib;\n"),
        ("Gone", None),
        ("Cut", "within ;\npackage Cut\n  model M\n"),
        ("Again", "within ;\npackage Lib\nend Lib;\n"),
        ("Dep", "within ;\npackage Dep\nend Dep;\n"),
        ("Twice", "within ;\npackage Dep\nend Dep;\n"),
    ]:
        (tmp_path / name).mkdir()
        if text is not None:
            (tmp_path / name / "package.mo").write_text(text)
    config = _config(
        tmp_path,
        source_path="Lib",
        dependencies=["Gone", "Cut", "Again", "Dep", "Twice"],
    )
    assert [r.tops for r in source_roots(config)] == [
        ("Lib",),
        (),
        (),
        (),
        ("Dep",),
        (),
    ]


# --------------------------------------------------------------------------
# What differs between a revision and the disk
# --------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A library kept in a SUBFOLDER of its repository, with its reference
    root inside it, its testing.json at its top and its test_spec.json in
    a folder of its own — committed once. No user or system git
    configuration is read."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git = GitRepo(tmp_path / "work")
    lib = git.top / "Lib"
    files = {
        "package.mo": "within ;\npackage Lib\nend Lib;\n",
        "package.order": "A\nZ\n",
        "A/package.mo": "within Lib;\npackage A\nend A;\n",
        "A/package.order": "M\nGone\n",
        "A/M.mo": "within Lib.A;\nmodel M\nend M;\n",
        "A/Gone.mo": "within Lib.A;\nmodel Gone\nend Gone;\n",
        "Z/package.mo": "within Lib;\npackage Z\nend Z;\n",
        "Resources/Data/table.txt": "1 2 3\n",
        "Resources/ReferenceResults/OpenModelica/linux/ref_a.json": "{}\n",
        "Resources/Specs/test_spec.json": "{}\n",
        "testing.json": json.dumps(
            {
                "source_path": ".",
                "backend": "OpenModelica",
                "reference_root": "Resources/ReferenceResults",
                "test_spec": "Resources/Specs/test_spec.json",
            }
        ),
    }
    for path, text in files.items():
        (lib / path).parent.mkdir(parents=True, exist_ok=True)
        (lib / path).write_text(text, encoding="utf-8")
    (git.top / "README.md").write_text("outside the library\n")
    git.lib = lib
    git.first = git.commit("the library")
    return git


def _changed(changes: Changes) -> dict[str, tuple]:
    """Every differing file of every root: path → (deleted, classes,
    reason)."""
    return {
        f.path: (f.deleted, f.classes, f.reason)
        for repo in changes.repos
        for root in repo.roots
        for f in root.files
    }


def _since(config: Config, rev: str = "HEAD") -> tuple[Changes, list[str]]:
    said: list[str] = []
    return changed_since(config, rev, said.append), said


def test_nothing_differs_in_a_clean_checkout(project):
    changes, said = _since(Config(config_file=project.lib / "testing.json"))
    assert _changed(changes) == {} and changes.classes == []
    # every file at the revision under the root, DSTF's own left out
    assert said == ["Comparing 8 files with HEAD ..."]


def test_edited_added_and_deleted_files_and_package_order(project):
    lib = project.lib
    (lib / "A" / "M.mo").write_text("within Lib.A;\nmodel M\n  Real x;\nend M;\n")
    (lib / "A" / "New.mo").write_text("within Lib.A;\nmodel New\nend New;\n")
    (lib / "A" / "Gone.mo").unlink()
    (lib / "A" / "package.order").write_text("M\nNew\n")
    # a new file that is no .mo file is not seen: an editor's backup, a
    # data file no .mo file names yet
    (lib / "A" / "M.mo~").write_text("model M end M;\n")
    (lib / "Resources" / "Data" / "new.txt").write_text("4 5 6\n")
    (project.top / "README.md").write_text("outside the library, edited\n")
    changes, _ = _since(Config(config_file=lib / "testing.json"))
    assert _changed(changes) == {
        "A/Gone.mo": (True, ("Lib.A",), None),
        "A/M.mo": (False, ("Lib.A.M",), None),
        "A/New.mo": (False, ("Lib.A.New",), None),
    }
    assert changes.classes == ["Lib.A", "Lib.A.M", "Lib.A.New"]
    assert changes.every_test == []


def test_a_committed_change_since_the_revision_counts_as_one_on_disk(project):
    (project.lib / "Z" / "package.mo").write_text(
        "within Lib;\npackage Z\n  constant Real k = 1;\nend Z;\n"
    )
    project.commit("edit Z")
    config = Config(config_file=project.lib / "testing.json")
    assert _changed(_since(config, project.first)[0]) == {
        "Z/package.mo": (False, ("Lib.Z",), None)
    }
    assert _changed(_since(config, "HEAD")[0]) == {}


def test_dstf_s_own_files_are_never_compared(project):
    """The reference root inside the library, testing.json and
    test_spec.json: an accept or an edited spec would count every test."""
    lib = project.lib
    refs = lib / "Resources" / "ReferenceResults" / "OpenModelica" / "linux"
    (refs / "ref_a.json").write_text('{"time": [0]}\n')
    (refs / "ref_b.json").write_text("{}\n")
    (refs / "ref_a.json.mo").write_text("model Odd end Odd;\n")
    (lib / "Resources" / "Specs" / "test_spec.json").write_text('{"tests": []}\n')
    config = json.loads((lib / "testing.json").read_text())
    config["library_name"] = "Lib"
    (lib / "testing.json").write_text(json.dumps(config))
    changes, _ = _since(Config(config_file=lib / "testing.json"))
    assert _changed(changes) == {}
    # and any other file under the root that is no .mo file counts every test
    (lib / "Resources" / "Data" / "table.txt").write_text("1 2 4\n")
    changes, _ = _since(Config(config_file=lib / "testing.json"))
    assert _changed(changes) == {
        "Resources/Data/table.txt": (False, (), NO_CLASS),
    }
    assert [f.path for f in changes.every_test] == ["Resources/Data/table.txt"]


@pytest.mark.parametrize("refs", [".", ".."])
def test_a_reference_root_that_is_the_library_or_holds_it_hides_nothing(project, refs):
    """Only a reference root INSIDE the library is left out: one that holds
    the library holds its classes too."""
    lib = project.lib
    config = json.loads((lib / "testing.json").read_text())
    config["reference_root"] = refs
    (lib / "testing.json").write_text(json.dumps(config))
    (lib / "A" / "M.mo").write_text("within Lib.A;\nmodel M\n  Real x;\nend M;\n")
    changes, _ = _since(Config(config_file=lib / "testing.json"))
    assert _changed(changes) == {"A/M.mo": (False, ("Lib.A.M",), None)}


def test_files_that_count_every_test_are_listed_first(project):
    lib = project.lib
    (lib / "A" / "M.mo").write_text("within Lib.A;\nmodel M\n  Real x;\nend M;\n")
    (lib / "Resources" / "Data" / "table.txt").unlink()
    changes, _ = _since(Config(config_file=lib / "testing.json"))
    [root] = changes.repos[0].roots
    assert [(f.path, f.reason) for f in root.files] == [
        ("Resources/Data/table.txt", NO_CLASS),
        ("A/M.mo", None),
    ]


def test_a_linked_directory_is_not_entered_and_a_link_is_its_target_s_name(
    project, tmp_path
):
    """A sub-package kept elsewhere and linked in: git holds the link, not
    what is under it, so its .mo files would read as added every time."""
    kept = tmp_path / "kept" / "Shared"
    kept.mkdir(parents=True)
    (kept / "package.mo").write_text("within Lib;\npackage Shared\nend Shared;\n")
    (kept / "S.mo").write_text("within Lib.Shared;\nmodel S\nend S;\n")
    (project.lib / "Shared").symlink_to(kept)
    (project.lib / "A" / "Link.mo").symlink_to("M.mo")
    project.commit("a linked sub-package and a linked class")
    config = Config(config_file=project.lib / "testing.json")
    assert _changed(_since(config)[0]) == {}
    # what a link points at is its content: pointed elsewhere, it differs
    (project.lib / "Shared").unlink()
    (project.lib / "Shared").symlink_to(tmp_path / "kept")
    (project.lib / "A" / "Link.mo").unlink()
    (project.lib / "A" / "Link.mo").symlink_to("Gone.mo")
    assert _changed(_since(config)[0]) == {
        "Shared": (False, (), NO_CLASS),
        "A/Link.mo": (False, ("Lib.A.Link",), None),
    }


def test_a_nested_repository_is_named_and_its_files_are_not_compared(project):
    inner = GitRepo(project.lib / "Vendored")
    (inner.top / "package.mo").write_text(
        "within Lib;\npackage Vendored\nend Vendored;\n"
    )
    (inner.top / "V.mo").write_text("within Lib.Vendored;\nmodel V\nend V;\n")
    inner.commit("the inner repository")
    (inner.top / "V.mo").write_text("within Lib.Vendored;\nmodel V\n Real x;\nend V;\n")
    changes, _ = _since(Config(config_file=project.lib / "testing.json"))
    [root] = changes.repos[0].roots
    assert (root.files, root.not_compared) == ((), ("Vendored",))


def test_one_repository_holding_several_roots(project):
    """A dependency's folder and a one-file dependency in the library's
    repository: one revision, each root's files under its own top."""
    top = project.top
    (top / "Dep").mkdir()
    (top / "Dep" / "package.mo").write_text("within ;\npackage Dep\nend Dep;\n")
    (top / "Dep" / "B").mkdir()
    (top / "Dep" / "B" / "package.mo").write_text("within Dep;\npackage B\nend B;\n")
    (top / "One.mo").write_text(
        "within ;\npackage Uno\nend Uno;\npackage Due\nend Due;\n"
    )
    (top / "Again").mkdir()
    (top / "Again" / "package.mo").write_text("within ;\npackage Lib\nend Lib;\n")
    config = json.loads((project.lib / "testing.json").read_text())
    config["dependencies"] = ["../Dep", "../One.mo", "../Again"]
    (project.lib / "testing.json").write_text(json.dumps(config))
    project.commit("three dependencies")
    (top / "Dep" / "B" / "package.mo").unlink()
    (top / "One.mo").write_text((top / "One.mo").read_text() + "// edited\n")
    (top / "Again" / "package.mo").write_text(
        "within ;\npackage Lib\n  // x\nend Lib;\n"
    )
    (project.lib / "A" / "M.mo").write_text(
        "within Lib.A;\nmodel M\n Real x;\nend M;\n"
    )
    changes, said = _since(Config(config_file=project.lib / "testing.json"))
    [repo] = changes.repos
    assert repo.top == top
    assert [
        (r.root.tops, [(f.path, f.deleted, f.classes, f.reason) for f in r.files])
        for r in repo.roots
    ] == [
        (("Lib",), [("A/M.mo", False, ("Lib.A.M",), None)]),
        (("Dep",), [("B/package.mo", True, ("Dep",), None)]),
        (("Uno", "Due"), [("One.mo", False, ("Uno", "Due"), None)]),
        ((), [("package.mo", False, (), NO_TOP)]),
    ]
    assert changes.classes == ["Dep", "Due", "Lib.A.M", "Uno"]
    # one line for the one repository
    assert len(said) == 1 and said[0].startswith("Comparing ")


def test_a_dependency_whose_package_mo_is_gone_counts_every_test(project):
    """Its top name can no longer be read: no file of it can be asked as a
    class."""
    top = project.top
    (top / "Dep").mkdir()
    (top / "Dep" / "package.mo").write_text("within ;\npackage Dep\nend Dep;\n")
    (top / "Dep" / "M.mo").write_text("within Dep;\nmodel M\nend M;\n")
    config = json.loads((project.lib / "testing.json").read_text())
    config["dependencies"] = ["../Dep"]
    (project.lib / "testing.json").write_text(json.dumps(config))
    project.commit("a dependency")
    (top / "Dep" / "package.mo").unlink()
    (top / "Dep" / "M.mo").write_text("within Dep;\nmodel M\n Real x;\nend M;\n")
    changes, _ = _since(Config(config_file=project.lib / "testing.json"))
    assert _changed(changes) == {
        "M.mo": (False, (), NO_TOP),
        "package.mo": (True, (), NO_TOP),
    }


def test_a_dependency_in_no_repository_is_not_compared(project, tmp_path, monkeypatch):
    dep = tmp_path / "unpacked" / "Dep"
    dep.mkdir(parents=True)
    (dep / "package.mo").write_text("within ;\npackage Dep\nend Dep;\n")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path / "unpacked"))
    config = json.loads((project.lib / "testing.json").read_text())
    config["dependencies"] = [str(dep)]
    (project.lib / "testing.json").write_text(json.dumps(config))
    changes, said = _since(Config(config_file=project.lib / "testing.json"))
    assert [r.tops for r in changes.unversioned] == [("Dep",)]
    assert said[0] == "Dep is in no git repository: its files are not compared."
    # the library is compared as ever
    (project.lib / "A" / "M.mo").write_text(
        "within Lib.A;\nmodel M\n Real x;\nend M;\n"
    )
    changes, _ = _since(Config(config_file=project.lib / "testing.json"))
    assert _changed(changes) == {"A/M.mo": (False, ("Lib.A.M",), None)}


def test_a_library_in_no_repository_is_an_error(tmp_path, monkeypatch):
    lib = tmp_path / "Lib"
    lib.mkdir()
    (lib / "package.mo").write_text("within ;\npackage Lib\nend Lib;\n")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    config = _config(lib, source_path=".")
    with pytest.raises(GitError) as exc:
        _since(config)
    assert str(exc.value) == f"not a git repository: {lib}"
