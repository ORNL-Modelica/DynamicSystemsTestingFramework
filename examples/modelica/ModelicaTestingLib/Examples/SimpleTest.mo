within ModelicaTestingLib.Examples;
model SimpleTest "Simple dynamics with two state variables"

  Real x(start=0, fixed=true) "Linear ramp";
  Real y(start=1, fixed=true) "Exponential decay";

  Components.UnitTests unitTests(
    n=2,
    x={x, y});

equation
  der(x) = 1;
  der(y) = -y;

  annotation (experiment(
    StopTime=10,
    Tolerance=1e-6,
    __Dymola_Algorithm="Dassl"));
end SimpleTest;
