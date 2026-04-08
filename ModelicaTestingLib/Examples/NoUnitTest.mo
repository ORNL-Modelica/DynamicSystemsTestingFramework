within ModelicaTestingLib.Examples;
model NoUnitTest "Model without UnitTests — tested via test_spec.json"

  Real x(start=0, fixed=true) "Simple ramp";

equation
  der(x) = 1;

  annotation (experiment(
    StopTime=5,
    Tolerance=1e-4));
end NoUnitTest;
