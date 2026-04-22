within ModelicaTestingLib.Examples;
model MetricTreeTest "Two signals — demonstrates AND + warn combinator tree"

  Real x "Sine component";
  Real y "Cosine component";

  Components.UnitTests unitTests(
    n=2,
    x={x, y});

equation
  x = sin(time);
  y = cos(time);

  annotation (
    experiment(
      StopTime=10,
      Tolerance=1e-6,
      __Dymola_Algorithm="Dassl"),
    Documentation(info="<html>
<p>
Two correlated signals used to showcase an explicit <b>MetricTree</b> in
<code>test_spec.json</code>. The authored tree is
<pre>
AND
  NRMSE(x)           &lt;-- primary, hard-fail
  WARN
    NRMSE(y)         &lt;-- soft-fail (warn-wrapped)
</pre>
The <code>x</code> leaf is the primary regression anchor; the
<code>y</code> leaf is warn-wrapped so a regression on cosine reports
yellow but doesn't turn the test red. Open the interactive report to see
the tree rendered at the top of the page plus per-variable mounts in each
row.
</p>
</html>"));
end MetricTreeTest;
