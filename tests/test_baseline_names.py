"""A baseline's file name is a code from its model id.

The expected codes are LITERALS, computed once outside this suite (with
``sha256sum``): the name is a format other people's trees hold — TRANSFORM's
converted baselines carry these exact names — so a test that recomputed it
the way the code does would follow any change to the rule.
"""

from dstf.storage.names import baseline_code, baseline_file, baseline_stem

#: Real TRANSFORM model ids and the codes their converted files carry.
KNOWN = {
    # The one real code that is all digits: new and old names are never told
    # apart by a digit pattern.
    "TRANSFORM.Media.IdealGases.Examples.HeXeAlternate": "121677875640",
    "TRANSFORM.Blocks.Examples.PulseSine_Test": "5341dec448cc",
    "TRANSFORM.Blocks.Examples.EasingRamp_Test": "6045fe1e176d",
    "TRANSFORM.HeatAndMassTransfer.Examples.ExamplesFrom_NellisAndKlein.Example_1_4_1_FuelElement": "5b4e2768fb27",
    "TRANSFORM.Units.Conversions.Functions.PrefixMultipliers.Examples.check_peta": "3a225bdb4edf",
}


def test_known_codes():
    assert {m: baseline_code(m) for m in KNOWN} == KNOWN


def test_file_name_is_ref_code_json():
    hexe = "TRANSFORM.Media.IdealGases.Examples.HeXeAlternate"
    assert baseline_file(hexe) == "ref_121677875640.json"


def test_same_id_same_name():
    a = "TRANSFORM.Blocks.Examples.PulseSine_Test"
    # Equal, but built at run time: not the same object.
    b = "".join(["TRANSFORM.Blocks.", "Examples.PulseSine_Test"])
    assert baseline_file(a) == baseline_file(b) == "ref_5341dec448cc.json"


def test_distinct_ids_distinct_names_including_whitespace_and_case():
    ids = [
        "TRANSFORM.Blocks.Examples.PulseSine_Test",
        "TRANSFORM.Blocks.Examples.PulseSine_Test ",
        " TRANSFORM.Blocks.Examples.PulseSine_Test",
        "TRANSFORM.Blocks.Examples.pulseSine_Test",
        "TRANSFORM.Blocks.Examples.EasingRamp_Test",
    ]
    assert len({baseline_file(m) for m in ids}) == len(ids)


def test_non_ascii_id_is_hashed_as_utf8():
    assert baseline_code("Bibliothek.Wärmeübertrager_Test") == "5f9e96781b58"


def test_stem_names_the_role_folders_and_the_report_dir():
    hexe = "TRANSFORM.Media.IdealGases.Examples.HeXeAlternate"
    assert baseline_stem(hexe) == "ref_121677875640"
