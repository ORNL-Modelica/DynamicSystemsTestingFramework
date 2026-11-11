"""What a reference folder holds, found by name, plus the atomic-write primitive.

A model's baseline is ``ref_<code>.json`` (:mod:`dstf.storage.names`): its
name is computed from the model id, so nothing is allocated, counted or
remembered beside the files — the ref files are the source of truth.
:class:`RefIndex` opens a model's file by that name, and lists a folder for
the commands that walk every baseline. :func:`_atomic_write_text` lives here
because every JSON write in the package uses it.
"""

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

from ..config import ConfigError
from .names import baseline_file, baseline_stem

logger = logging.getLogger(__name__)


#: A baseline file's name today: ``ref_`` and the 12 lowercase hex digits of
#: its model's code. Anything else under ``ref_*.json`` is the old numbered
#: form (``ref_0001.json``). Never told apart by a digit pattern: a real code
#: can be all digits (``ref_121677875640.json``, names.py).
_NAME = re.compile(r"^ref_[0-9a-f]{12}\.json$")

#: The statuses a baseline may have. ``obsolete`` is not one (owner, Q5 of
#: the plan of 2026-11-10): a baseline is removed by deleting its file, and a
#: file still marked so is refused by name rather than silently skipped.
#: Nor is ``skip`` (plan of 2026-11-11): nothing but ``manifest show``'s
#: count read it, so a "skipped" baseline was compared all the same.
STATUSES = ("active",)


def _status_refusal(name: str, status: str) -> str:
    """Why the file ``name`` is refused for its ``status``: the one sentence."""
    return (
        f"{name}: status {json.dumps(status)} is not a status — "
        "delete the file to remove a baseline"
    )


class OldBaselineNames(ConfigError):
    """A reference folder holds baselines under the old numbered names
    (``ref_0001.json``). Refused whole: read as it is, every test of it
    would read NO_REF and an accept would write a second file beside each
    old one."""


def _atomic_write_text(path: Path, text: str, *, newline: str = "\n") -> None:
    """Atomically replace ``path`` with ``text``.

    review 2026-07-06 (finding 31): all reference writes were plain
    ``write_text`` — a Ctrl-C mid-accept corrupted the baseline, the next
    scan silently dropped it, and (while baselines were numbered) its
    number went to a different model. Same tmp-file + replace pattern as
    ``dashboard_render._atomic_write`` (unique tmp name in the same
    directory, retry loop for Windows file locking), except this variant
    always raises on failure — a silently stale baseline is a
    data-integrity bug, not a cosmetic one.

    ``newline`` is passed through to :func:`open` and defaults to ``"\\n"``,
    NOT to :func:`open`'s ``None``: with ``None`` Python
    translates ``\\n`` to ``os.linesep``, so a baseline accepted on Windows
    landed CRLF and the same file accepted from WSL landed LF. The reference
    tree is a surface other people version-control; a cross-platform accept
    turned a one-value edit into a 6,881-line whole-file diff, hiding the
    real change inside it. Every JSON write in the package therefore gets LF
    on every platform, and a new caller cannot reintroduce the bug by
    forgetting a keyword. The one CSV caller still passes ``""``
    explicitly, which is now belt-and-braces rather than load-bearing:
    neither ``""`` nor ``"\\n"`` translates on write, so the ``\\r\\n`` that
    :mod:`csv` embeds survives either way. It stays because it states the
    intent at the call site and because it is what keeps that caller correct
    if this default is ever revisited.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline=newline) as f:
            f.write(text)
        last_err: OSError | None = None
        for delay in (0, 0.05, 0.1, 0.2, 0.5):
            if delay:
                time.sleep(delay)
            try:
                tmp.replace(path)
                return
            except OSError as e:
                last_err = e
        assert last_err is not None
        raise last_err
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


class RefIndex:
    """A reference folder's baselines, each found by its model's name.

    Lookup opens ``ref_<code>.json`` — the one file a model's baseline can
    be — and nothing else; a listing reads every file of the folder for the
    commands that walk them all. Both remember what they read (the folder is
    read once, as the old scan was), and a write through the store updates
    what is remembered.
    """

    def __init__(self, ref_dir: Path):
        self.ref_dir = ref_dir
        #: ``ref_<code>.json`` -> (the model id it holds, its status), or
        #: None for a file that is not there or cannot be read.
        self._files: dict[str, tuple[str, str] | None] = {}
        #: ``ref_<code>.json`` -> why it cannot be read, for each file that
        #: is there and is no readable baseline: what its model is refused by.
        self._unreadable: dict[str, str] = {}
        #: ``ref_<code>.json`` -> why a listing leaves it out, for each file
        #: that holds a model whose name it is not (found by a listing).
        self._misnamed: dict[str, str] = {}
        self._loaded = False
        self._refuse_old_names()

    def _refuse_old_names(self) -> None:
        """Refuse a folder that still holds a numbered file (`OldBaselineNames`).

        Checked when the index is made, before any lookup or write: a
        lookup there finds nothing (every test NO_REF) and an accept writes
        ``ref_<code>.json`` beside the old file, two baselines of one model
        in one folder."""
        if not self.ref_dir.is_dir():
            return
        old = sorted(
            p.name for p in self.ref_dir.glob("ref_*.json") if not _NAME.match(p.name)
        )
        if old:
            more = ", …" if len(old) > 1 else ""
            raise OldBaselineNames(
                f"{self.ref_dir} holds baselines under the old numbered names "
                f"({old[0]}{more}) — convert them with "
                "scripts/convert_baseline_names.py, see CHANGELOG"
            )

    def _read(self, name: str) -> tuple[str, str] | None:
        """``(model_id, status)`` of the file ``name``, or None: not there,
        or not a readable baseline — warned of, by name.

        A ``model_id`` that is no non-empty string is unreadable too: a
        listing computes each file's name from it, so a number there raised
        out of every command that lists the folder. So is a body the codec
        cannot decode (`baseline_codec.check_body`): read as it was, it
        raised out of the run that compared it (measured 2026-11-11,
        ``"variables": "oops"``)."""
        # Imported here: the codec imports `_atomic_write_text` from this
        # module, so a module-level import would be a cycle.
        from .baseline_codec import UndecodableBaseline, read_reference_header

        path = self.ref_dir / name
        if not path.is_file():
            return None
        try:
            # Whole-file read + parse (a header-only read is idea #64).
            model_id, status = read_reference_header(path)
        except json.JSONDecodeError as e:
            why = f"not JSON at line {e.lineno}, column {e.colno}"
        except UnicodeDecodeError:
            why = "not UTF-8 text"
        except UndecodableBaseline as e:
            why = str(e)
        except OSError as e:
            why = e.strerror or str(e)
        else:
            if isinstance(model_id, str) and model_id:
                return model_id, status
            why = (
                "no model_id"
                if model_id is None
                else f"model_id {json.dumps(model_id)} is not a string"
            )
        said = f"{name} cannot be read ({why}) — fix or delete it"
        logger.warning("%s", said)
        self._unreadable[name] = said
        return None

    def _held(self, model_id: str) -> tuple[str, str] | None:
        """What the file named for ``model_id`` holds, read once."""
        name = baseline_file(model_id)
        if name not in self._files:
            # After a listing, a name it did not see is not there.
            self._files[name] = None if self._loaded else self._read(name)
        return self._files[name]

    def _scan(self) -> None:
        """Read every ``ref_<code>.json`` of the folder. A file whose name is
        not its model's code is warned of and left out: no lookup opens it,
        so the model it holds reads as having no baseline."""
        self._files.clear()
        self._unreadable.clear()
        self._misnamed.clear()
        if self.ref_dir.is_dir():
            for ref_file in sorted(self.ref_dir.glob("ref_*.json")):
                if _NAME.match(ref_file.name):
                    self._files[ref_file.name] = self._read(ref_file.name)
        for name, held in self._files.items():
            if held is not None and baseline_file(held[0]) != name:
                said = (
                    f"{name} holds the baseline of {held[0]}, whose file is "
                    f"{baseline_file(held[0])} — rename or delete it"
                )
                logger.warning("%s", said)
                self._misnamed[name] = said
        self._loaded = True

    def ensure_loaded(self) -> None:
        """Read the whole folder now if not yet read. `Session.open` calls
        it before its project is live, so the UI loop — which reads a
        model's baseline name the moment the project is set — finds every
        file already read and never waits on one."""
        if not self._loaded:
            self._scan()

    def path_for(self, model_id: str) -> Path:
        """Where ``model_id``'s baseline is, or is written: computed, never
        looked up."""
        return self.ref_dir / baseline_file(model_id)

    def refusal(self, model_id: str) -> str | None:
        """Why the file named for ``model_id`` cannot be its baseline, or
        None: it cannot be read (not JSON — a merge's conflict markers, a
        hand edit — or no model id), it holds another model (two ids share
        a code, or a hand copy), or its ``status`` is not a status. A file
        that cannot be read is refused, not written over: what it held
        cannot be told, and it may be a baseline one fix away.

        A sentence, not an exception: the comparison FAILS the test with it
        and an accept skips the test with it (`compare_one`, the store's
        write), and every other reader of the lookup sees no baseline — a
        bad file in the folder must not stop a run, a report or the tool."""
        held = self._held(model_id)
        name = baseline_file(model_id)
        if held is None:
            return self._unreadable.get(name)
        other, status = held
        if other != model_id:
            return (
                f"{name} holds the baseline of {other}, not of {model_id} — "
                "two model ids share its code, or the file was copied by hand"
            )
        if status not in STATUSES:
            return _status_refusal(name, status)
        return None

    def refused(self) -> dict[str, str]:
        """``{file name: the sentence it is refused by}`` for every file of
        the folder this index reads as no baseline, in name order: one that
        cannot be read, one that holds a model whose name it is not, one
        whose status is none. The sentences are the ones a lookup refuses a
        test by and a listing warns with — what ``manifest show`` lists."""
        self.ensure_loaded()
        out = {**self._unreadable, **self._misnamed}
        for name, held in self._files.items():
            if name not in out and held is not None and held[1] not in STATUSES:
                out[name] = _status_refusal(name, held[1])
        return dict(sorted(out.items()))

    def file(self, model_id: str) -> Path | None:
        """``model_id``'s baseline file, when it has one this index reads:
        the file named for it, holding it, with a status. None otherwise."""
        if self._held(model_id) is None or self.refusal(model_id) is not None:
            return None
        return self.path_for(model_id)

    def stem(self, model_id: str) -> str | None:
        """``ref_<code>`` of a model that has a baseline here, else None."""
        return baseline_stem(model_id) if self.file(model_id) is not None else None

    def written(self, model_id: str) -> None:
        """A baseline of ``model_id`` was just written under its name."""
        name = baseline_file(model_id)
        self._files[name] = (model_id, "active")
        self._unreadable.pop(name, None)

    def all_tests(self) -> dict[str, dict]:
        """``{code: {"model_id", "status"}}`` for every file of the folder
        named for the model it holds — a refused status included."""
        self.ensure_loaded()
        return {
            name[len("ref_") : -len(".json")]: {"model_id": held[0], "status": held[1]}
            for name, held in sorted(self._files.items())
            if held is not None and baseline_file(held[0]) == name
        }

    def active_tests(self) -> dict[str, str]:
        """``{code: model_id}`` for every baseline a lookup reads."""
        return {
            code: entry["model_id"]
            for code, entry in self.all_tests().items()
            if entry["status"] in STATUSES
        }
