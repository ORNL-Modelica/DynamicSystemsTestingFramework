"""OpenModelica records a model's size: `statistics.translation.
continuous_time_states`, the key Dymola's translation log fills, read from
the init file omc generates beside the result — so a model that gained a
state says `Continuous states: 2 -> 3` on both simulators, where on
OpenModelica it said nothing.

The reader over a file shaped as omc writes it; the read through both
OpenModelica backends; what a baseline accepted before this does (nothing:
old data); and real omc over a scratch copy of the example library, with
the persistent workers and with `--batch`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from conftest import make_test_model
from dstf.backends.openmodelica.backend import OpenModelicaBackend
from dstf.backends.openmodelica.init_xml import system_size, with_system_size
from dstf.backends.openmodelica.persistent_backend import PersistentOpenModelicaBackend
from dstf.backends.types import TestResult, TestRunResult, VariableResult
from dstf.cli import main
from dstf.comparison.comparator import compare_test
from dstf.config import Config
from dstf.reporting.stats_rows import changed
from dstf.storage.baseline_codec import decode_reference

FIXTURES = Path(__file__).parent / "fixtures" / "results_openmodelica"
EXAMPLE = Path(__file__).parent.parent / "examples" / "modelica" / "ModelicaTestingLib"

#: The head of an init file as omc 1.27 writes it: one attribute a line,
#: each followed by a `cmt_…` attribute of its own on the same line.
HEAD = """<?xml version = "1.0" encoding="UTF-8"?>

<!-- description of the model interface using an extention of the FMI standard -->
<fmiModelDescription
  fmiVersion                          = "1.0"

  modelName                           = "Lib.M"
  numberOfEventIndicators             = "3"  cmt_numberOfEventIndicators             = "NG:       number of zero crossings,                           FMI"
  numberOfContinuousStates            = "{states}"  cmt_numberOfContinuousStates            = "NX:       number of states,                                   FMI"
  numberOfRealAlgebraicVariables      = "7"  cmt_numberOfRealAlgebraicVariables      = "NY:       number of real variables,                           OMC"
  >
"""
TAIL = """  <DefaultExperiment startTime="0" stopTime="10" stepSize="0.02" tolerance="1e-06" solver="dassl" />
  <ModelVariables>
  </ModelVariables>
</fmiModelDescription>
"""


def _init(path: Path, states: object = 3, tail: str = TAIL) -> Path:
    path.write_text(HEAD.format(states=states) + tail, encoding="utf-8")
    return path


def test_the_states_are_read_into_the_key_dymola_fills(tmp_path):
    assert system_size(_init(tmp_path / "result_init.xml")) == {
        "continuous_time_states": 3
    }


def test_no_state_is_a_count_of_zero_and_a_first_state_is_a_change(tmp_path):
    """A model with no state has a count — 0 — and it is recorded: read
    as nothing, the state a model then gains would be a count with no
    count to compare with, and no line."""
    path = _init(tmp_path / "result_init.xml", states=0)
    assert system_size(path) == {"continuous_time_states": 0}
    assert with_system_size(None, path) == {
        "translation": {"continuous_time_states": 0}
    }
    assert system_size(_init(path, states="007")) == {"continuous_time_states": 7}
    gained = _scored(1, {"translation": {"continuous_time_states": 0}})
    assert [w.text for w in gained.warnings] == ["Continuous states: 0 -> 1"]
    lost = _scored(0, {"translation": {"continuous_time_states": 1}})
    assert [w.text for w in lost.warnings] == ["Continuous states: 1 -> 0"]
    assert changed("translation.continuous_time_states", "count", 1, 0) is True


def test_only_the_root_elements_start_tag_is_read(tmp_path):
    """The file lists every variable of the model — megabytes for a large
    one — and nothing after the root's own attributes is asked for: a body
    that is cut off, or is no XML at all, changes nothing."""
    cut = _init(tmp_path / "result_init.xml", tail="  <ModelVariables>\n   <Scal")
    assert system_size(cut) == {"continuous_time_states": 3}


@pytest.mark.parametrize(
    "write",
    [
        lambda p: None,
        lambda p: p.write_text("", encoding="utf-8"),
        lambda p: p.write_text("not xml at all", encoding="utf-8"),
        lambda p: p.write_text(
            "<fmiModelDescription modelName='M'/>", encoding="utf-8"
        ),
        lambda p: _init(p, states="many"),
        lambda p: _init(p, states="2.5"),
        # What `int()` reads and no count is: a count is digits, all of it.
        lambda p: _init(p, states="-1"),
        lambda p: _init(p, states="+3"),
        lambda p: _init(p, states="1_000"),
        lambda p: _init(p, states=" 3 "),
        lambda p: _init(p, states="٣"),
        lambda p: _init(p, states=""),
    ],
    ids=[
        "no file",
        "empty",
        "not xml",
        "no attribute",
        "no number",
        "no whole number",
        "negative",
        "signed",
        "underscored",
        "padded",
        "a digit of another script",
        "an empty attribute",
    ],
)
def test_a_file_that_says_nothing_gives_nothing(tmp_path, write):
    path = tmp_path / "result_init.xml"
    write(path)
    assert system_size(path) == {}
    # ...and the statistics are the ones handed in, None included.
    timing = {"timing": {"simulation": 0.1}}
    assert with_system_size(timing, path) is timing
    assert with_system_size(None, path) is None


def test_the_size_joins_the_statistics_under_translation(tmp_path):
    path = _init(tmp_path / "result_init.xml", states=20)
    stats = {"timing": {"simulation": 0.1}, "translation": {"kept": 1}}
    merged = with_system_size(stats, path)
    assert merged == {
        "timing": {"simulation": 0.1},
        "translation": {"kept": 1, "continuous_time_states": 20},
    }
    assert stats == {"timing": {"simulation": 0.1}, "translation": {"kept": 1}}
    assert with_system_size(None, path) == {
        "translation": {"continuous_time_states": 20}
    }


@pytest.mark.parametrize(
    "backend_cls", [OpenModelicaBackend, PersistentOpenModelicaBackend]
)
@pytest.mark.parametrize("ran", [True, False], ids=["after its run", "compare later"])
def test_both_backends_read_it_with_the_result(tmp_path, backend_cls, ran):
    """One `read_result` serves the persistent workers, `--batch` and a
    `compare` of an earlier run (no run result in hand): the size is read
    from the file each time, beside whatever the run recorded."""
    (tmp_path / "package.mo").write_text("package Lib end Lib;", encoding="utf-8")
    cfg = Config(
        source_path=tmp_path,
        reference_root=tmp_path / "refs",
        backend_name="OpenModelica",
        work_dir=tmp_path / "work",
    )
    test_dir = cfg.work_dir / "test_0001"
    test_dir.mkdir(parents=True)
    shutil.copy(
        FIXTURES / "pid_controller_res.mat", test_dir / backend_cls.RESULT_MAT_FILENAME
    )
    _init(test_dir / backend_cls.INIT_XML_FILENAME, states=4)
    test = make_test_model(
        "Lib.M", variable_patterns=["inertia1.phi"], stop_time=1.0, source="spec"
    )
    run = (
        TestRunResult(
            model_id="Lib.M",
            test_key="test_0001",
            success=True,
            elapsed=1.0,
            statistics={"timing": {"simulation": 0.5}},
        )
        if ran
        else None
    )
    backend = object.__new__(backend_cls)  # no omc is asked for a read
    OpenModelicaBackend.__init__(backend, cfg)
    result = backend.read_result(test, "test_0001", run)
    assert result.success, result.error_message
    assert result.statistics["translation"] == {"continuous_time_states": 4}
    if ran:
        assert result.statistics["timing"] == {"simulation": 0.5}


def _scored(states_now: int | None, reference_statistics: dict | None):
    t = np.linspace(0.0, 1.0, 5)
    result = TestResult(
        "Lib.M",
        True,
        variables=[VariableResult(1, t, t, name="x")],
        statistics=(
            None
            if states_now is None
            else {"translation": {"continuous_time_states": states_now}}
        ),
    )
    reference = {"time": list(t), "variables": [{"name": "x", "values": list(t)}]}
    if reference_statistics is not None:
        reference["statistics"] = reference_statistics
    return compare_test(
        make_test_model("Lib.M", variable_patterns=["x"]),
        result,
        decode_reference(reference),
    )


def test_a_state_gained_is_the_structural_line_dymola_gives():
    comp = _scored(3, {"translation": {"continuous_time_states": 2}})
    assert [w.text for w in comp.warnings] == ["Continuous states: 2 -> 3"]
    assert _scored(2, {"translation": {"continuous_time_states": 2}}).warnings == []


def test_a_baseline_accepted_before_this_says_no_change():
    """Old data: a baseline with timing statistics only — every
    OpenModelica baseline until now — has no state count, and a statistic
    one side lacks is no change, on either tier."""
    assert _scored(3, {"timing": {"simulation": 0.02}}).warnings == []
    assert _scored(3, None).warnings == []
    assert changed("translation.continuous_time_states", "count", 3, None) is False
    assert changed("translation.continuous_time_states", "count", 3, 2) is True


def test_every_shipped_openmodelica_baseline_records_its_state_count():
    """The OpenModelica setup page says a changed state count is a warning
    on OpenModelica; against a baseline with no count it is none, and the
    example's shipped ones had none. They were accepted again."""
    refs = EXAMPLE / "Resources" / "ReferenceResults" / "OpenModelica" / "linux"
    counts = {}
    for path in sorted(refs.glob("ref_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        translation = (data.get("statistics") or {}).get("translation") or {}
        counts[data["model_id"].rsplit(".", 1)[-1]] = translation.get(
            "continuous_time_states"
        )
    assert len(counts) == 13 and None not in counts.values(), counts
    assert (counts["SimpleTest"], counts["HeatRodTest"]) == (2, 20)
    assert counts["ThermostatBuildingTest"] == 4  # the four room temperatures
    assert counts["FrequencyTest"] == 0  # no state: a count all the same


# ---------------------------------------------------------------------------
# Real omc
# ---------------------------------------------------------------------------

omc_unavailable = pytest.mark.skipif(
    shutil.which("omc") is None, reason="omc not installed"
)

SIMPLE = "ModelicaTestingLib.Examples.SimpleTest"


@omc_unavailable
@pytest.mark.ompython
@pytest.mark.parametrize("how", ["persistent", "batch"])
def test_real_omc_says_a_state_was_gained(tmp_path, capsys, how):
    pytest.importorskip("OMPython")
    lib = tmp_path / "ModelicaTestingLib"
    shutil.copytree(EXAMPLE, lib)
    refs = lib / "Resources" / "ReferenceResults"
    cfg = refs / "testing.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    cfg.write_text(json.dumps({**data, "backend": "OpenModelica"}), encoding="utf-8")
    work = tmp_path / "work"
    batch = ["--batch"] if how == "batch" else []

    def dstf(*extra: str) -> int:
        argv = ["--config", str(cfg), "run", "--work-dir", str(work), "--no-browser"]
        return main([*argv, "--filter", SIMPLE, *batch, *extra])

    (ref_path,) = [
        p
        for p in (refs / "OpenModelica" / "linux").glob("ref_*.json")
        if json.loads(p.read_text(encoding="utf-8"))["model_id"] == SIMPLE
    ]

    def reference() -> dict:
        return json.loads(ref_path.read_text(encoding="utf-8"))

    # A baseline from before this — as every shipped one was until they
    # were accepted again — has no state count, and gives no line.
    old = reference()
    assert old["statistics"].pop("translation") == {"continuous_time_states": 2}
    ref_path.write_text(json.dumps(old), encoding="utf-8")
    assert "translation" not in reference()["statistics"]
    capsys.readouterr()
    assert dstf() == 0
    assert "Continuous states" not in capsys.readouterr().out
    # Accepted now, it records the two states of x and y.
    assert dstf("--accept") == 0
    assert reference()["statistics"]["translation"] == {"continuous_time_states": 2}

    model = lib / "Examples" / "SimpleTest.mo"
    text = model.read_text(encoding="utf-8")
    assert "der(y) = -y;" in text
    model.write_text(
        text.replace(
            'Real y(start=1, fixed=true) "Exponential decay";',
            'Real y(start=1, fixed=true) "Exponential decay";\n'
            "  Real z(start=0, fixed=true);",
        ).replace("der(y) = -y;", "der(y) = -y;\n  der(z) = x;"),
        encoding="utf-8",
    )
    capsys.readouterr()
    dstf()
    out = capsys.readouterr().out
    assert "Continuous states" in out and "2 -> 3" in out.replace("→", "->")
