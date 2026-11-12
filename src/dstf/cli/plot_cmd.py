"""`dstf plot`: two runs or more of a library as images and a page."""

import argparse
import sys
from pathlib import Path

from ..config import Config, ConfigError
from .baseline_cmds import MAX_IMAGES, draw, wrote
from .common import NotLabelled, _build_config, labelled


def _run_config(args: argparse.Namespace, work_dir: Path, backend: str) -> Config:
    """The project as the command line names it, read for the run in
    ``work_dir`` by the backend that ran it: what `dstf compare` reads a
    work dir under, with no default testing.json written."""
    args = argparse.Namespace(
        **{**vars(args), "work_dir": str(work_dir), "backend": backend}
    )
    return _build_config(args)


def cmd_plot(args: argparse.Namespace) -> int:
    """Draw the runs' results of the tests asked for: a PNG per variable
    that moved, a table of the constants, and one gallery.html, into the
    --output directory. The runs are only read. Every check runs before
    the directory is created or a file in it removed."""
    from ..reporting.baseline_gallery import (
        GALLERY,
        OutputRefused,
        nothing_moved_line,
        plan_images,
        run_gallery_html,
        run_tables,
        stale_files,
    )
    from ..reporting.images import ImagesUnavailable
    from ..reporting.run_history import run_history
    from ..storage.baseline_diff import DiffError

    out = Path(args.output)
    try:
        history = run_history(
            [labelled(a) for a in args.runs],
            lambda work_dir, backend: _run_config(args, work_dir, backend),
            tests=args.test,
            folders=args.folder,
        )
        images = plan_images(history.tests)
        if args.test is None and len(images) > MAX_IMAGES:
            raise DiffError(
                f"{len(images)} images to draw, over {MAX_IMAGES}: name the "
                "tests (--test) or the folders (--folder) to draw"
            )
        pngs = draw(out, images, stale_files(out, "plot"), "plot")
    except (ConfigError, NotLabelled, DiffError, OutputRefused, ImagesUnavailable) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    page = out / GALLERY
    page.write_text(run_gallery_html(history, images, pngs), encoding="utf-8")
    if history.nothing_moved:
        print(nothing_moved_line(history.nothing_moved))
    print(wrote(len(images), run_tables(history), page))
    return 0
