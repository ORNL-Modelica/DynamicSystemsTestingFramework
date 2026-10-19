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


def test_a_listed_name_the_result_holds_is_recorded_and_one_it_lacks_is_not(
    installed, tmp_path
):
    """The entry lists `y` and `z` (its own `diagnostic_variables`, after
    the project's default CPUtime and EventCounter): the example records
    `y`, which its result holds, as a diagnostic — the baseline stores its
    final, min and max — and skips every name the result lacks."""
    project = tmp_path / "project"
    shutil.copytree(EXAMPLE / "project", project)
    spec_path = project / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["tests"][0]["diagnostic_variables"] = ["y", "z"]
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    config = str(project / "testing.json")
    assert main(["run", "--config", config, "--accept", "--no-browser"]) == 0
    stored = next((project / "ReferenceResults" / "Decay").glob("*/ref_*.json"))
    diagnostics = json.loads(stored.read_text(encoding="utf-8")).get("diagnostics", [])
    assert [d["name"] for d in diagnostics] == ["y"]
    (y,) = diagnostics
    assert y["final"] == pytest.approx(math.exp(-0.5 * 4), rel=1e-6)
    assert y["max"] == pytest.approx(1.0)


def _documented() -> set[str]:
    """Every object the API page documents, by its dotted name: each
    ``auto…::`` directive's target."""
    import re

    page = Path(__file__).resolve().parents[1] / "docs" / "reference" / "api.md"
    text = page.read_text(encoding="utf-8")
    return set(re.findall(r"^\.\. auto\w+:: (dstf[\w.]*)$", text, re.MULTILINE))


def _imported(source: Path) -> list[tuple[str, object]]:
    """Every ``dstf`` name ``source`` imports, by the dotted name it is
    imported as, with the object it is."""
    import ast
    import importlib

    names = []
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("dstf"):
            module = importlib.import_module(node.module)
            for alias in node.names:
                names.append(
                    (f"{node.module}.{alias.name}", getattr(module, alias.name))
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("dstf"):
                    names.append((alias.name, importlib.import_module(alias.name)))
    return names


def _undocumented(source: Path) -> list[str]:
    """The ``dstf`` names ``source`` imports that the API page does not
    document: neither by the name they are imported as nor, for a class or a
    function imported where it is re-exported, by where it is defined."""
    documented = _documented()
    missing = []
    for name, obj in _imported(source):
        where = f"{getattr(obj, '__module__', '')}.{getattr(obj, '__qualname__', '')}"
        if name not in documented and where not in documented:
            missing.append(name)
    return missing


def test_the_example_imports_only_what_the_api_page_documents():
    """Everything else is internal and may change at any time (the page's
    own warning), so the example a plug-in is written from must use none of
    it. It imported ``dstf.backends.locate.EXECUTABLE`` and ``SEARCH`` and
    ``dstf.backends.types.resolve_variable_patterns``, none of them on the
    page."""
    source = EXAMPLE / "dstf_decay.py"
    assert len(_imported(source)) >= 10, "the AST walk found the imports"
    assert _undocumented(source) == []


def test_an_internal_name_the_example_imported_would_be_named(tmp_path):
    """The check bites: one more import, of a name the page does not
    document, is named."""
    source = tmp_path / "dstf_decay.py"
    source.write_text(
        (EXAMPLE / "dstf_decay.py").read_text(encoding="utf-8")
        + "\nfrom dstf.backends.types import is_glob_pattern\n",
        encoding="utf-8",
    )
    assert _undocumented(source) == ["dstf.backends.types.is_glob_pattern"]
