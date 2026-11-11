"""The one place DSTF reads git (``storage.git_revisions``): a revision's
files under a folder, a blob's bytes, a commit's short hash and subject —
each failure one sentence, and nothing it runs writes the repository."""

import hashlib
import os
from pathlib import Path

import pytest

from conftest import GitRepo
from dstf.storage.git_revisions import GitError, Repository


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
    may. The file is touched first, so a refresh would have something to
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
    assert _tree_hash(repo.top / ".git") == before
    # and the hash sees a write: `git diff` refreshes the touched file's entry
    repo.git("diff", "--quiet", "HEAD")
    assert _tree_hash(repo.top / ".git") != before
