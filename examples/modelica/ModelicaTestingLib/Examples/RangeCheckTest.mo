within ModelicaTestingLib.Examples;
model RangeCheckTest "Bounded sinusoid — demonstrates range comparison mode"

  Real x "Signal always within [-1, 1]";

  Components.UnitTests unitTests(
    n=1,
    x={x});

equation
  x = sin(time);

  annotation (
    experiment(
      StopTime=10,
      Tolerance=1e-6,
      __Dymola_Algorithm="Dassl"),
    Documentation(info="<html>
<p>
Bounded sinusoidal signal used to showcase the framework's <b>range</b>
comparison mode. The declared bounds (<i>[-1.05, 1.05]</i> in
<code>test_spec.json</code>) are spec-sourced — they don't come from the
baseline — so range is the one metric that scores without a reference
signal. The per-variable panel shows the editable min/max inputs; the
trajectory plot renders the bounds as dashed red horizontal lines.
</p>
</html>"));
end RangeCheckTest;
