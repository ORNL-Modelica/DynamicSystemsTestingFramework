"""Pure unit tests for OpenModelica stdout parsing."""

from pathlib import Path

import pytest

from dstf.simulators.openmodelica.log_parser import (
    ParsedOmcOutput,
    parse_omc_stdout,
)

FIXTURES = Path(__file__).parent / "fixtures" / "results_openmodelica"


def _synth_record(**overrides) -> str:
    """Build a fake SimulationResult record block for tests."""
    defaults = dict(
        resultFile='/tmp/test_0001/result_res.mat',
        simulationOptions="stopTime = 1.0",
        messages="LOG_SUCCESS | info | The simulation finished successfully.",
        timeFrontend=0.1,
        timeBackend=0.05,
        timeSimCode=0.01,
        timeTemplates=0.02,
        timeCompile=0.9,
        timeSimulation=0.03,
        timeTotal=1.11,
    )
    defaults.update(overrides)
    return (
        'record SimulationResult\n'
        f'    resultFile = "{defaults["resultFile"]}",\n'
        f'    simulationOptions = "{defaults["simulationOptions"]}",\n'
        f'    messages = "{defaults["messages"]}",\n'
        f'    timeFrontend = {defaults["timeFrontend"]},\n'
        f'    timeBackend = {defaults["timeBackend"]},\n'
        f'    timeSimCode = {defaults["timeSimCode"]},\n'
        f'    timeTemplates = {defaults["timeTemplates"]},\n'
        f'    timeCompile = {defaults["timeCompile"]},\n'
        f'    timeSimulation = {defaults["timeSimulation"]},\n'
        f'    timeTotal = {defaults["timeTotal"]}\n'
        'end SimulationResult;\n'
    )


class TestParseSuccess:
    def test_real_pid_controller_fixture(self):
        text = (FIXTURES / "pid_controller_stdout.txt").read_text()
        parsed = parse_omc_stdout(text)
        assert parsed.success is True, (
            f"parser should treat a successful omc run as success; got "
            f"result_file={parsed.result_file!r} messages={parsed.messages!r}"
        )
        assert parsed.result_file.endswith("result_res.mat")
        assert parsed.timings is not None
        for k in ("frontend", "backend", "simcode", "templates",
                  "compile", "simulation", "total"):
            assert k in parsed.timings
            assert parsed.timings[k] >= 0.0

    def test_synthetic_success(self):
        p = parse_omc_stdout(_synth_record())
        assert p.success is True
        assert p.result_file == "/tmp/test_0001/result_res.mat"
        assert p.timings["frontend"] == 0.1
        assert p.timings["total"] == 1.11
        assert "finished successfully" in p.messages

    def test_multiline_messages_preserved(self):
        """messages is a multi-line Modelica string — parser must grab all of it."""
        msg = "line1\nline2\nline3"
        p = parse_omc_stdout(_synth_record(messages=msg))
        assert "line1" in p.messages
        assert "line2" in p.messages
        assert "line3" in p.messages


class TestParseFailure:
    def test_empty_result_file_means_failure(self):
        p = parse_omc_stdout(_synth_record(
            resultFile="",
            messages="Simulation Failed. Model: X does not exist!",
        ))
        assert p.success is False
        assert p.result_file == ""
        assert "does not exist" in p.messages

    def test_error_string_before_record(self):
        text = "Error: Failed to load package Foo\n" + _synth_record(
            resultFile="", messages="",
        )
        p = parse_omc_stdout(text)
        assert p.success is False
        assert any("Failed to load package" in n for n in p.error_notices)


class TestParseMalformed:
    def test_no_record_at_all(self):
        text = "omc crashed before the simulate() call ran"
        p = parse_omc_stdout(text)
        assert p.success is False
        assert p.timings is None
        assert p.result_file == ""

    def test_truncated_record(self):
        """Record start but no end: graceful failure, not an exception."""
        text = (
            "record SimulationResult\n"
            '    resultFile = "/tmp/foo.mat",\n'
            # truncated — no end SimulationResult;
        )
        p = parse_omc_stdout(text)
        assert p.success is False
        assert p.timings is None

    def test_multiple_notices_stitched(self):
        """Pre-record Error/Warning/Notification lines are preserved."""
        text = (
            "Error: thing1\n"
            "Notification: thing2\n"
            "Warning: thing3\n"
            + _synth_record(resultFile="/tmp/r_res.mat", messages="")
        )
        p = parse_omc_stdout(text)
        joined = "\n".join(p.error_notices)
        assert "thing1" in joined
        assert "thing3" in joined
