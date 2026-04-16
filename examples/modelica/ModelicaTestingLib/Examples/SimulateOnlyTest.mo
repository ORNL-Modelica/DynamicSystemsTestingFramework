within ModelicaTestingLib.Examples;
model SimulateOnlyTest "Pass iff this model simulates — no comparison"
  extends ModelicaTestingLib.Icons.Example;

  Real x(start=0, fixed=true) "Trivial ramp";

equation
  der(x) = 1;

  annotation (
    experiment(StopTime=5, Tolerance=1e-4),
    Documentation(info="<html>
<p>
Exercises the PTA demo recognizer (see
<code>Resources/ReferenceResults/testing.json</code>): any class that extends
<code>ModelicaTestingLib.Icons.Example</code> is discovered as a simulate-only
test. No <code>UnitTests</code> component, no reference baseline — the test
passes if the simulation completes.
</p>
</html>"));
end SimulateOnlyTest;
