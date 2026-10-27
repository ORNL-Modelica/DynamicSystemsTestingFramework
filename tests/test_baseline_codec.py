"""The baseline types and THE codec of a baseline file: decode -> encode is
byte-identical, and a single-precision file reads back through float32."""

import json

import numpy as np
import pytest

from dstf.storage import baseline_codec as codec
from dstf.storage.baseline import (
    PRIMARY_BASELINE,
    Baseline,
    DiagnosticSummary,
    Sampling,
    StoredVariable,
)


def _baseline():
    return Baseline(
        name=PRIMARY_BASELINE,
        time=[0.0, 1.0],
        variables=[
            StoredVariable(index=1, name="x", expression="a.x", values=[1.0, 2.0]),
            StoredVariable(index=2, name="x", expression="dup", values=[9.0, 9.0]),
        ],
        diagnostics=[DiagnosticSummary(name="CPUtime", final=0.5, min=0.0, max=0.5)],
    )


def test_variable_by_name_is_the_first_with_that_name():
    assert _baseline().variable("x").expression == "a.x"
    assert _baseline().variable("nope") is None


def test_sampling_holds_the_four_fields():
    s = Sampling(setting="all", source_points=3, stored_points=3, precision="single")
    assert (s.setting, s.source_points, s.stored_points, s.precision) == (
        "all",
        3,
        3,
        "single",
    )


PRIMARY_SINGLE = """{
  "model_id": "Lib.M",
  "test_id": "0007",
  "status": "active",
  "date_added": "2026-10-13T00:00:00+00:00",
  "last_updated": "2026-10-13T00:00:00+00:00",
  "simulation": {
    "stop_time": 1.0,
    "tolerance": 1e-06,
    "method": "dassl",
    "number_of_intervals": 1,
    "output_interval": null
  },
  "sampling": {
    "setting": "all",
    "source_points": 2,
    "stored_points": 2,
    "precision": "single"
  },
  "comparison": {
    "tolerance": 0.001
  },
  "statistics": {
    "translation": {
      "continuous_time_states": 1
    }
  },
  "diagnostics": [
    {
      "name": "CPUtime",
      "final": 0.015562999993562698,
      "min": 0.0019630000460892916,
      "max": 0.015562999993562698
    }
  ],
  "n_vars": 1,
  "time": [
    0.0,
    0.100000001
  ],
  "variables": [
    {
      "index": 1,
      "name": "x",
      "expression": "a.x",
      "values": [
        1.0,
        0.333333343
      ]
    }
  ]
}
"""


def test_single_precision_reads_through_float32_and_writes_back_identically():
    ref = codec.decode_reference(json.loads(PRIMARY_SINGLE))
    assert ref.baseline.time[1] == float(np.float32(0.1))
    assert ref.baseline.variable("x").values[1] == float(np.float32(1 / 3))
    assert codec.encode_reference(ref) == PRIMARY_SINGLE


def test_double_file_round_trips_identically():
    text = (
        PRIMARY_SINGLE.replace('"single"', '"double"')
        .replace("0.100000001", "0.1")
        .replace("0.333333343", "0.3333333333333333")
    )
    assert codec.encode_reference(codec.decode_reference(json.loads(text))) == text


@pytest.mark.parametrize(
    "values,summary",
    [([2.0, 9.0, 5.0], (5.0, 2.0, 9.0)), ([5.0], (5.0, 5.0, 5.0)), ([], (None,) * 3)],
    ids=["three", "one", "none"],
)
def test_old_diagnostic_values_are_summarized_on_read(values, summary):
    data = json.loads(PRIMARY_SINGLE)
    data["diagnostics"] = [{"name": "EventCounter", "values": values}]
    d = codec.decode_reference(data).baseline.diagnostics[0]
    assert (d.final, d.min, d.max) == summary


def test_absent_optional_fields_stay_absent():
    data = json.loads(PRIMARY_SINGLE)
    for key in (
        "status",
        "date_added",
        "sampling",
        "comparison",
        "statistics",
        "diagnostics",
    ):
        data.pop(key)
    text = json.dumps(data, indent=2) + "\n"
    ref = codec.decode_reference(json.loads(text))
    assert ref.status is None and ref.active_status == "active"
    assert codec.encode_reference(ref) == text


def test_soft_check_round_trips_in_its_own_key_order():
    text = (
        json.dumps(
            {
                "time": [0.0, 1.0],
                "variables": [
                    {"index": 1, "name": "x", "expression": "", "values": [1.0, 2.0]}
                ],
                "simulation": {"stop_time": 1.0},
                "statistics": {"a": 1},
                "provenance": {"origin": "cross-backend"},
            },
            indent=2,
        )
        + "\n"
    )
    b = codec.decode_series(json.loads(text), "experiment")
    assert b.name == "experiment" and b.provenance == {"origin": "cross-backend"}
    assert codec.encode_soft_check(b) == text


def test_header_reads_model_and_status(tmp_path):
    p = tmp_path / "ref_0007.json"
    p.write_text(PRIMARY_SINGLE.replace('"active"', '"skip"'), encoding="utf-8")
    assert codec.read_reference_header(p) == ("Lib.M", "skip")


def test_header_of_a_file_without_status_reads_active(tmp_path):
    data = json.loads(PRIMARY_SINGLE)
    data.pop("status")
    p = tmp_path / "ref_0007.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    assert codec.read_reference_header(p) == ("Lib.M", "active")


def test_a_single_precision_series_reads_through_float32_whatever_its_role():
    # A soft check / companion / soft-check-add source copied from a
    # single-precision reference keeps its `sampling` and is read like one.
    b = codec.decode_series(json.loads(PRIMARY_SINGLE), "experiment")
    assert b.variable("x").values[1] == float(np.float32(1 / 3))


def test_stored_numbers_rounds_by_dtype():
    assert codec.stored_numbers(np.array([1 / 3], dtype=np.float32)) == [0.333333343]
    assert codec.stored_numbers(np.array([1 / 3])) == [1 / 3]


def test_write_reference_is_atomic_and_reads_back(tmp_path):
    ref = codec.decode_reference(json.loads(PRIMARY_SINGLE))
    p = tmp_path / "ref_0007.json"
    codec.write_reference(p, ref)
    assert p.read_text(encoding="utf-8") == PRIMARY_SINGLE
    assert codec.read_reference(p) == ref


def test_provenance_is_written_after_sampling_and_read_back():
    data = json.loads(PRIMARY_SINGLE)
    prov = {
        "backend": "Dymola",
        "backend_name": "Dymola 2026x",
        "tool_version": "Dymola Version 2026x",
        "library_versions": {"Modelica": "4.1.0"},
        "dstf_version": "1.2.0",
        "os": "linux",
        "run_started": "2026-10-15T09:12:44+00:00",
    }
    ref = codec.decode_reference({**data, "provenance": prov})
    assert ref.baseline.provenance == prov
    keys = list(json.loads(codec.encode_reference(ref)))
    assert keys.index("provenance") == keys.index("sampling") + 1
