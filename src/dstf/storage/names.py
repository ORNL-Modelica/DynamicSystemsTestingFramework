"""A baseline's name: a code from its model id.

``ref_<code>.json`` is a model's baseline file, the same name in every
``<Backend>/<os>/`` folder; ``soft_checks/ref_<code>/``,
``companions/ref_<code>/`` and ``reports/ref_<code>/`` are named from the
same code. Every one of those names is built from :func:`baseline_code` and
nothing else — no reader takes a name from a file's contents or from a
directory listing — so a name never has to be allocated, remembered or
looked up: it is computed from the model id, which is inside the file.

The code is the first 12 hex digits of sha256 over the model id as UTF-8:
48 bits, so the chance that any two of 10,000 models share a code is about
2e-7. A collision is refused when a baseline is written (the file holds its
model id), never silently overwritten.

The ``ref_`` prefix is kept from the old numbered names (``ref_0001.json``)
because TRANSFORM-UnitTests' table script globs ``ref_*.json``. A code can be
all digits (``TRANSFORM.Media.IdealGases.Examples.HeXeAlternate`` →
``ref_121677875640.json``), so an old name and a new one are never told
apart by a digit pattern.
"""

import hashlib

#: Hex digits of the code: 48 bits.
CODE_LENGTH = 12


def baseline_code(model_id: str) -> str:
    """The code of ``model_id``: the first 12 hex digits of its UTF-8 sha256."""
    return hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:CODE_LENGTH]


def baseline_stem(model_id: str) -> str:
    """``ref_<code>`` — the name of ``model_id``'s role folders and report
    dir, and what a row's Reference reads."""
    return f"ref_{baseline_code(model_id)}"


def baseline_file(model_id: str) -> str:
    """``ref_<code>.json`` — the name of ``model_id``'s baseline file."""
    return f"{baseline_stem(model_id)}.json"
