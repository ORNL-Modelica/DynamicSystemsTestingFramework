within ModelicaTestingLib.Components;
model UnitTests "Track variables for regression testing"

  parameter Integer n=0 "Number of tracked variables";
  input Real x[n] "Tracked variable values" annotation(Dialog(group="Inputs"));

  annotation (
    defaultComponentName="unitTests",
    Icon(graphics={
      Rectangle(extent={{-100,-100},{100,100}}, lineColor={0,0,0}, fillColor={215,215,215}, fillPattern=FillPattern.Solid),
      Text(extent={{-80,80},{80,40}}, textString="Unit", textColor={0,0,0}),
      Text(extent={{-80,0},{80,-40}}, textString="Tests", textColor={0,0,0}),
      Text(extent={{-80,-50},{80,-80}}, textString="n=%n", textColor={0,0,0})}),
    Documentation(info="<html>
<p>
Drop this component into any model to track variables for regression testing.
The testing framework scans for this component and extracts the values of <code>x[1..n]</code>
during simulation for comparison against stored references.
</p>
<h4>Usage</h4>
<pre>
  ModelicaTestingLib.Components.UnitTests unitTests(
    n=2,
    x={pipe.T[1], tank.level});
</pre>
<p>
The variable expressions in <code>x={...}</code> are parsed by the testing tool to identify
which simulation variables to track. Any valid Modelica expression can be used.
</p>
</html>"));
end UnitTests;
