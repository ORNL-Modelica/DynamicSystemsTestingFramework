"""The backend plug-in example under ``examples/plugins/DecayBackend`` runs.

The documentation includes that example's files as they are, so this test
is what keeps the page true: the plug-in is found the way an installed one
is — an entry point of the ``dstf.plugins`` group, read by
``importlib.metadata`` from a distribution's metadata on ``sys.path`` — and
its example project is run with ``dstf run`` twice: once to store the
baseline, once to score against it.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import pytest

from dstf import plugins
from dstf.cli.main import main

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "plugins" / "DecayBackend"


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """The example as an installed distribution: its module importable and
    its entry point in a ``.dist-info`` on ``sys.path`` — what ``pip
    install`` of its ``pyproject.toml`` leaves."""
    from dstf import backends

    site = tmp_path / "site"
    info = site / "dstf_decay-0.1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: dstf-decay\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[dstf.plugins]\ndecay = dstf_decay\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.syspath_prepend(str(EXAMPLE))
    plugins._reset_for_tests()
    yield
    plugins._reset_for_tests()
    backends._REGISTRY.pop("Decay", None)
    sys.modules.pop("dstf_decay", None)


def test_the_entry_point_is_the_one_in_the_example_pyproject():
    """The distribution the fixture fakes declares what the example's
    ``pyproject.toml`` does."""
    text = (EXAMPLE / "pyproject.toml").read_text(encoding="utf-8")
    assert '[project.entry-points."dstf.plugins"]\ndecay = "dstf_decay"' in text


def test_the_example_plugin_runs_its_project(installed, tmp_path, capsys):
    project = tmp_path / "project"
    shutil.copytree(EXAMPLE / "project", project)
    config = str(project / "testing.json")

    assert main(["run", "--config", config, "--accept", "--no-browser"]) == 0
    from dstf.backends import backend_class

    assert backend_class("Decay").__module__ == "dstf_decay"
    stored = project / "ReferenceResults" / "Decay"
    assert list(stored.glob("*/ref_*.json")), "no baseline was stored"

    assert main(["run", "--config", config, "--no-browser"]) == 0
    work = next((project / "testing_output").rglob("verdicts.json"))
    verdicts = json.loads(work.read_text(encoding="utf-8"))
    by_model = {test["model_id"]: test for test in verdicts["tests"]}
    assert by_model["Decay.Settle"]["status"] == "PASS"
    assert verdicts["exit_code"] == 0


def test_the_setting_is_read(installed, tmp_path):
    """``rate`` from the project's block reaches the simulation: the stored
    trajectory is ``exp(-0.5 t)``."""
    project = tmp_path / "project"
    shutil.copytree(EXAMPLE / "project", project)
    assert (
        main(
            [
                "run",
                "--config",
                str(project / "testing.json"),
                "--accept",
                "--no-browser",
            ]
        )
        == 0
    )
    result = next((project / "testing_output").rglob("result.json"))
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["y"][-1] == pytest.approx(math.exp(-0.5 * 4))
