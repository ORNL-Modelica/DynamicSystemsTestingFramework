within ModelicaTestingLib.Examples;
model IntervalTest "Model using output Interval instead of NumberOfIntervals"

  Real x(start=0, fixed=true) "Linear ramp";

  Components.UnitTests unitTests(
    n=1,
    x={x});

equation
  der(x) = 1;

  annotation (experiment(
    StopTime=10,
    Interval=0.5,
    Tolerance=1e-6,
    __Dymola_Algorithm="Dassl"));
end IntervalTest;
