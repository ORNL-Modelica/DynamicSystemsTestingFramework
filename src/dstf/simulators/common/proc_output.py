"""Shared subprocess-output helpers for simulator backends."""

from __future__ import annotations


def decode_output(data: bytes | str | None) -> str:
    """Normalize subprocess output (str, bytes, or None) to str.

    review 2026-07-06 (finding 23): ``subprocess.TimeoutExpired.stdout`` /
    ``.stderr`` are **bytes** even when the process was opened with
    ``text=True``. Handlers that did ``(exc.stdout or "") + "..."`` or
    ``write_text(exc.stdout)`` raised TypeError inside the timeout path —
    aborting the whole run precisely when a test timed out after printing
    output. Every backend's timeout handler funnels through this helper.
    """
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data
