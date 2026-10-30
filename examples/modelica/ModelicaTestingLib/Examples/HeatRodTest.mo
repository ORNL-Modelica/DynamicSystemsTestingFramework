within ModelicaTestingLib.Examples;
model HeatRodTest "Heat conduction along a rod — an array state for array-family criteria"

  parameter Integer n=20 "Number of nodes along the rod";
  parameter Real a=0.01 "Thermal diffusivity (m2/s)";
  parameter Real L=1 "Length of the rod (m)";
  parameter Real T_hot=100 "Temperature held at the left end (degC)";
  parameter Real T_cold=20 "Temperature held at the right end, and of the rod at t=0 (degC)";
  final parameter Real dx=L/n "Node spacing (m)";

  Real T[n](each start=T_cold, each fixed=true) "Node temperatures, left to right (degC)";

equation
  der(T[1]) = a*(2*T_hot - 3*T[1] + T[2])/dx^2;
  for i in 2:n - 1 loop
    der(T[i]) = a*(T[i - 1] - 2*T[i] + T[i + 1])/dx^2;
  end for;
  der(T[n]) = a*(T[n - 1] - 3*T[n] + 2*T_cold)/dx^2;

  annotation (
    experiment(
      StopTime=50,
      Interval=0.5,
      Tolerance=1e-6,
      __Dymola_Algorithm="Dassl"),
    Documentation(info="<html>
<p>
One-dimensional heat conduction along a rod of twenty nodes: the left end is
held at 100&nbsp;degC, the right end at 20&nbsp;degC, and the rod starts at
20&nbsp;degC throughout. The node temperatures <code>T[1]</code> &hellip;
<code>T[20]</code> are one array of twenty, so this is the model to try a
criterion over an <b>array family</b> on.
</p>
<p>
The model has no <code>UnitTests</code> component: its entry in
<code>test_spec.json</code> declares it, tracks <code>T[*]</code>, and scores
it with one <b>tube</b> criterion per element &mdash; what the Indices field
of <code>dstf author</code>'s add form writes for <code>1:20</code> &mdash;
plus a criterion of its own on the middle node. Under the array's plot the
twenty identical criteria fold into one row, <code>tube &middot; T[1:20]</code>,
whose editor changes them all.
</p>
</html>"));
end HeatRodTest;
