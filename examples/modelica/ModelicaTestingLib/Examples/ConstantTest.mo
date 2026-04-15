within ModelicaTestingLib.Examples;
model ConstantTest "Model with constant outputs (zero signal range)"

  Real x(start=5, fixed=true) "Constant value";
  Real y(start=0, fixed=true) "Near-zero constant";

  Components.UnitTests unitTests(
    n=2,
    x={x, y});

equation
  der(x) = 0;
  der(y) = 0;

  annotation (experiment(
    StopTime=10,
    Tolerance=1e-6,
    __Dymola_Algorithm="Dassl"));
end ConstantTest;
