within ModelicaTestingLib.Examples;
model FrequencyTest "1 Hz sinusoid — demonstrates dominant-frequency comparison mode"

  Real x "1 Hz sine signal";

  Components.UnitTests unitTests(
    n=1,
    x={x});

equation
  // 2*pi expressed as a literal so the model doesn't require MSL at parse time
  x = sin(6.283185307179586*time);

  annotation (
    experiment(
      StopTime=5,
      Tolerance=1e-6,
      __Dymola_Algorithm="Dassl"),
    Documentation(info="<html>
<p>
Pure 1 Hz sinusoid over 5 seconds (5 full cycles) used to showcase the
framework's <b>dominant-frequency</b> comparison mode. The metric runs an FFT
on both reference and simulation trajectories, extracts each peak bin, and
scores on relative error between the two peak frequencies. For a
self-regression the peak lands on the same bin and the test passes at 0%
relative error. The per-variable panel in the interactive report shows the
CLI-authoritative badge since live recompute isn't implemented for this mode.
</p>
</html>"));
end FrequencyTest;
