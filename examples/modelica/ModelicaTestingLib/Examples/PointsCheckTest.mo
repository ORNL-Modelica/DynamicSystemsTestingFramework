within ModelicaTestingLib.Examples;
model PointsCheckTest "Sinusoid sampled at declared checkpoints — demonstrates points comparison mode"

  Real x "Sinusoidal signal sampled at declared checkpoints";

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
Sinusoidal signal scored against a list of declared checkpoints to
exercise the framework's <b>points</b> comparison mode (D84). The
checkpoints in <code>test_spec.json</code> exercise every per-point
knob:
</p>
<ul>
  <li>Implicit reference-relative target at the initial sample (t=0)
      with a tight absolute y-tolerance.</li>
  <li>Explicit absolute peak target (t=&pi;/2, value=1.0) with a
      relative y-tolerance.</li>
  <li>Reference-relative zero crossing (t=&pi;) with a non-zero
      <code>time_tolerance</code> — exercises the x+y box check that
      tolerates small solver-timing drift.</li>
  <li>Explicit absolute trough (t=3&pi;/2, value=-1.0) with relative
      y-tolerance.</li>
  <li>Reference-relative endpoint (t=10) with a tight absolute
      tolerance.</li>
</ul>
<p>
Open the interactive report to see the diamond markers + tolerance
boxes per point and the table editor (📸 Snapshot from ref + add /
delete rows).
</p>
</html>"));
end PointsCheckTest;
