within ModelicaTestingLib.Examples;
model EventTest "Model with discrete events producing duplicate time points"

  Real x(start=0, fixed=true) "Continuous ramp";
  Real y(start=0, fixed=true) "Stepped output with events";

  Components.UnitTests unitTests(
    n=2,
    x={x, y});

equation
  der(x) = 1;

  when x > 2 then
    y = 1;
  elsewhen x > 5 then
    y = 2;
  elsewhen x > 8 then
    y = 3;
  end when;

  annotation (experiment(
    StopTime=10,
    Tolerance=1e-6,
    __Dymola_Algorithm="Dassl"));
end EventTest;
