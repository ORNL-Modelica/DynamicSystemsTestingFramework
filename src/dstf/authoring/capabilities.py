"""The capability contract (two-tier design, §4.6).

A capability is a plug-in the authoring shell lists by scope: it reads a
:class:`SessionView`, builds its controls in a :class:`Panel` the shell
provides, and returns an :class:`Outcome`. **The Session applies outcomes;
a capability applies nothing.** It holds a view only, so the enforcement
is structural. Registration goes through the single ``dstf.plugins``
entry-point group: a plug-in module calls :func:`register_capability` on
import exactly as it calls ``register_metric``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..storage.reference_store import ReferenceStore
    from .session import Project, SessionView
    from .spec_document import PatchOp


class Scope(Enum):
    PROJECT = "project"
    TESTS = "tests"
    VARIABLE = "variable"
    LEAF = "leaf"


@dataclass(frozen=True)
class JobContext:
    """What a job's callable receives: the open project and the models the
    job locked. Sub-project 5's run callable reads its config and tests
    from here."""

    project: Project
    models: frozenset[str]


@dataclass(frozen=True, kw_only=True)
class Patch:
    """Ops to stage; the Session rescores their models."""

    ops: list[PatchOp]


@dataclass(frozen=True, kw_only=True)
class StoreWrite:
    """A write against the reference store; results reload afterwards.
    ``models`` names what it touches, so a locked model can refuse it."""

    run: Callable[[ReferenceStore], None]
    models: frozenset[str]


@dataclass(frozen=True, kw_only=True)
class Job:
    """Work on a worker thread. The Session locks ``models`` while it runs
    and, unless ``reloads_results`` is false, reloads results and rescores
    them when it completes."""

    run: Callable[[JobContext], None]
    label: str
    models: frozenset[str]
    reloads_results: bool = True


@dataclass(frozen=True, kw_only=True)
class Artifact:
    """Something written outside the spec and the store; shown as a link."""

    paths: list[Path]
    label: str


@dataclass(frozen=True, kw_only=True)
class OpenProject:
    config_path: Path
    work_dir: Path | None = None  # the run's work dir, when not the default


Outcome = Patch | StoreWrite | Job | Artifact | OpenProject | None


class FigureHook(Protocol):
    """The shell's centre figure, as a capability may subscribe to it."""

    def on(self, kind: str, handler: Callable[[str, dict], None]) -> None: ...


class Panel(Protocol):
    """A container the capability owns entirely (sub-project 4 provides the
    NiceGUI one). ``figure`` is the hook on-plot editing will use first."""

    figure: FigureHook

    def submit(self, **inputs: Any) -> None:
        """Hand the capability's inputs to the shell, which calls ``act`` and
        applies the outcome; an exception shows in the tab."""
        ...


@runtime_checkable
class Capability(Protocol):
    name: str
    label: str
    scope: Scope

    def available(self, s: SessionView) -> bool | str:
        """``True``, or ``False`` / the reason it is greyed out."""
        ...

    def panel(self, s: SessionView, ui: Panel) -> None: ...

    def act(self, s: SessionView, **inputs: Any) -> Outcome | Sequence[Outcome]: ...


_CAPABILITIES: dict[str, Capability] = {}


def register_capability(cap: Capability) -> Capability:
    """Register ``cap`` under its name; returns it. Loud on a duplicate —
    two capabilities sharing a name would make the shell's tab list depend
    on import order."""
    if not isinstance(cap, Capability):
        missing = [
            a
            for a in ("name", "label", "scope", "available", "panel", "act")
            if not hasattr(cap, a)
        ]
        raise TypeError(
            f"{cap!r} is not a Capability: missing {', '.join(missing) or 'a method'}"
        )
    if cap.name in _CAPABILITIES:
        raise ValueError(f"capability {cap.name!r} is already registered")
    _CAPABILITIES[cap.name] = cap
    return cap


def registered_capabilities() -> tuple[Capability, ...]:
    """Every registered capability, in registration order."""
    return tuple(_CAPABILITIES.values())


def _reset_for_tests() -> None:
    _CAPABILITIES.clear()
