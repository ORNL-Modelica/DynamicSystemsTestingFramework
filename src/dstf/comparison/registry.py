"""Registries for comparison metrics and MetricTree combinators.

The extension seam docs/extensibility.md promises for the Metric layer.
Before this module the metric and combinator name spaces were closed
if/elif chains duplicated across four files (``modes.resolve_mode``,
``tree_spec.VALID_METRICS`` / ``_MODE_TO_METRIC``,
``tree_eval._build_combinator`` / ``_METRIC_TO_MODE_KEY``,
``reporting/schema_export._MODES``) — adding a metric meant editing all
of them. Now each of those derives from the descriptors registered here,
so a third-party metric only has to call :func:`register_metric`.

Builtins register in :mod:`dstf.comparison.builtins`, imported by the
package ``__init__`` — mirroring how ``backends`` self-registers its
backends.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..backends.types import DatasetType
from .sides import Sides
from .summaries import summarize_default
from .types import ScoreSummary, VariableComparison

if TYPE_CHECKING:
    from .handles import HandleEdit, RowPlace
    from .metric_tree import Combinator
    from .modes import ComparisonMode
    from .seeds import SeedData
    from .tree_spec import CombinatorSpec

#: The metric a leaf/override falls back to when none is named. Also the
#: one metric whose name is *absent* from the flat override dict (there is
#: no ``{"mode": "nrmse"}`` in stored specs — no key means NRMSE).
DEFAULT_METRIC = "nrmse"

#: The metric a flat override with no ``mode`` gets under ``--default-points``
#: instead (``modes.resolve_mode``).
DEFAULT_POINTS_METRIC = "points"


def unnamed_metrics() -> tuple[str, str]:
    """The metrics an override that names no ``mode`` may be scored by: the
    default, or ``points`` under ``--default-points``. What such an override
    may carry is what either reads."""
    return (DEFAULT_METRIC, DEFAULT_POINTS_METRIC)


#: Default ``accepted_datasets`` — every backend produces time series today.
TIME_SERIES_ONLY = frozenset({DatasetType.TIME_SERIES})


@dataclass(frozen=True)
class MetricDescriptor:
    """Everything the framework knows of one comparison mode: what
    :func:`register_metric` registers.

    Only ``name``, ``config_cls``, ``build`` and ``description`` are needed of
    a plug-in; the other fields are for what the bundled modes do beyond
    scoring — dragging a handle on a plot, seeding a new criterion — and have
    defaults that leave those features off.

    Attributes:
        name: The name a criterion's ``metric`` gives (``"tube"``).
        config_cls: The mode's settings, as a frozen dataclass; its fields are
            the keys a criterion may hold, described by their metadata
            (``label``, ``help``, ``columns`` and ``column_help`` for a table
            of rows).
        build: Makes the mode for one criterion: called with the criterion's
            keys as written and its tolerance, already resolved to a
            :class:`~dstf.comparison.sides.Sides`; returns a
            :class:`~dstf.comparison.modes.ComparisonMode`. Raises
            :class:`ValueError` for settings that cannot be scored, which fails
            that criterion with the message.
        description: What the mode scores and when to use it, in one paragraph
            for the person writing a criterion.
        accepted_datasets: The kinds of result data the mode can score.
        score_direction: ``"lower"`` when a smaller score is better (an error),
            ``"higher"`` when a larger one is (a coverage).
        accepts_quantity: Whether a string ``quantity`` names the data the
            mode scores, in place of a variable.
        summarize: The verdict in words: the short text and the sentence the
            report shows.
        edit_handle: For a mode the authoring tool edits by dragging on the
            plot: the criterion after a drag.
        drawn_by_default: Whether the authoring tool draws a passing criterion
            of the mode before it is asked to.
        seed: A new criterion's first settings, from the data it will score.
        place_row: The criterion with one more row, where a click on the plot
            adds one.
        row_noun: What such a row is called (``"a point"``).
    """

    name: str
    config_cls: type
    build: Callable[[dict, Sides], ComparisonMode]
    #: What the metric scores and when to use it, in one paragraph for the
    #: person writing a criterion: the reference page and the published
    #: schema print it. Its fields are described by their own ``help``.
    description: str = ""
    accepted_datasets: frozenset[DatasetType] = TIME_SERIES_ONLY
    score_direction: Literal["lower", "higher"] = "lower"
    accepts_quantity: bool = False
    #: One leaf's verdict in words. The default is NRMSE-shaped; every
    #: builtin supplies its own in :mod:`.builtins`.
    summarize: Callable[[VariableComparison], ScoreSummary] = summarize_default
    #: A drag on one of the mode's handles, as the new raw leaf.
    edit_handle: Callable[[HandleEdit], dict] | None = None
    #: Whether a closed criterion of this metric that is not failing draws on
    #: the Test page until its swatch says otherwise (legend b). Off for
    #: metrics whose drawing is a bound across the whole plot — range, scalar
    #: — which would otherwise cover every plot they sit on. The open
    #: criterion always draws, and a failing one draws by default.
    drawn_by_default: bool = True
    #: A new leaf's first values from the data it will score.
    seed: Callable[[SeedData], dict] | None = None
    #: A Ctrl+click on the plot, as the leaf with one more row.
    place_row: Callable[[RowPlace], dict] | None = None
    #: What that row is called for a leaf, or None where a click adds none.
    row_noun: Callable[[dict], str | None] | None = None


@dataclass(frozen=True)
class CombinatorDescriptor:
    """One kind of group of a criteria tree: what :func:`register_combinator`
    registers.

    Attributes:
        name: The name a group's ``combinator`` gives (``"k-of-n"``).
        build: Makes the group's combinator from the parsed group, whose
            fields (``k``, ``weights``, ...) the parser has already checked.
        description: When the group passes, in one paragraph.
    """

    name: str
    build: Callable[[CombinatorSpec], Combinator]
    #: When the group passes, in one paragraph for the person writing a
    #: tree: the reference page prints it.
    description: str = ""


# Insertion-ordered: registration order is the canonical presentation order
# for the schema export, the reporter's mode-schema map, and the "valid: ..."
# list in resolve_mode's error.
_METRICS: dict[str, MetricDescriptor] = {}
_COMBINATORS: dict[str, CombinatorDescriptor] = {}


def register_metric(desc: MetricDescriptor) -> MetricDescriptor:
    """Register a comparison mode under its name.

    A mode plug-in calls it when its module is imported. The mode is then a
    ``metric`` a criterion may name, and a ``mode`` an override may.

    Args:
        desc: The mode's descriptor.

    Returns:
        ``desc``, unchanged.

    Raises:
        ValueError: A mode is already registered under that name.
    """
    if desc.name in _METRICS:
        raise ValueError(f"metric {desc.name!r} is already registered")
    _METRICS[desc.name] = desc
    return desc


def register_combinator(desc: CombinatorDescriptor) -> CombinatorDescriptor:
    """Register a kind of group under its name.

    Args:
        desc: The group's descriptor.

    Returns:
        ``desc``, unchanged.

    Raises:
        ValueError: A group is already registered under that name.
    """
    if desc.name in _COMBINATORS:
        raise ValueError(f"combinator {desc.name!r} is already registered")
    _COMBINATORS[desc.name] = desc
    return desc


def metric(name: str) -> MetricDescriptor | None:
    """Return the descriptor for ``name``, or None if not registered."""
    return _METRICS.get(name)


def combinator(name: str) -> CombinatorDescriptor | None:
    """Return the descriptor for ``name``, or None if not registered."""
    return _COMBINATORS.get(name)


def metric_names() -> frozenset[str]:
    return frozenset(_METRICS)


def combinator_names() -> frozenset[str]:
    return frozenset(_COMBINATORS)


def metrics() -> tuple[MetricDescriptor, ...]:
    """All metric descriptors in registration order."""
    return tuple(_METRICS.values())


def summarize(vc: VariableComparison) -> ScoreSummary:
    """The pill text and criterion for one leaf, by its mode's descriptor.

    Any sentinel carrying a reason — a leaf config that could not build, an
    empty-data failure — shows that reason instead of a numeric score there
    is none of. Mode-agnostic on purpose: the flat variable_overrides path
    emits the same shape. Uses ``vc.mode`` raw: the unknown-mode sentinel
    has no mode, and "nrmse leaf" would name the wrong metric.
    """
    error = (vc.diagnostics or {}).get("error")
    if error:
        label = f"{vc.mode} leaf" if vc.mode else "leaf"
        return ScoreSummary(
            display="not scored", criterion=f"{label} not scored — {error}"
        )
    # A mode nothing is registered under — none at all, or a plug-in's that
    # is not loaded — reads as the default summary does.
    desc = metric(vc.mode)
    fn = desc.summarize if desc is not None else summarize_default
    return fn(vc)


def combinators() -> tuple[CombinatorDescriptor, ...]:
    """All combinator descriptors in registration order."""
    return tuple(_COMBINATORS.values())


def _reset_for_tests() -> None:
    """Drop user registrations and restore the builtins — unit tests only."""
    _METRICS.clear()
    _COMBINATORS.clear()

    from .builtins import register_builtins

    register_builtins()
