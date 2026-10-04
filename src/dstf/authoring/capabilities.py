"""The capability contract (two-tier design, §4.6).

A capability is a plug-in the authoring shell draws at the placements it
names: it reads a :class:`SessionView`, builds its controls in a
:class:`Panel` the shell provides, and returns an :class:`Outcome`. **The Session applies outcomes;
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
    from ..comparison.types import TestComparison
    from ..storage.reference_store import ReferenceStore
    from .session import Project, SessionView
    from .spec_document import PatchOp


class Placement(Enum):
    """Where a capability attaches (brief §3.2; D129): the Suite page's
    batch bar, the Test page's status strip, a plot card's criteria header,
    the open criterion's editor, a tree node's place in the tree, a row of
    the Statistics card and a statistics criterion's editor there. A capability
    names the frozenset it attaches at; the shell draws it at each and knows
    no capability by name. The tab strip this replaces listed one tab per
    ``Scope``; the scopes' selection rules (TESTS needs a selection, LEAF a
    leaf) are now what ``available`` answers for the page's selection.

    SUITE and TEST are a button carrying the capability's ``caption``, its
    panel in a dialog; VARIABLE, CRITERION, NODE and STATISTIC are inline —
    the panel is built straight into the plot's criteria header (for the
    plot's first tracked variable, its rows opening under the header), the
    criterion's editor, beside a tree node as that node's controls, or at
    the end of a Statistics row no criterion reads yet (the selection's
    ``quantity`` is the row's), with no button and no heading.
    STATISTIC_CRITERION is the editor of a criterion scored through a
    statistic, under its Statistics row: CRITERION's with no plot, so a
    capability that needs one is not offered there. TREE_CRITERION is the
    editor of any criterion opened under its row in the criteria tree
    (U5): CRITERION's with no plot, like STATISTIC_CRITERION — the plot's
    own editor is a link away. At VARIABLE and
    CRITERION ``Panel.figure`` is the plot's hook, subscribed for as long as
    the panel is drawn; elsewhere a subscription raises."""

    SUITE = "suite"
    TEST = "test"
    VARIABLE = "variable"
    CRITERION = "criterion"
    NODE = "node"
    STATISTIC = "statistic"
    STATISTIC_CRITERION = "statistic-criterion"
    TREE_CRITERION = "tree-criterion"


class Emphasis(Enum):
    """How a bar that is told the page's situation draws a capability
    (``Capability.emphasis``): NEXT is the one next action, drawn filled;
    MENU goes into the bar's ⋮ menu. A situation a capability does not
    name draws it quiet."""

    NEXT = "next"
    MENU = "menu"


def plural(count: int, noun: str, many: str | None = None) -> str:
    """``"2 tests"``, ``"1 test"``: the count and its noun for a caption. A
    regular plural adds ``s``; ``many`` spells one that does not
    (``plural(2, "new entry", "new entries")``)."""
    if count == 1:
        return f"{count} {noun}"
    return f"{count} {many}" if many is not None else f"{count} {noun}s"


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
    ``models`` names what it touches, so a locked model can refuse it.

    ``run`` returns the one line the job panel shows — what the write did,
    including what it refused and why — or ``None`` when there is nothing
    to say. A partial write has no other way to report its skips, and
    raising after some baselines were stored would mislead."""

    run: Callable[[ReferenceStore], str | None]
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
    #: Called once the reload has rescored, with the project and a copy of
    #: the Session's comparisons, so a job that wrote a run can finish its
    #: artifacts with the verdicts (a rerun's dashboard). OFF the UI loop,
    #: on the Session's background thread, in order with its own dashboard
    #: renders: the job reads as finished before it has run. Not called for
    #: a failed job or one that reloads nothing; one that raises is logged
    #: and the job stays good.
    after_reload: Callable[[Project, dict[str, TestComparison]], None] | None = None


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
    """ONE plot card's figure, as a capability may subscribe to it. There is
    a hook per card — the plot a VARIABLE panel (its criteria header) or a
    CRITERION editor sits under — not one figure for the page.

    ``on(kind, handler)`` calls ``handler(primitive_id, payload)``:

    - ``plotly_click`` / ``plotly_selected``: the id of the first point whose
      trace ``meta.kind`` is ``"handle"``, else of the first point
      (``customdata``, then ``meta.id``, then ``curveNumber``), with the event
      payload — under x-unified hover a click lists every trace at that x.
    - ``plotly_relayout`` moving shapes: once PER SHAPE, the payload being
      that shape's keys without their ``shapes[N].`` prefix
      (``{"y0": 6.0, "y1": 6.0}`` for a line; ``{"xanchor": 4.0, "yanchor":
      2.99, "path": "M …"}`` for a ring), in data coordinates.
      Plotly's payload never names the shape; the shell resolves the id from
      the rendered figure's ``layout.shapes[N].name``, ``""`` when it cannot.
      The shell does not interpret positions: the capability does, from the
      handle's primitive type (a Marker's ring moves only by
      ``xanchor``/``yanchor``).
      A relayout mixing shape keys with others is a shape relayout.
    - Any other relayout (a zoom): once, with id ``""`` and the raw payload.

    Handler semantics: each shape index is dispatched in turn, ascending,
    from its own snapshot of the handlers, taken when that shape's dispatch
    starts. A handler registered since the snapshot is not called for that
    shape; one removed since the snapshot (its panel rebuilt) is skipped,
    checked by identity. A handler registered twice is called twice. A
    handler that raises ENDS the relayout: later handlers and later shapes
    are not dispatched, the exception propagates, and the shell still
    re-sends the figure so the plot snaps back."""

    def on(self, kind: str, handler: Callable[[str, dict], None]) -> None: ...


class Panel(Protocol):
    """A container the capability owns entirely (the shell's
    ``ShellPanel``). ``figure`` is the plot the panel is drawn beside, as a
    capability subscribes to it — what Edit on plot reads its drags from."""

    figure: FigureHook

    def submit(self, **inputs: Any) -> bool:
        """Hand the capability's inputs to the shell, which calls ``act`` and
        applies the outcome; an exception shows with the panel — above an
        inline one, in the dialog of a placement that is a button. Returns
        whether it went through: ``False`` when ``act`` or the apply
        raised. A form row closes on ``True`` and only then — refused, it
        stays open with what was typed, and the refusal shows under the
        node's row, above the form."""
        ...

    def row(self, title: str, build: Callable[[Callable[[], None]], None]) -> None:
        """Open a form row below the row this panel is drawn in, titled
        ``title``. ``build(close)`` draws its content — fields, a sentence,
        its buttons; ``close`` takes the row away — after a ``submit`` that
        went through, never before it (a refused act keeps the row and
        what was typed in it). ONE thing is open at a time on a page: this
        closes any other row and the criterion editor. While it is open,
        ``build`` is called AGAIN, into an emptied row, when an edit staged
        elsewhere moves the node the panel is for — a form never shows a
        value the node no longer has — and it closes with the host, when
        its row is hidden, and when its node is gone. In a dialog (a
        placement that is a button) the form is drawn in the dialog, below
        the panel. A panel with no row to open one under — the criterion
        editor — raises."""
        ...

    def lead(self, build: Callable[[], None]) -> None:
        """Draw ``build()`` in the node's lead place — beside its status,
        where its kind is read (a combinator's kind dropdown). The place is
        emptied first, and with the panel; while it holds something the
        row's text leaves the kind out. A row without one (a leaf's, a
        criteria row's) raises, like ``row`` where there is no row."""
        ...

    def reveal(self, leaf_path: str) -> None:
        """Show that criterion's editor once the page has drawn it — after
        an add, the new criterion. A no-op where no page reveals criteria:
        a dialog, the criterion editor."""
        ...

    @property
    def can_move(self) -> bool:
        """Whether the host offers moving the node this panel is drawn for
        (U4, V2): a tree card's row, and nowhere else — a plot's criteria
        row hosts the same NODE placement and moves nothing."""
        ...

    def movable(
        self,
        on_drop: Callable[[str, str], None],
        zones: Callable[[], dict[str, dict[str, str | None]]] | None = None,
    ) -> bool:
        """Take part in the host's drag (U4) where it offers moving
        (`can_move`); elsewhere register nothing and return False.
        ``on_drop(src_path, zone)`` is called when a node is dropped on
        this panel's row — ``zone`` is ``before`` (the row's top half) or
        ``onto`` (its bottom half; the root's whole row). ``zones`` makes
        the row draggable: called once at the start of a drag from it, it
        answers ``{row path: {zone: reason}}`` — ``None`` allowed, ``""``
        where the node already is, else the sentence refusing it — which
        the page draws over every row while dragging, with no round trip
        to the server."""
        ...

    def on_click(self, handler: Callable[[], None]) -> bool:
        """What a click on the row this panel is drawn in does (U5, Q6):
        ``handler()``, called when the row itself is clicked — or, from the
        keyboard, Enter or Space on it — never a control inside it. Offered
        by the tree card's GROUP rows (a combinator's) and nowhere else
        (`can_move`'s rule): a criterion's row — a plot's criteria row or
        the tree's — has a click of its own, its editor. Elsewhere it
        registers nothing and returns False. One per row; a later call
        replaces it, and it goes with the panel."""
        ...

    def moved(self, node_path: str, text: str, paths: dict[str, str]) -> None:
        """A move just went through (U4, V4): every open editor and form row
        on the page closes NOW — two criteria on one variable trading
        places move no shape, and an editor left open would show the other
        one — and once the tree is redrawn ``node_path``'s row is brought
        into view with ``text`` as one line under it, until the next form,
        editor or edit. ``paths`` is every node the move kept, its path
        before -> after (`Moved.paths`): what the page keys by a node's
        path follows it, as for `edited`. A no-op where no page shows the
        tree."""
        ...

    def edited(self, paths: dict[str, str]) -> None:
        """Any other edit of the tree just went through: ``paths`` is every
        node it kept, its path before -> after (`tree_move.path_map`) — an
        add or a remove shifts the siblings after it, a wrap pushes its node
        one level down, an unwrap lifts its children. What the page keys by
        a node's path follows it (the tree card's folded groups, U5 R1);
        a node not in it is gone. A no-op where no page shows the tree."""
        ...


@runtime_checkable
class Capability(Protocol):
    """A plug-in the shell draws at its placements. It MAY also declare
    ``icon`` (a Material icon name): a bar that draws icons (the Suite
    toolbar) draws it as that icon, its caption the tooltip; one without
    keeps its text button. Not a protocol member — a runtime-checkable
    protocol requires every member, and most capabilities have no icon —
    so the shell reads it with ``getattr``.

    It MAY also declare ``emphasis``, read the same way: a mapping from a
    :class:`~dstf.authoring.situation.Situation` to :attr:`Emphasis.NEXT`
    — drawn filled, the page's one next action — or :attr:`Emphasis.MENU`
    — drawn in the bar's ⋮ menu; a situation it does not name draws it
    quiet. Only a bar the page tells its situation reads it (the Test
    page's status strip); every other bar draws today's outlined button."""

    name: str
    label: str
    placements: frozenset[Placement]

    def available(self, s: SessionView) -> bool | str:
        """``True``, or ``False`` / the sentence saying why it is unavailable
        and what would enable it ("Accept needs a selection", "rerun is
        running on FakeLib.A"). The greyed button carries it where the
        placement draws one; at CRITERION, NODE, STATISTIC and
        STATISTIC_CRITERION it is shown in the panel's place."""
        ...

    def caption(self, s: SessionView) -> str:
        """The button's label: its object, its effect and a count
        ("Rerun 2 selected tests"). At zero it still says what it
        would do; ``available`` then carries the sentence. The inline
        placements (CRITERION, NODE, STATISTIC, STATISTIC_CRITERION) draw
        no button and never read it."""
        ...

    def panel(self, s: SessionView, ui: Panel) -> None: ...

    def act(self, s: SessionView, **inputs: Any) -> Outcome | Sequence[Outcome]:
        """What this capability's inputs DO, as an outcome the Session
        applies — a Patch, a Job, a StoreWrite, an Artifact, an OpenProject,
        or a sequence of them.

        Returning ``None`` means nothing happened: the host applies
        nothing, leaves a dialog open (there is nothing to close over) and
        rebuilds no panel. It is a real answer — every plot sends
        ``{"autosize": true}`` at mount, and a subscribing panel answers
        that with None — but it is a silent one, so a capability that
        returns None on purpose says why in its own panel."""
        ...


_CAPABILITIES: dict[str, Capability] = {}


def register_capability(cap: Capability) -> Capability:
    """Register ``cap`` under its name; returns it. Loud on a duplicate —
    two capabilities sharing a name would make the shell's button order
    depend on import order — and on a shape the shell cannot draw."""
    if not isinstance(cap, Capability):
        missing = [
            a
            for a in (
                "name",
                "label",
                "placements",
                "available",
                "caption",
                "panel",
                "act",
            )
            if not hasattr(cap, a)
        ]
        raise TypeError(
            f"{cap!r} is not a Capability: missing {', '.join(missing) or 'a method'}"
        )
    placements = cap.placements
    if (
        not isinstance(placements, frozenset)
        or not placements
        or not all(isinstance(p, Placement) for p in placements)
    ):
        raise TypeError(
            f"capability {cap.name!r}: placements must be a non-empty frozenset of "
            f"Placement, got {placements!r}"
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
