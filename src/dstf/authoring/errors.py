"""What the tool says when an action fails.

The errors a person can cause — a patch the validator refuses, a drag a
criterion cannot take, a move the tree refuses, a value out of range, a
locked spec, a running job — read as their plain reason: the location is
already on the screen, beside the row or the plot it came from. The
validator's refusal of a tree of warnings read "PatchError: validator
rejected the patched tree: metrics: tree has no leaf targeting 'primary'
..."; it now reads the sentence alone.
Anything else is a bug and keeps its class name, so it can be reported.
"""

from __future__ import annotations

from ..comparison.handles import HandleEditError
from ..config import ConfigError
from ..discovery.patch_apply import PatchError
from .session import JobRunningError, LockedError
from .tree_move import MoveRefused
from .unscorable import Refused

#: What a person's own edit raises. Shown as its reason and never logged.
USER_ERRORS: tuple[type[BaseException], ...] = (
    PatchError,
    HandleEditError,
    ConfigError,
    LockedError,
    JobRunningError,
    # A move the tree refuses — a Move to… menu or a drag map gone stale
    # under an edit staged since offers one — is said, not a bug.
    MoveRefused,
    # A value an editor will not stage: typed on the way to a good one.
    Refused,
)


def user_text(e: BaseException) -> str:
    """``e`` as the tool shows it: its ``reason`` (else its text) for a
    person's own error or a ValueError, the class name first for anything
    else. A ValueError also covers a JSONDecodeError or a numpy shape error
    from a bug, so a caller that shows one also logs it (see `USER_ERRORS`)."""
    if isinstance(e, (*USER_ERRORS, ValueError)):
        return str(getattr(e, "reason", None) or e)
    return f"{type(e).__name__}: {e}"
