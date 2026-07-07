"""Run-level provenance metadata: which simulator/backend/version produced
a set of results.

Motivation (2026-07-07): a report showed *what* the verdicts were but never
*what produced them*. When results drift, the first question is "did the tool
change?" — e.g. Dymola 2025x vs 2026x, or an MSL bump. Without the backend +
version stamped on the report, that question needs a full re-investigation.

Design: the identity that's always available comes from ``Config`` for free
(``simulator_backend``, the user-configured ``simulator`` label, ``os_name``).
The *actual* tool-reported version (``DymolaVersion()``, ``getVersion()``, …)
is best-effort — captured from a live backend when obtainable, ``None`` when
not, and never allowed to fail a run. ``tool_version`` is therefore the gold
standard when present and the configured ``simulator`` label is the reliable
floor.

This dataclass is constructed once per run by the runner, serialized into
``status.json`` under a ``"metadata"`` key, and read back by the reporters to
render a provenance banner.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from ..config import Config


def _dstf_version() -> str:
    """Installed DSTF version, or ``"unknown"`` when it can't be resolved.

    Never raises — provenance must not be able to break a run.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("dstf")
        except PackageNotFoundError:
            return "unknown"
    except Exception:  # pragma: no cover — defensive
        return "unknown"


@dataclass
class RunMetadata:
    """Provenance for one test run.

    ``backend``       resolved backend family (e.g. ``"Dymola"``).
    ``simulator``     the label the user configured (e.g. ``"Dymola 2026x"``);
                      equals ``backend`` when no version suffix was given.
    ``os``            ``"linux"`` / ``"macos"`` / ``"windows"``.
    ``tool_version``  actual tool-reported version, or ``None`` when the
                      backend couldn't (or wasn't asked to) report one.
    ``dstf_version``  the DSTF package version that ran the tests.
    ``generated_at``  epoch seconds when the run started; ``None`` only in
                      synthetic contexts (tests) that don't stamp a time.
    """

    backend: str
    simulator: str
    os: str
    tool_version: str | None = None
    dstf_version: str = ""
    generated_at: float | None = None

    @classmethod
    def from_config(
        cls,
        config: Config,
        tool_version: str | None = None,
        now: float | None = None,
    ) -> RunMetadata:
        """Build from a resolved ``Config``.

        ``now`` is injectable so tests can assert a deterministic timestamp;
        production passes ``None`` and gets wall-clock ``time.time()``.
        """
        return cls(
            backend=config.simulator_backend,
            simulator=config.simulator or config.simulator_backend,
            os=config.os_name or "unknown",
            tool_version=tool_version,
            dstf_version=_dstf_version(),
            generated_at=time.time() if now is None else now,
        )

    def as_dict(self) -> dict:
        """Plain-dict form for JSON serialization into ``status.json``."""
        return asdict(self)
