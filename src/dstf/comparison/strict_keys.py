"""A key nobody reads is an error that names it.

``test_spec.json`` is written by hand as well as by the tool, and a key
the scorer does not read used to be kept and ignored: a ``range`` leaf
spelled ``"max_val": 0.5`` scored as if it had no upper bound and passed;
an ``"agianst"`` scored against the primary baseline. This module is the
ONE check every part of a spec entry goes through — a leaf, a flat
override, the rows of a metric's tables, the keys inside a ``quantity``
object and a tolerance pair, and (through :func:`unknown_field`) a window,
a combinator, the entry's own blocks and the spec's root.

What is known is never listed here. It is read from what already says it:
a metric's config dataclass (its fields, and the ``columns`` metadata of
its row tables — what the authoring tool's forms are built from) and the
keys the parser and the evaluator read off every leaf. A metric a plug-in
registers is therefore checked against ITS config with no change here.
"""

from __future__ import annotations

import dataclasses
import difflib
from collections.abc import Iterable, Mapping

from .registry import metric, unnamed_metrics

#: Params read off EVERY leaf and override, whatever its metric.
#:
#: ``tolerance`` — the evaluator resolves it for each leaf before it builds
#: the mode (a leaf's own, else the test's), so it is a key of every metric
#: even where the mode then has no use for the number.
#:
#: ``quantity`` — the evaluator looks at it on each leaf, and acts on it
#: only for a metric that declares ``accepts_quantity``. On any other
#: metric it is the one documented inert param (docs/extensibility.md,
#: the Metric contract, item 6; ``MetricDescriptor``): "an unrecognized
#: ``quantity`` stays an inert param — a trajectory metric keeps scoring
#: trajectories". That promise is kept here, by name, and not by leniency.
EVERY_METRIC = ("tolerance", "quantity")


def unknown_field(keys: Iterable[str], known: Iterable[str]) -> str | None:
    """What to say about the first of ``keys`` that is not in ``known``, or
    None when all are: ``unknown field "max_val" — did you mean
    "max_value"?`` with the nearest known key when one is close — in
    spelling, or as the start of one (``min`` for ``min_value``) — else the
    keys there are to choose from."""
    names = sorted(set(known))
    for key in keys:
        if key in names:
            continue
        text = str(key)
        close = difflib.get_close_matches(text, names, n=1, cutoff=0.6) or [
            name for name in names if text and name.startswith(text)
        ]
        if close:
            return f'unknown field "{key}" — did you mean "{close[0]}"?'
        return f'unknown field "{key}"; the fields here are: {", ".join(names)}'
    return None


def metric_fields(metric_name: str) -> frozenset[str] | None:
    """The param keys a metric reads: its config dataclass's fields and
    :data:`EVERY_METRIC`. None — NOT CHECKED — for a name no metric is
    registered under (the parser and the flat path each say that
    themselves) and for a metric whose ``config_cls`` is not a dataclass:
    nothing says what such a metric reads, so nothing is refused for it.
    """
    desc = metric(metric_name)
    if desc is None or not dataclasses.is_dataclass(desc.config_cls):
        return None
    return frozenset(f.name for f in dataclasses.fields(desc.config_cls)) | frozenset(
        EVERY_METRIC
    )


def row_fields(metric_name: str) -> dict[str, frozenset[str]]:
    """``{table field: the keys of one of its rows}`` for a metric's row
    tables — ``points``, ``peaks``, ``events``, ``tube_points`` — from the
    ``columns`` metadata on the config field, which names each row key as
    the scorer reads it. A list field with no ``columns`` has no entry and
    its rows are not checked."""
    desc = metric(metric_name)
    if desc is None or not dataclasses.is_dataclass(desc.config_cls):
        return {}
    return {
        f.name: frozenset(column[0] for column in f.metadata["columns"])
        for f in dataclasses.fields(desc.config_cls)
        if f.metadata.get("columns")
    }


def unread_param(
    metric_name: str, params: Mapping[str, object], *, beside: Iterable[str] = ()
) -> str | None:
    """What to say about the first key of ``params`` — a leaf's keys bar
    its structural ones, or a flat override's bar ``mode`` — that
    ``metric_name`` does not read, or None. A row of one of its tables is
    checked too: ``points row 2: unknown field "tolerence" — did you mean
    "tolerance"?`` (rows count from 1, as the tool's tables do).

    ``beside`` names the keys written beside the params — a leaf's
    ``metric`` / ``variable`` / ``against`` / ``window``, an override's
    ``mode`` — which the caller reads itself: a misspelling of one of
    THOSE lands among the params, and is offered its spelling (``agianst``
    → ``against``)."""
    known = metric_fields(metric_name)
    if known is None:
        return None
    problem = unknown_field(params, (*known, *beside))
    if problem is not None:
        return problem
    problem = _unread_inside(params, declares_quantity(metric_name))
    if problem is not None:
        return problem
    for name, columns in row_fields(metric_name).items():
        rows = params.get(name)
        if not isinstance(rows, list):
            continue
        for i, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue  # the mode's own reader says what a row must be
            problem = unknown_field(row, columns) or _unread_inside(row, False)
            if problem is not None:
                return f"{name} row {i}: {problem}"
    return None


def declares_quantity(metric_name: str) -> bool:
    """Whether the metric's config has a ``quantity`` field of its own —
    ``scalar`` — and so reads the object's keys. On any other metric
    ``quantity`` is the inert param (:data:`EVERY_METRIC`): of any shape,
    read by nothing, and not looked into."""
    desc = metric(metric_name)
    return (
        desc is not None
        and dataclasses.is_dataclass(desc.config_cls)
        and any(f.name == "quantity" for f in dataclasses.fields(desc.config_cls))
    )


def _unread_inside(values: Mapping[str, object], quantity_is_read: bool) -> str | None:
    """What to say about the first key INSIDE one of ``values`` — a leaf's
    params, or the cells of a table row — that nothing reads, or None:
    ``tolerance: unknown field "uper" — did you mean "upper"?``.

    Two kinds of value are objects. A ``quantity`` holds ``reduce``
    (``modes.QUANTITY_KEYS``): ``{"reduce": "max", "over": "x"}`` scored the
    max and dropped ``over``. Every other object is a tolerance's
    lower/upper pair, and ``sides.parse_sides`` — the one place a pair is
    read — takes exactly those two keys: ``{"lower": 0.1, "uper": 0.2}`` was
    found at compare, as a criterion that could not be configured. Both are
    said here, where the spec is read."""
    from .modes import QUANTITY_KEYS
    from .sides import PAIR_KEYS

    for name, value in values.items():
        if not isinstance(value, dict):
            continue
        if name == "quantity":
            if not quantity_is_read:
                continue
            problem = unknown_field(value, QUANTITY_KEYS)
        else:
            problem = unknown_field(value, PAIR_KEYS)
        if problem is not None:
            return f"{name}: {problem}"
    return None


def unread_override_key(override: Mapping[str, object]) -> tuple[str, str] | None:
    """For a flat ``comparison.variable_overrides`` entry holding a key its
    mode does not read: ``(that mode, what to say)``; else None.

    The mode is the one ``modes.resolve_mode`` gives it. With no ``mode``
    that is the default metric, or ``points`` under ``--default-points``
    (:func:`~.registry.unnamed_metrics`) — the flag is not known where a
    spec is read, so the entry may carry what EITHER reads, and is refused
    only when neither reads all of it. A ``mode`` no metric is registered
    under is not judged here: the comparison fails that variable loudly.
    """
    params = {k: v for k, v in override.items() if k != "mode"}
    named = override.get("mode") or ""
    modes = (str(named),) if named else unnamed_metrics()
    problems = [unread_param(mode, params, beside=("mode",)) for mode in modes]
    if any(problem is None for problem in problems):
        return None
    assert problems[0] is not None
    return modes[0], problems[0]
