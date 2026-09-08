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
from .summaries import summarize_default
from .types import ScoreSummary, VariableComparison

if TYPE_CHECKING:
    from .metric_tree import Combinator
    from .modes import ComparisonMode
    from .tree_spec import CombinatorSpec

#: The metric a leaf/override falls back to when none is named. Also the
#: one metric whose name is *absent* from the flat override dict (there is
#: no ``{"mode": "nrmse"}`` in stored specs — no key means NRMSE).
DEFAULT_METRIC = "nrmse"

#: Default ``accepted_datasets`` — every backend produces time series today.
TIME_SERIES_ONLY = frozenset({DatasetType.TIME_SERIES})


@dataclass(frozen=True)
class MetricDescriptor:
    """Everything the framework needs to know about one leaf metric.

    ``build`` takes the flat override dict (the ``variable_overrides``
    payload shape, also used for a :class:`~.tree_spec.LeafSpec`'s params)
    plus the resolved tolerance, and returns a ready-to-score mode.

    ``score_direction`` says which way a better score runs: ``"lower"``
    for error-like scores (NRMSE, point violations) and ``"higher"`` for
    coverage-like ones (tube's in-band fraction). ``accepted_datasets``
    declares which :class:`~dstf.backends.types.DatasetType` kinds the
    metric can score — a leaf handed anything else hard-fails in
    :func:`~.tree_eval._evaluate_leaf`.

    ``accepts_quantity`` says the metric's ``quantity`` param names the
    data it scores. For such a metric — and ONLY for such a metric — a
    string ``quantity`` makes the evaluator resolve the value from the
    run's statistics / diagnostics instead of a tracked trajectory, and
    lets :func:`~.tree_spec._parse_leaf` default ``variable`` to the path.
    Off by default: a metric that scores trajectories must keep scoring
    trajectories, so an unrecognized ``quantity`` stays an inert param
    rather than silently swapping that metric's data source.

    ``summarize`` turns the mode's ``VariableComparison`` into the pill
    text and criterion sentence; see :mod:`.summaries`.
    """

    name: str
    config_cls: type
    build: Callable[[dict, float], ComparisonMode]
    accepted_datasets: frozenset[DatasetType] = TIME_SERIES_ONLY
    score_direction: Literal["lower", "higher"] = "lower"
    accepts_quantity: bool = False
    #: One leaf's verdict in words. The default is NRMSE-shaped; every
    #: builtin supplies its own in :mod:`.builtins`.
    summarize: Callable[[VariableComparison], ScoreSummary] = summarize_default


@dataclass(frozen=True)
class CombinatorDescriptor:
    """One MetricTree combinator: a name plus a spec → instance builder.

    ``build`` reads whatever combinator-specific fields it needs off the
    :class:`~.tree_spec.CombinatorSpec` (``k``, ``weights``, ...). The
    *shape* validation of those fields stays in ``tree_spec`` at parse
    time — this is dispatch only.
    """

    name: str
    build: Callable[[CombinatorSpec], Combinator]


# Insertion-ordered: registration order is the canonical presentation order
# for the schema export, the reporter's mode-schema map, and the "valid: ..."
# list in resolve_mode's error.
_METRICS: dict[str, MetricDescriptor] = {}
_COMBINATORS: dict[str, CombinatorDescriptor] = {}


def register_metric(desc: MetricDescriptor) -> MetricDescriptor:
    """Register a leaf metric. Raises on a duplicate name.

    Duplicates are loud rather than last-writer-wins: two metrics sharing
    a name would make spec parsing depend on import order.
    """
    if desc.name in _METRICS:
        raise ValueError(f"metric {desc.name!r} is already registered")
    _METRICS[desc.name] = desc
    return desc


def register_combinator(desc: CombinatorDescriptor) -> CombinatorDescriptor:
    """Register a MetricTree combinator. Raises on a duplicate name."""
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
    desc = metric(vc.mode or "nrmse")
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
