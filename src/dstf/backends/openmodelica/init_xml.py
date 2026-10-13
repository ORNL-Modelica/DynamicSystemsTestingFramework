"""What omc's ``<prefix>_init.xml`` says of a model's SIZE.

OpenModelica prints no translation statistics, so its statistics were
timing only: a model that gained a state said ``Continuous states: 2 -> 3``
on Dymola (its translation log) and nothing here. omc does write the
system's size, as attributes of the root element of the init file it
generates beside the executable (``fmiModelDescription
numberOfContinuousStates="3" …``). What that file holds of a quantity
Dymola's backend records is read into the SAME statistics key — one
structural check, one Statistics row, on both simulators.

Read from the file, at every read of a result: nothing is kept beside the
result for it, so a ``compare`` of an earlier run has it too. A baseline
accepted before this has no such statistic, and a statistic one side lacks
is no change (``comparator._check_structural_changes``, ``stats_rows.
changed``): the first accept after it records the number.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

#: omc's attribute → the ``statistics.translation`` key Dymola's translation
#: log fills with the same quantity. Only what both simulators count the
#: same way: omc's other counts (event indicators, algebraic variables by
#: type, parameters by type) have no twin there — Dymola's ``free_parameters``
#: and ``scalar_unknowns`` are other quantities — and are not read.
SYSTEM_SIZE = {"numberOfContinuousStates": "continuous_time_states"}

#: A count as omc writes one: ASCII digits and nothing else.
_COUNT = re.compile(r"[0-9]+")


def system_size(init_xml: Path) -> dict[str, int]:
    """The ``statistics.translation`` entries *init_xml* gives; none for a
    file that is not there, does not parse, or holds no count under an
    attribute — an old omc, a build that failed before it wrote the file.
    ``0`` is a count: a model with no state has one. Only the root element's start tag is read: the file lists every
    variable of the model, megabytes for a large one."""
    try:
        for _event, root in ET.iterparse(init_xml, events=("start",)):
            attributes = dict(root.attrib)
            break
        else:
            return {}
    except (OSError, ET.ParseError):
        logger.debug("no system size from %s", init_xml, exc_info=True)
        return {}
    found: dict[str, int] = {}
    for attribute, key in SYSTEM_SIZE.items():
        text = attributes.get(attribute)
        # A count is a non-negative decimal integer, all of it — or it is
        # not recorded. `int()` also reads `-1`, `+3`, `1_000`, ` 3 ` and
        # the digits of other scripts, none of which omc writes for one.
        if text is not None and _COUNT.fullmatch(text):
            found[key] = int(text)
    return found


def with_system_size(stats: dict | None, init_xml: Path) -> dict | None:
    """*stats* with what *init_xml* says under ``translation``: a new dict,
    or *stats* itself — None included — when the file says nothing."""
    found = system_size(init_xml)
    if not found:
        return stats
    merged = dict(stats or {})
    merged["translation"] = {**(merged.get("translation") or {}), **found}
    return merged


__all__ = ["SYSTEM_SIZE", "system_size", "with_system_size"]
