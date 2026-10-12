"""`dstf author` (§4.7 "Launch"; brief §3.8): the project checked for a
config or spec error, the shell mounted, the project opened on a thread
once the server runs, served on 127.0.0.1 until Stop server. The page
appears before a result is read."""

from __future__ import annotations

from pathlib import Path

from nicegui import app, ui

from ..session import check_project
from .shell import Shell


def run_app(
    config_path: Path,
    *,
    filter: str | None = None,
    package: str | None = None,
    port: int | None = None,
    work_dir: Path | None = None,
    backend: str | None = None,
    show: bool = True,
) -> None:
    # Before anything is served: a config or spec error is one `Config
    # error: …` line and exit 2, as from every other command (`cli.main`
    # prints it). The open itself still runs on a thread, below — it scans
    # the library and reads the results, which this does not.
    check_project(config_path, backend=backend, work_dir=work_dir)
    shell = Shell(
        config_path,
        work_dir=work_dir,
        filter=filter,
        package=package,
        show_browser=show,
    )
    shell.mount()

    def start() -> None:
        shell.start()
        shell.open_async(backend)

    app.on_startup(start)
    app.on_shutdown(shell.shutdown)
    ui.run(
        shell.index,
        host="127.0.0.1",
        port=port or 8080,
        show=show,
        reload=False,
        title="DSTF",
    )
