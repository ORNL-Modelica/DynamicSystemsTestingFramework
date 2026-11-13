"""The one place DSTF reads git (``storage.git_revisions``): a revision's
files under a folder, a blob's bytes, a commit's short hash and subject —
each failure one sentence, and nothing it runs writes the repository."""

import hashlib
import os
from pathlib import Path

import pytest

from conftest import GitRepo
from dstf.storage.git_revisions import GitError, Repository


@pytest.fixture(autouse=True)
def _no_user_git_config(monkeypatch):
    """The code under test runs git with this process's environment: no
    user or system configuration is read (a developer's ``core.autocrlf``
    would decide what a file on disk hashes to)."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def repo(tmp_path):
    git = GitRepo(tmp_path / "lib")
    (git.top / "refs" / "Dymola" / "linux").mkdir(parents=True)
    return git


def _tree_hash(git_dir: Path) -> str:
    """Every file of ``git_dir`` (the index included), by path and bytes."""
    h = hashlib.sha256()
    for path in sorted(p for p in git_dir.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(git_dir)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def test_a_folder_in_no_repository_is_one_sentence(tmp_path):
    with pytest.raises(GitError) as e:
        Repository.holding(tmp_path)
    assert str(e.value) == f"not a git repository: {tmp_path}"


def test_a_folder_that_is_not_there_is_one_sentence(tmp_path):
    with pytest.raises(GitError) as e:
        Repository.holding(tmp_path / "nope")
    assert str(e.value) == f"not a directory: {tmp_path / 'nope'}"


def test_an_unknown_revision_is_one_sentence_naming_the_repository(repo):
    (repo.top / "refs" / "a.txt").write_text("a")
    repo.commit("first")
    git = Repository.holding(repo.top / "refs")
    with pytest.raises(GitError) as e:
        git.commit("xyz")
    assert str(e.value) == f"unknown revision 'xyz' in {repo.top}"


def test_a_revision_that_starts_like_a_flag_is_no_flag(repo):
    (repo.top / "refs" / "a.txt").write_text("a")
    repo.commit("first")
    with pytest.raises(GitError, match="unknown revision '--all'"):
        Repository.holding(repo.top / "refs").commit("--all")


def test_without_git_on_the_path_is_one_sentence(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(GitError, match="^git is not installed here"):
        Repository.holding(repo.top)


def test_a_commit_is_its_hash_short_hash_and_subject(repo):
    (repo.top / "refs" / "a.txt").write_text("a")
    sha = repo.commit("add the first baseline")
    got = Repository.holding(repo.top / "refs").commit("HEAD")
    assert got.sha == sha
    assert sha.startswith(got.short) and 7 <= len(got.short) < len(sha)
    assert got.subject == "add the first baseline"


def test_files_are_a_revision_s_blobs_under_the_folder_by_path_inside_it(repo):
    folder = repo.top / "refs" / "Dymola" / "linux"
    (folder / "ref_0001.json").write_text("one")
    (folder / "ref_ü.json").write_text("two")
    (repo.top / "outside.json").write_text("not under refs")
    first = repo.commit("first")
    repo.git("mv", "refs/Dymola/linux/ref_0001.json", "refs/Dymola/linux/ref_b.json")
    second = repo.commit("rename")
    git = Repository.holding(repo.top / "refs")
    at_first = git.files(git.commit(first))
    at_second = git.files(git.commit(second))
    assert sorted(at_first) == ["Dymola/linux/ref_0001.json", "Dymola/linux/ref_ü.json"]
    assert sorted(at_second) == ["Dymola/linux/ref_b.json", "Dymola/linux/ref_ü.json"]
    # a rename keeps the blob: the same bytes are the same id
    assert (
        at_first["Dymola/linux/ref_0001.json"] == at_second["Dymola/linux/ref_b.json"]
    )
    assert at_first["Dymola/linux/ref_0001.json"] == repo.git(
        "rev-parse", f"{first}:refs/Dymola/linux/ref_0001.json"
    )


def test_a_folder_a_revision_does_not_hold_has_no_files(repo):
    (repo.top / "README").write_text("before the references")
    first = repo.commit("first")
    (repo.top / "refs" / "Dymola" / "linux" / "ref_a.json").write_text("{}")
    repo.commit("second")
    git = Repository.holding(repo.top / "refs")
    assert git.files(git.commit(first)) == {}


def test_the_folder_may_be_the_repository_itself(repo):
    (repo.top / "refs" / "Dymola" / "linux" / "ref_a.json").write_text("{}")
    sha = repo.commit("first")
    git = Repository.holding(repo.top)
    assert list(git.files(git.commit(sha))) == ["refs/Dymola/linux/ref_a.json"]


def test_a_blob_reads_back_byte_for_byte(repo):
    folder = repo.top / "refs" / "Dymola" / "linux"
    big = bytes(range(256)) * 4096  # 1 MiB: more than a pipe holds
    (folder / "ref_big.json").write_bytes(big)
    (folder / "ref_crlf.json").write_bytes(b'{\r\n"a": "\xff"}\n')
    (folder / "ref_empty.json").write_bytes(b"")
    sha = repo.commit("first")
    git = Repository.holding(repo.top / "refs")
    files = git.files(git.commit(sha))
    with git.reader() as read:
        # each read in turn, the big one between two others
        assert read(files["Dymola/linux/ref_crlf.json"]) == b'{\r\n"a": "\xff"}\n'
        assert read(files["Dymola/linux/ref_big.json"]) == big
        assert read(files["Dymola/linux/ref_empty.json"]) == b""
        assert read(files["Dymola/linux/ref_crlf.json"]) == b'{\r\n"a": "\xff"}\n'


def test_reading_never_writes_the_repository(repo):
    """``git diff`` refreshes the index of a stat-dirty file: nothing here
    may, hashing the disk included. The file is touched first, so a refresh would have something to
    write (a clean tree passes vacuously)."""
    ref = repo.top / "refs" / "Dymola" / "linux" / "ref_a.json"
    ref.write_text("{}")
    sha = repo.commit("first")
    st = ref.stat()
    os.utime(ref, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    before = _tree_hash(repo.top / ".git")
    git = Repository.holding(repo.top / "refs")
    commit = git.commit(sha)
    files = git.files(commit)
    with git.reader() as read:
        read(files["Dymola/linux/ref_a.json"])
    git.commit("HEAD~0")
    # what is on disk, hashed: the touched file, an edited one, a new one
    # and a link, through a folder's repository and its top's
    (repo.top / "refs" / "b.json").write_text("new")
    (repo.top / "refs" / "link").symlink_to("b.json")
    git.blobs_on_disk(["Dymola/linux/ref_a.json", "b.json", "link"])
    Repository.holding(repo.top).blobs_on_disk(["refs/Dymola/linux/ref_a.json"])
    assert _tree_hash(repo.top / ".git") == before
    # and the hash sees a write: `git diff` refreshes the touched file's entry
    repo.git("diff", "--quiet", "HEAD")
    assert _tree_hash(repo.top / ".git") != before


def test_a_revision_s_date_is_its_committer_date(repo, monkeypatch):
    """The date a plot's legend shows: when the commit was made on the
    branch (a rebased or cherry-picked commit keeps its author date)."""
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2025-01-02T10:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-04-30T23:30:00+00:00")
    (repo.top / "refs" / "a.txt").write_text("a")
    sha = repo.commit("first")
    git = Repository.holding(repo.top / "refs")
    assert git.date(git.commit(sha)) == "2026-04-30"


# --------------------------------------------------------------------------
# What is on disk now, as the blob git would store it
# --------------------------------------------------------------------------


@pytest.fixture
def lib(tmp_path):
    """A repository whose library is a SUBFOLDER (``src/Lib``), committed
    once: (the GitRepo, the folder, the commit)."""
    git = GitRepo(tmp_path / "work")
    folder = git.top / "src" / "Lib"
    (folder / "A").mkdir(parents=True)
    (folder / "package.mo").write_bytes(b"within ;\npackage Lib\nend Lib;\n")
    (folder / "A" / "package.mo").write_bytes(b"within Lib;\npackage A\nend A;\n")
    (folder / "A" / "M.mo").write_bytes(b"within Lib.A;\nmodel M\nend M;\n")
    (folder / "A" / "Gone.mo").write_bytes(b"within Lib.A;\nmodel Gone\nend Gone;\n")
    (git.top / "README.md").write_bytes(b"outside the folder\n")
    return git, folder, git.commit("first")


def _differing(git: Repository, rev: str, also: list[str]) -> dict[str, str]:
    """Each path that differs between ``rev`` and the disk: added /
    modified / deleted — what a caller makes of the two calls."""
    at_rev = git.files(git.commit(rev))
    now = git.blobs_on_disk([*at_rev, *also])
    out = {}
    for path in sorted({*at_rev, *now}):
        if path not in now:
            out[path] = "deleted"
        elif path not in at_rev:
            out[path] = "added"
        elif now[path] != at_rev[path]:
            out[path] = "modified"
    return out


def test_an_unchanged_checkout_is_the_blobs_of_its_revision(lib):
    _, folder, sha = lib
    git = Repository.holding(folder)
    files = git.files(git.commit(sha))
    assert sorted(files) == ["A/Gone.mo", "A/M.mo", "A/package.mo", "package.mo"]
    assert git.blobs_on_disk(files) == files


def test_modified_added_and_deleted_under_a_subfolder(lib):
    repo, folder, sha = lib
    (folder / "A" / "M.mo").write_bytes(b"within Lib.A;\nmodel M\n  Real x;\nend M;\n")
    (folder / "A" / "New.mo").write_bytes(b"within Lib.A;\nmodel New\nend New;\n")
    (folder / "A" / "Gone.mo").unlink()
    # outside the folder: not asked, not seen
    (repo.top / "README.md").write_bytes(b"changed, outside the folder\n")
    git = Repository.holding(folder)
    assert _differing(git, sha, ["A/New.mo"]) == {
        "A/Gone.mo": "deleted",
        "A/M.mo": "modified",
        "A/New.mo": "added",
    }
    # the id of a file on disk is the id git gives it
    assert git.blobs_on_disk(["A/New.mo"])["A/New.mo"] == repo.git(
        "hash-object", "src/Lib/A/New.mo"
    )


def test_a_path_that_is_no_file_is_left_out_and_the_rest_are_hashed(lib):
    """`hash-object --stdin-paths` stops at the first path it cannot read:
    one given before the others would lose every id after it."""
    _, folder, sha = lib
    git = Repository.holding(folder)
    files = git.files(git.commit(sha))
    got = git.blobs_on_disk(["A", "A/Nope.mo", "package.mo", "A/M.mo"])
    assert got == {"package.mo": files["package.mo"], "A/M.mo": files["A/M.mo"]}


@pytest.mark.parametrize(
    "name",
    [
        "My Pkg/S p.mo",
        "Ünï/Ü.mo",
        'A/"q".mo',
        '"Q/x.mo',
        "A/back\\slash.mo",
        "A/t\tab.mo",
        "A/new\nline.mo",
        "A/ends in cr\r",
    ],
)
def test_a_name_git_would_quote_is_hashed_as_it_is(lib, name):
    repo, folder, _ = lib
    (folder / name).parent.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"within Lib;\nmodel X\nend X;\n")
    sha = repo.commit("an odd name")
    git = Repository.holding(folder)
    files = git.files(git.commit(sha))
    assert name in files
    assert git.blobs_on_disk(files) == files


def test_a_crlf_checkout_of_a_file_stored_with_lf_is_unchanged(lib):
    """`core.autocrlf=true` (Git for Windows' default): the work tree holds
    CRLF, the blob LF. Hashing the bytes on disk would read every file as
    changed."""
    repo, folder, sha = lib
    repo.git("config", "core.autocrlf", "true")
    crlf = b"within Lib.A;\r\nmodel M\r\nend M;\r\n"
    (folder / "A" / "M.mo").write_bytes(crlf)
    git = Repository.holding(folder)
    files = git.files(git.commit(sha))
    assert git.blobs_on_disk(files) == files
    # not vacuous: the bytes as they are, are another blob
    assert repo.git("hash-object", "--no-filters", "src/Lib/A/M.mo") != files["A/M.mo"]
    # and an edit still shows through the conversion
    (folder / "A" / "M.mo").write_bytes(crlf.replace(b"model M", b"model M2"))
    assert git.blobs_on_disk(["A/M.mo"])["A/M.mo"] != files["A/M.mo"]


def test_a_symbolic_link_is_the_blob_of_its_target_s_name(lib):
    """git stores a link as its target's name: hashing what it points at
    would read an unchanged link as modified, and a link to a directory is
    no file to hash."""
    repo, folder, _ = lib
    (folder / "A" / "Link.mo").symlink_to("M.mo")
    (folder / "Res").symlink_to("A")
    sha = repo.commit("links")
    git = Repository.holding(folder)
    files = git.files(git.commit(sha))
    assert {"A/Link.mo", "Res"} <= set(files)
    assert git.blobs_on_disk(files) == files
    # a link pointed elsewhere is another blob, though the file it names is not
    (folder / "A" / "Link.mo").unlink()
    (folder / "A" / "Link.mo").symlink_to("./M.mo")
    assert git.blobs_on_disk(["A/Link.mo"])["A/Link.mo"] != files["A/Link.mo"]


def test_a_filter_driver_s_clean_command_is_run(lib, tmp_path):
    """The one boundary of "git is only read": a `filter` driver's clean
    command is the user's program, run as `git status` runs it (git-lfs
    keeps a copy of what it is given). It is run here too — without it, a
    file the driver cleans would read as changed."""
    repo, folder, _ = lib
    mark = tmp_path / "clean-ran"
    repo.git("config", "filter.stand.clean", f"touch '{mark}'; sed s/WORKING/STORED/")
    (folder / ".gitattributes").write_text("*.mo filter=stand\n")
    (folder / "A" / "M.mo").write_bytes(b"within Lib.A;\nmodel M // WORKING\nend M;\n")
    sha = repo.commit("a cleaned file")
    stored = repo.git("cat-file", "-p", f"{sha}:src/Lib/A/M.mo")
    assert "STORED" in stored and "WORKING" not in stored
    mark.unlink()
    git = Repository.holding(folder)
    files = git.files(git.commit(sha))
    assert git.blobs_on_disk(["A/M.mo"]) == {"A/M.mo": files["A/M.mo"]}
    assert mark.exists()


def test_git_s_location_variables_of_a_hook_are_not_read(lib, tmp_path, monkeypatch):
    """Inside a git hook, `GIT_DIR` (and `GIT_WORK_TREE`, `GIT_INDEX_FILE`)
    name the hook's repository: read here, every file would be another
    repository's, or none."""
    repo, folder, sha = lib
    other = GitRepo(tmp_path / "other")
    (other.top / "x.txt").write_text("x")
    other.commit("another repository")
    monkeypatch.setenv("GIT_DIR", str(other.top / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other.top))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other.top / ".git" / "index"))
    git = Repository.holding(folder)
    assert git.top == repo.top
    files = git.files(git.commit(sha))
    assert sorted(files) == ["A/Gone.mo", "A/M.mo", "A/package.mo", "package.mo"]
    assert git.blobs_on_disk(files) == files


def test_a_file_git_cannot_read_is_an_error_not_a_shorter_answer(lib):
    """One file it cannot open ends `hash-object`: the ids it gave are not
    an answer for every file."""
    _, folder, sha = lib
    locked = folder / "A" / "M.mo"
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("this user reads any file")
        git = Repository.holding(folder)
        with pytest.raises(GitError, match="^cannot hash the files of src/Lib in "):
            git.blobs_on_disk(["package.mo", "A/M.mo", "A/package.mo"])
    finally:
        locked.chmod(0o644)
