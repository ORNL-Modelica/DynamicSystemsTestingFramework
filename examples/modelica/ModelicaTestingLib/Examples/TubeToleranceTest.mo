within ModelicaTestingLib.Examples;
model TubeToleranceTest "Exponential approach — demonstrates tube comparison mode"

  Real x(start=0, fixed=true) "Exponential approach to 1";

  Components.UnitTests unitTests(
    n=1,
    x={x});

equation
  der(x) = 1 - x;

  annotation (
    experiment(
      StopTime=10,
      Tolerance=1e-6,
      __Dymola_Algorithm="Dassl"),
    Documentation(info="<html>
<p>
First-order exponential approach used to showcase the framework's <b>tube</b>
comparison mode. The reference trajectory <i>x(t) = 1 - exp(-t)</i> is compared
against a rel-width tube (5% of |reference|) around itself — the simulation
passes so long as every point stays inside that band. Open the interactive
report to see the tube editor; Shift+click on the trajectory plot to add
control points.
</p>
</html>"));
end TubeToleranceTest;
