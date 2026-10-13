within ModelicaTestingLib.Examples;
model MetricTreeTest
  "Step response — demonstrates windowed leaves + a multi-baseline tree"

  Real x(start=0, fixed=true) "First-order step response: transient, then steady state";
  Real y(start=0, fixed=true) "Downstream lag of x";

  Components.UnitTests unitTests(
    n=2,
    x={x, y});

equation
  der(x) = (1 - x)/0.5;
  der(y) = (x - y)/1.0;

  annotation (
    experiment(
      StopTime=10,
      Tolerance=1e-6,
      __Dymola_Algorithm="Dassl"),
    Documentation(info="<html>
<p>
A first-order step response (time constant 0.5&nbsp;s, settled after
&asymp;2.5&nbsp;s) feeding a slower downstream lag. Showcases an explicit
<b>MetricTree</b> in <code>test_spec.json</code> that scores different
<i>phases</i> of the same trajectory with different criteria:
</p>
<pre>
AND
  NRMSE(x, window [0, 2.5])     &lt;-- start-up transient, primary, hard-fail
  TUBE(x, window [2.5, 10])     &lt;-- steady state, primary, hard-fail
  WARN
    NRMSE(x, against=openmodelica)  &lt;-- cross-simulator soft_check, advisory
  RANGE(y, max 1)               &lt;-- the lag never overshoots, baseline-free, hard-fail
</pre>
<p>
The two windowed leaves are the regression anchor against the primary
baseline; the warn-wrapped leaf compares the same trajectory against an
imported OpenModelica <code>soft_check</code> baseline, so cross-simulator
disagreement is visible in the report without failing the test. The last
leaf scores the downstream lag against no baseline at all: <code>y</code>
rises from 0 towards 1 and never passes it, whatever the two time
constants. Open the interactive report to see the tree, the window
brackets, the tube band and the bound rendered on the plots.
</p>
</html>"));
end MetricTreeTest;
