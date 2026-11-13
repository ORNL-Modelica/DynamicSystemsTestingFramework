"""`mo_parser.declared_classes`: every class a Modelica text declares, by its
name qualified within the text — what `dstf uses` reads of the file a
simulator writes of a model and everything it uses."""

import re
from pathlib import Path

import pytest

from dstf.discovery.mo_parser import UnclosedClass, declared_classes

FIXTURES = Path(__file__).parent / "fixtures" / "uses"

HAND = """
within ;
package Lib "the library: model Fake end Fake;"
  type Temp = Real(unit="K") "a type";
  type E = enumeration(a "model InEnum", b);
  package A
    package B
      model C "three deep"
        Real x;
      algorithm
        if x > 0 then x := 1; end if;
        for i in 1:2 loop x := x; end for;
        while x > 2 loop x := x - 1; end while;
      end C;
    end B;
    model Short = B.C(x(start=1)) "a short model";
    function f = g;
    connector RealIn = input Real;
    package ShortPkg = B "a short package";
    record ShortRec = Rec(r = 1);
    block ShortBlock = Blk;
    block Blk end Blk;
    record Rec Real r; end Rec;
    class Cls end Cls;
    partial model P end P;
    encapsulated package Enc end Enc;
    expandable connector Bus end Bus;
    pure function Pure input Real u; output Real y; algorithm y := u; end Pure;
    model extends Base
    end Base;
    model 'a b' "quoted"
    end 'a b';
    model User
      B.C c(redeclare model X = Y);
      String s = "model InString end InString;"; // model InComment
      /* package InBlock end InBlock; */
      replaceable model HT = Ideal;
    end User;
    function g input Real u; output Real y; algorithm y := u; end g;
  end A;
end Lib;
model Lib_A_B_C
  extends Lib.A.B.C;
end Lib_A_B_C;
"""


def test_every_class_a_hand_written_text_declares_in_source_order():
    assert declared_classes(HAND) == [
        "Lib",
        "Lib.Temp",
        "Lib.E",
        "Lib.A",
        "Lib.A.B",
        "Lib.A.B.C",
        "Lib.A.Short",
        "Lib.A.f",
        "Lib.A.RealIn",
        "Lib.A.ShortPkg",
        "Lib.A.ShortRec",
        "Lib.A.ShortBlock",
        "Lib.A.Blk",
        "Lib.A.Rec",
        "Lib.A.Cls",
        "Lib.A.P",
        "Lib.A.Enc",
        "Lib.A.Bus",
        "Lib.A.Pure",
        "Lib.A.Base",
        "Lib.A.'a b'",
        "Lib.A.User",
        "Lib.A.User.HT",
        "Lib.A.g",
        "Lib_A_B_C",
    ]


#: An operator record, its operators and a type as the tools print them
#: (the Modelica Standard Library's own spelling): an ``operator`` holds a
#: function and ends by its quoted name, which closes nothing else.
OPERATORS = """
package Units
  package SI
    type Temperature = Real(final quantity = "ThermodynamicTemperature", final unit = "K", min = 0.0, displayUnit = "degC") "Absolute temperature (use type TemperatureDifference for relative temperatures)" annotation(absoluteValue = true);
    type Angle = Real(final quantity = "Angle", final unit = "rad", displayUnit = "deg");
  end SI;
end Units;
operator record Complex "Complex number with overloaded operators"
  replaceable Real re "Real part of complex number";
  replaceable Real im "Imaginary part of complex number";

  encapsulated operator 'constructor' "Constructor"
    function fromReal "Construct Complex from Real"
      import Complex;
      input Real re "Real part of complex number";
      input Real im = 0 "Imaginary part of complex number";
      output Complex result(re = re, im = im) "Complex number";
    algorithm
      annotation(Inline = true);
    end fromReal;
  end 'constructor';

  encapsulated operator function '+' "Add two complex numbers"
    import Complex;
    input Complex c1 "Complex number 1";
    input Complex c2 "Complex number 2";
    output Complex c3 "= c1 + c2";
  algorithm
    c3 := Complex(c1.re + c2.re, c1.im + c2.im);
    annotation(Inline = true);
  end '+';
  annotation(Protection(access = Access.hide), version = "4.1.0");
end Complex;
model After
  Complex z;
end After;
"""


def test_types_and_operators_as_the_tools_print_them():
    assert declared_classes(OPERATORS) == [
        "Units",
        "Units.SI",
        "Units.SI.Temperature",
        "Units.SI.Angle",
        "Complex",
        "Complex.'constructor'",
        "Complex.'constructor'.fromReal",
        "Complex.'+'",
        "After",
    ]


LIBRARY = [
    "ModelicaTestingLib",
    "ModelicaTestingLib.Examples",
    "ModelicaTestingLib.Examples.SimpleTest",
    "ModelicaTestingLib.Components",
    "ModelicaTestingLib.Components.UnitTests",
]


@pytest.mark.parametrize(
    ("file", "expected"),
    [
        # Dymola: the library pruned to what the model uses, then its wrapper
        ("dymola_SimpleTest.mo", [*LIBRARY, "ModelicaTestingLib_Examples_SimpleTest"]),
        # omc: the same, with an emptied `Icons` package of the library, the
        # Modelica Standard Library's icons the library extends, and its
        # wrapper last
        (
            "openmodelica_SimpleTest.mo",
            [
                *LIBRARY,
                "ModelicaTestingLib.Icons",
                "Modelica",
                "Modelica.Icons",
                "Modelica.Icons.ExamplesPackage",
                "Modelica.Icons.Package",
                "Modelica.Icons.IconsPackage",
                "SimpleTest_total",
            ],
        ),
    ],
)
def test_the_files_the_real_tools_write(file, expected):
    text = (FIXTURES / file).read_text(encoding="utf-8")
    assert declared_classes(text) == expected


def test_a_class_keyword_inside_a_quoted_name_declares_nothing():
    """`Real 'a model b'` is a component: read as `model b` it opened a
    class that never closed, and the whole file read as no class at all."""
    text = """package Lib
  model T
    Real 'a model b' = 1;
    Real 'end T;' = 2;
    Lib.U u;
  end T;
  model U end U;
end Lib;
model T_total
  extends Lib.T;
end T_total;
"""
    assert declared_classes(text) == ["Lib", "Lib.T", "Lib.U", "T_total"]


@pytest.mark.parametrize(
    ("text", "inside"),
    [
        ("package Lib\n  model T\n    Real x;\n  end T;\n", "Lib"),
        ("package Lib\n  model T\n    Real x;\n", "Lib.T"),
    ],
)
def test_a_text_that_ends_inside_a_class_is_refused(text, inside):
    """A file cut off is no list of what the model uses: an empty or short
    list would say the test uses nothing."""
    with pytest.raises(UnclosedClass, match=rf"ends inside class {re.escape(inside)}$"):
        declared_classes(text)
