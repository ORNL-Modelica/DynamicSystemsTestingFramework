"""Shared process-kill helpers for backends."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psutil


def kill_process_tree(
    pid: int,
    name_hint: str | None = None,
    create_time: float | None = None,
) -> None:
    """Kill *pid* and its entire child tree, children first.

    The tracked PID is never the process doing the simulating: dymola.exe
    spawns dymosim, omc spawns the compiled model binary. Killing only the
    parent leaves that child burning CPU and holding the result files open
    (on Windows the next run's rmtree then fails with PermissionError).

    Two independent guards against PID reuse, because a PID we recorded
    seconds ago may belong to something else by now — on Windows freed
    PIDs come back almost immediately:

    * ``name_hint`` — nothing is killed unless the process's name still
      contains the hint (``"dymola"``, ``"omc"``, ``"julia"``).
    * ``create_time`` — the exact process-creation timestamp recorded when
      the PID was captured. A recycled PID has a later one, and nothing is
      killed. Pass it whenever the PID was recorded earlier and the caller
      can't otherwise prove the process is the same one.

    Children resolved from the live tree in this call need neither guard —
    they are read from the process we just validated, and the hint would
    wrongly exclude exactly the children we're after (dymosim under
    dymola). Children recorded *earlier* do need both: see
    :func:`snapshot_children`, which records them with their create times
    for feeding back through this function.

    A parent that already exited takes its children out of reach here,
    which is what ``snapshot_children`` exists to cover.

    Best-effort throughout: psutil may be missing and processes may vanish
    or be inaccessible mid-walk.
    """
    try:
        import psutil
    except ImportError:  # pragma: no cover — psutil is a hard dep in practice
        return

    try:
        parent = psutil.Process(pid)
        if name_hint and name_hint not in (parent.name() or "").lower():
            return
        if create_time is not None and parent.create_time() != create_time:
            # PID reused since it was recorded — this is somebody else.
            return
        children = parent.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return
    except Exception:
        # A kill path must never raise: an unexpected psutil error here
        # would surface as a bogus "worker exception" in the dispatch
        # loop instead of the timeout that actually happened.
        return

    for proc in [*children, parent]:
        try:
            proc.kill()
        except Exception:
            pass


def snapshot_children(
    pid: int,
    name_hint: str | None = None,
) -> set[tuple[int, float]]:
    """Descendants of *pid* as ``(pid, create_time)`` pairs, recorded while
    the parent is still alive.

    Covers exactly one case: a graceful shutdown that succeeds. dymola.exe
    exits on ``close()`` while dymosim keeps running and keeps the result
    files open, and once the parent is gone nothing links the two, so
    ``kill_process_tree`` can no longer find the child. The worker
    therefore snapshots the tree immediately before its graceful attempt
    and kills the recorded processes afterwards. (A parent that died
    *before* ``close()`` was ever called has already orphaned its children
    beyond the reach of this snapshot.)

    The create times are what make the recorded PIDs safe to kill later:
    the graceful window is seconds long, and a PID freed inside it can be
    reissued to an unrelated process — or to another worker's fresh
    dymosim. ``name_hint`` guards the parent the same way
    :func:`kill_process_tree` does, so a recycled *parent* PID never hands
    us a stranger's children in the first place.

    Best-effort: an empty set on any failure.
    """
    try:
        import psutil
    except ImportError:  # pragma: no cover — psutil is a hard dep in practice
        return set()

    try:
        parent = psutil.Process(pid)
        if name_hint and name_hint not in (parent.name() or "").lower():
            return set()
        children = parent.children(recursive=True)
    except Exception:
        # Snapshotting sits on the close path; it must never raise there.
        return set()

    snapshot: set[tuple[int, float]] = set()
    for child in children:
        try:
            snapshot.add((child.pid, child.create_time()))
        except Exception:
            # Vanished or inaccessible between the walk and the read —
            # a PID without its create time is not safe to kill later.
            continue
    return snapshot


def orphans_in(
    directory: str, born_after: float | None, token: str | None = None
) -> set[tuple[int, float]]:
    """Linux only: the processes a dead tool left behind in *directory*, as
    ``(pid, create_time)`` pairs for :func:`kill_process_tree`.

    A tool that dies by itself orphans its children, and a child nobody
    recorded while the tool was alive cannot be found through it any more.
    What it left is told from everything else on the machine by a mark of
    three parts, ALL required:

    * it runs in *directory* or below — the scratch directory of the one
      test the dead tool was running, which the tool was told to work in
      and no other worker uses;
    * it was created no earlier than *born_after*, the tool's own creation
      time on the same clock (a child cannot predate its parent);
    * it has no parent of its own left: it hangs under init, or under a
      reaper among this process's ancestors — where the kernel puts the
      orphans of our descendants. A child of this process is not one, and
      neither is a child of anybody else's live process.

    The third part is wider than it reads: the reapers include every
    ancestor of this process (WSL's init adopts orphans one level below
    PID 1), so a child of the shell dstf was started from, born after the
    tool and sitting in the folder, carries the mark too. With *token* —
    the value of :data:`TREE_TOKEN` in the environment the tool was started
    with, which every process it starts inherits — the mark is exact
    instead: a process in the folder whose environment holds the token.
    Whether it was reparented yet does not matter then, nor when it was
    born — only the tool's descendants can carry the token — so a token's
    sweep passes *born_after* ``None``: a wall-clock birth read at the
    launch is moved by every step of the clock since (``create_time``
    adds the boot time, read afresh), and would spare a leftover.

    Unreadable means unmarked: a process whose directory, parent or
    environment cannot be read is left alone. Other platforms report no
    working directory for a foreign process without privileges, or do not
    reparent: ``set()``.
    """
    if not sys.platform.startswith("linux"):
        return set()
    try:
        import psutil

        reapers = {1} | {parent.pid for parent in psutil.Process().parents()}
        root = os.path.realpath(directory)
    except Exception:
        return set()
    found: set[tuple[int, float]] = set()
    for proc in psutil.process_iter():
        try:
            if proc.pid in reapers | {os.getpid()}:
                continue
            if token is None and proc.ppid() not in reapers:
                continue
            created = proc.create_time()
            if born_after is not None and created < born_after:
                continue
            if proc.status() == psutil.STATUS_ZOMBIE:
                continue
            cwd = proc.cwd()
            if not (cwd == root or cwd.startswith(root + os.sep)):
                continue
            if token is not None and proc.environ().get(TREE_TOKEN) != token:
                continue
            found.add((proc.pid, created))
        except Exception:
            continue
    return found


def created_at(pids: set[int]) -> float | None:
    """When the earliest of *pids* was created (the clock of
    :func:`orphans_in`), or ``None`` when that cannot be read."""
    try:
        import psutil

        return min(psutil.Process(pid).create_time() for pid in pids)
    except Exception:
        return None


def process_gone(pids: set[int], name_hint: str | None = None) -> bool:
    """True when *pids* names at least one process and none of them is
    running any more — exited, or a zombie nobody has reaped yet. With
    ``name_hint``, a PID that is no longer the tool's is not running
    either: the process under it has another name (the guard of
    :func:`kill_process_tree`, for a PID recorded long ago).

    How a worker tells "the tool died" from "the tool is slow". Errs
    towards alive: no PIDs, no psutil, or a process it cannot inspect all
    read as not gone, since the answer triggers a kill and a restart.
    """
    if not pids:
        return False
    try:
        import psutil

        for pid in list(pids):
            try:
                proc = psutil.Process(pid)
                if proc.status() == psutil.STATUS_ZOMBIE:
                    continue
                if name_hint and name_hint not in (proc.name() or "").lower():
                    continue
                return False
            except psutil.NoSuchProcess:
                continue
        return True
    except Exception:
        return False


def live_children(pids: set[int], name_hint: str | None = None) -> list:
    """Every descendant of *pids* alive right now, as ``psutil.Process``
    objects — what a backend's soft cancel looks through for the process
    doing the simulating. A zombie is not alive: a child killed a moment
    ago stays in the table until its parent reaps it. ``name_hint`` guards
    each of *pids* as :func:`kill_process_tree` does: a PID whose process
    is no longer the tool (recycled) hands over nobody's children — what
    comes back is killed, or told to stop. Best-effort: ``[]`` on any
    failure."""
    try:
        import psutil
    except ImportError:  # pragma: no cover — psutil is a hard dep in practice
        return []
    children: list = []
    for pid in list(pids):
        try:
            parent = psutil.Process(pid)
            if name_hint and name_hint not in (parent.name() or "").lower():
                continue
            for child in parent.children(recursive=True):
                if child.status() != psutil.STATUS_ZOMBIE:
                    children.append(child)
        except Exception:
            continue
    return children


#: The environment variable that marks every process one test's tool
#: started: ``run_killable`` sets it to a value of its own for each launch,
#: and the processes inherit it.
TREE_TOKEN = "DSTF_TREE"


def run_killable(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float,
    on_spawn: Callable[[subprocess.Popen, psutil.Process | None], None] | None = None,
) -> subprocess.CompletedProcess:
    """``subprocess.run(cmd, capture_output=True, text=True, timeout=...)``
    as every subprocess-per-test launch is made, utf-8 with
    ``errors="replace"``. *on_spawn* gets the live ``Popen`` and the tool
    as psutil saw it at the launch (``None`` if unreadable), so another
    thread can stop it with :func:`kill_tree`.

    Same contract otherwise, except what a timeout stops: the whole tree
    the tool started (:func:`_stop_tree`), not the tool alone. omc runs the
    compiled simulation under ``/bin/sh -c``; ``subprocess.run`` killed
    only omc, and on a 340-test run six simulations were still running 30
    minutes after the run ended. ``TimeoutExpired`` carries what the tool
    had written. A tool that ends by a signal — a cancel's
    :func:`kill_tree`, or anyone's kill — has its folder swept the same
    way before the result returns.

    *cwd* is the one test's own directory, used by no other test: on Linux
    what the tree left there is found by it and by the environment mark
    (:data:`TREE_TOKEN`) the tool is started with.
    """
    token = uuid.uuid4().hex
    with subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Pin utf-8 + replace so a non-ASCII traceback can't crash decoding
        # on cp1252 hosts.
        encoding="utf-8",
        errors="replace",
        env={**os.environ, TREE_TOKEN: token},
    ) as proc:
        tool = _process(proc.pid)
        if on_spawn is not None:
            on_spawn(proc, tool)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _stop_tree(proc, tool, cwd, token)
            exc.stdout, exc.stderr = _drain(proc)
            raise
        except BaseException:
            _stop_tree(proc, tool, cwd, token)
            raise
        if proc.returncode < 0:
            _sweep(cwd, token)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _process(pid: int) -> psutil.Process | None:
    """*pid* as psutil sees it now, or ``None`` (gone, unreadable, no
    psutil)."""
    try:
        import psutil

        return psutil.Process(pid)
    except Exception:
        return None


def kill_tree(proc: subprocess.Popen, tool: psutil.Process | None) -> None:
    """Kill *proc* and every process under it, children first, without
    waiting for any of them. Safe from another thread, also once the
    ``Popen`` was reaped and its PID reissued.

    *tool* is *proc* as psutil saw it at the launch, and every kill goes
    through such an object: psutil tells a process by its PID and its
    start in clock ticks since boot (on Linux; ``Process.__eq__``, which
    ``is_running`` and each ``kill`` check first), which no step of the
    wall clock moves. A wall-clock creation time read at the launch would
    not do: psutil's ``create_time`` adds the boot time, read afresh, and
    after a step of the clock the tool no longer matched it and nothing
    was killed. A reissued PID is not *tool* running, and hands over no
    children. The descendants are walked while the tool still lives (once
    it is gone nothing links them to it) and killed through the objects
    the walk returned. With *tool* ``None`` only the ``Popen`` itself is
    killed, a no-op once it was reaped.
    """
    if tool is not None:
        try:
            children = tool.children(recursive=True) if tool.is_running() else []
        except Exception:
            children = []
        for process in [*children, tool]:
            try:
                process.kill()
            except Exception:
                pass
    try:
        # The tree kill is a no-op without psutil; the Popen is ours.
        proc.kill()
    except OSError:
        pass


#: How long :func:`_stop_tree` waits for the tool to exit, and
#: :func:`_drain` for the pipes to close, before going on without them.
_STOP_GRACE = 5.0


def _stop_tree(
    proc: subprocess.Popen, tool: psutil.Process | None, cwd: Path, token: str
) -> None:
    """Kill *proc* and every process it started (:func:`kill_tree`), wait
    for the tool, then sweep the test's directory (:func:`_sweep`).

    A ``Popen`` nobody has reaped still holds its PID, whatever the clock
    says: the tool is then taken afresh, so the walk needs no identity
    from the launch. One a cancel's kill reaped meanwhile is walked through
    *tool*."""
    if proc.returncode is None:
        tool = _process(proc.pid) or tool
    kill_tree(proc, tool)
    try:
        proc.wait(timeout=_STOP_GRACE)
    except subprocess.TimeoutExpired:
        pass
    _sweep(cwd, token)


def _sweep(cwd: Path, token: str) -> None:
    """Kill what the tool's tree left in *cwd* (the test's own directory):
    a child forked between the walk and the kills, whose parent the kills
    then took, is out of every walk's reach. It is told by
    :func:`orphans_in` with the launch's *token*: in the folder and
    carrying the mark — a person's shell in the same folder carries
    none."""
    for pid, created in orphans_in(str(cwd), None, token=token):
        kill_process_tree(pid, create_time=created)


def _drain(proc: subprocess.Popen) -> tuple[str | bytes | None, str | bytes | None]:
    """What *proc* wrote, read to the end once its tree is stopped. A
    process that escaped the stop (a new session, another directory) and
    still holds the pipes would keep the read waiting for it to end: past
    :data:`_STOP_GRACE` the pipes are closed with what had arrived, as
    bytes (``decode_output`` reads both).

    The bound holds on POSIX. On Windows ``communicate`` reads through
    threads, and closing a pipe a reader thread is blocked on can wait
    until the process holding its other end exits.
    """
    try:
        return proc.communicate(timeout=_STOP_GRACE)
    except subprocess.TimeoutExpired as partial:
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        proc.wait()
        return partial.stdout, partial.stderr
