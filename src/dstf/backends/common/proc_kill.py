"""Shared process-kill helpers for backends."""

from __future__ import annotations

import os
import sys


def kill_process_tree(
    pid: int,
    name_hint: str | None = None,
    create_time: float | None = None,
) -> None:
    """Kill *pid* and its entire child tree, children first.

    The tracked PID is never the process doing the simulating: dymola.exe
    spawns dymosim, omc spawns the compiled model binary. Killing only the
    parent leaves that child burning CPU and holding the result files open
    (on Windows the next run's rmtree then fails with PermissionError)
    (review 2026-07-06, finding 22).

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


def orphans_in(directory: str, born_after: float) -> set[tuple[int, float]]:
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

    Unreadable means unmarked: a process whose directory or parent cannot
    be read is left alone. Other platforms report no working directory for
    a foreign process without privileges, or do not reparent: ``set()``.
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
            if proc.ppid() not in reapers or proc.pid in reapers | {os.getpid()}:
                continue
            created = proc.create_time()
            if created < born_after or proc.status() == psutil.STATUS_ZOMBIE:
                continue
            cwd = proc.cwd()
            if cwd == root or cwd.startswith(root + os.sep):
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
