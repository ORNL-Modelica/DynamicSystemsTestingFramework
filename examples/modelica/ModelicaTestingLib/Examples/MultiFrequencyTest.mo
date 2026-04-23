within ModelicaTestingLib.Examples;
model MultiFrequencyTest "Composite 1/3/7 Hz signal — exercises n_peaks multi-peak FFT comparison"

  constant Real twoPi = 6.283185307179586 "2*pi literal (avoids parse-time MSL dep)";
  Real y "composite signal (3 sinusoids at 1, 3, 7 Hz with distinct amplitudes)";

  Components.UnitTests unitTests(
    n=1,
    x={y});

equation
  // Distinct amplitudes (3, 2, 1) so amplitude-rank peak detection picks all
  // three cleanly before the frequency-sort stage pairs them for comparison.
  y = 3.0 * sin(1.0 * twoPi * time)
    + 2.0 * sin(3.0 * twoPi * time)
    + 1.0 * sin(7.0 * twoPi * time);

  annotation (
    experiment(
      StopTime=4,
      Tolerance=1e-6,
      __Dymola_Algorithm="Dassl"),
    Documentation(info="<html>
<p>
Composite signal summing 1 Hz, 3 Hz, and 7 Hz sinusoids with amplitudes
3, 2, 1 respectively. Simulation runs 4 seconds (4 full cycles at the
lowest frequency; 28 at the highest) — enough samples for a clean FFT
across the three tracked peaks.
</p>
<p>
Configured with <b>n_peaks=3</b> in <code>test_spec.json</code>; the
framework's multi-peak dominant-frequency algorithm detects the
top-3 local maxima by amplitude (filtering spectral noise), sorts them
by frequency for predictable pairing between reference and actual, and
fails iff any paired peak's relative error exceeds the declared
tolerance. Distinct amplitudes are intentional — they make the
amplitude-rank filter unambiguous against any future noise-robustness
variants of this test.
</p>
<p>
Activate the leaf in the interactive report to see the spectrum subplot:
reference (blue) vs actual (red, dotted), peak markers, and shaded
±relative-tolerance acceptance bands around each reference peak.
</p>
</html>"));
end MultiFrequencyTest;
