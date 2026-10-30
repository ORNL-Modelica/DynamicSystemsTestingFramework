within ModelicaTestingLib.Examples;
model ThermostatBuildingTest
  "Four thermostat-heated rooms: warm-up, cycling, a setpoint step and an open door, scored by one criteria tree"

  parameter Real C[4](each unit="J/K") = {1.08e6, 1.296e6, 0.972e6, 1.188e6}
    "Heat capacity of each room";
  parameter Real G[4](each unit="W/K") = {125, 142.857, 166.667, 111.111}
    "Heat loss of each room to outdoors";
  parameter Real Pmax[4](each unit="W") = {3000, 3200, 3500, 2800}
    "Heater power of each room";
  parameter Real Gc(unit="W/K") = 150 "Heat exchange between neighbouring rooms";
  parameter Real Gdoor(unit="W/K") = 750 "Extra loss of room 3 while its door is open";
  parameter Real Tout(unit="degC") = 5 "Outdoor temperature";
  parameter Real band(unit="K") = 0.5 "Thermostat half-band around the setpoint";

  Real T[4](each unit="degC", each start=10, each fixed=true) "Room temperatures";
  Boolean heaterOn[4](each start=true, each fixed=true) "Heater state of each room";
  Real P[4](each unit="W") "Heater power drawn by each room";
  Real Tset(unit="degC") "Setpoint: 20 degC, then 22 degC from 6 h";
  Real Qdoor(unit="W") "Loss through room 3's open door";
  Real Qc[4](each unit="W") "Heat from the neighbouring rooms";

equation
  Tset = if time < 21600 then 20 else 22;
  Qdoor = if time >= 32400 and time < 34200 then Gdoor*(T[3] - Tout) else 0;
  for i in 1:4 loop
    heaterOn[i] = T[i] < Tset - band or pre(heaterOn[i]) and not T[i] > Tset + band;
    P[i] = if heaterOn[i] then Pmax[i] else 0;
  end for;
  Qc[1] = Gc*(T[2] - T[1]);
  Qc[2] = Gc*(T[1] - T[2]) + Gc*(T[3] - T[2]);
  Qc[3] = Gc*(T[2] - T[3]) + Gc*(T[4] - T[3]);
  Qc[4] = Gc*(T[3] - T[4]);
  for i in 1:4 loop
    C[i]*der(T[i]) = P[i] - G[i]*(T[i] - Tout) + Qc[i] - (if i == 3 then Qdoor else 0);
  end for;

  annotation (
    experiment(
      StopTime=43200,
      Interval=60,
      Tolerance=1e-06,
      __Dymola_Algorithm="Dassl"),
    Documentation(info="<html>
<p>
Four rooms in a row, each with its own heater and thermostat, start at
10&nbsp;degC and exchange heat with their neighbours and with the 5&nbsp;degC
outdoors. Each thermostat holds its room within &plusmn;0.5&nbsp;degC of a
setpoint of 20&nbsp;degC, which steps to 22&nbsp;degC at 21600&nbsp;s (6&nbsp;h);
room 3's door stands open from 32400&nbsp;s to 34200&nbsp;s. The run lasts
12&nbsp;h.
</p>
<p>
One <b>MetricTree</b> in <code>test_spec.json</code> scores every phase of
the run, with every metric and every combinator the framework has:
</p>
<pre>
AND
  NRMSE(T[1], window [0, 7200])     &lt;-- the warm-up of each room follows its baseline
  NRMSE(T[2], window [0, 7200])
  NRMSE(T[3], window [0, 7200])
  NRMSE(T[4], window [0, 7200])
  OR                                &lt;-- the thermostats cycle as before, by either reading:
    EVENT-TIMING(heaterOn[1], window [9000, 21500])        &lt;-- the same switchings, at the same times
    DOMINANT-FREQUENCY(T[1], 6.35e-4 Hz, window [9000, 21500])  &lt;-- the same cycle period
  K-OF-N (3 of 4)                   &lt;-- at least three rooms stay in the 19-21 degC comfort band
    RANGE(T[1], 19..21, window [9000, 21500])
    RANGE(T[2], 19..21, window [9000, 21500])
    RANGE(T[3], 19..21, window [9000, 21500])
    RANGE(T[4], 19..21, window [9000, 21500])
  TUBE(T[1], +-0.3 degC, window [21600, 28800])  &lt;-- room 1 climbs to the new setpoint as before
  POINTS(T[1], at 22500 s and 23400 s, +-0.2)    &lt;-- two checkpoints on that climb
  SCALAR(integral of P[1], +-2 %)                &lt;-- the energy heater 1 uses over the run
  WEIGHTED (0.5, 0.3, 0.2; below 0.5)            &lt;-- the late comfort band, room 1 weighing most
    RANGE(T[1], 21.4..22.6, window [28800, 43200])
    RANGE(T[2], 21.4..22.6, window [28800, 43200])
    RANGE(T[4], 21.4..22.6, window [28800, 43200])
  WARN
    RANGE(T[3], min 12, window [32400, 37800])   &lt;-- the open door: advisory only
  WARN
    NRMSE(T[1], against=openmodelica)            &lt;-- cross-simulator soft_check, advisory
</pre>
<p>
The model has no <code>UnitTests</code> component: every variable a
criterion names is tracked. Room 2 dips a little under the late band, so
its range criterion fails inside the weighted node while the node itself
passes &mdash; the report shows one criterion red under a green test.
</p>
</html>"));
end ThermostatBuildingTest;
