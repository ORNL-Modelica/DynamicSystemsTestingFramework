package ModelicaTestingLib "Library for regression testing Modelica models"
  extends Modelica.Icons.Package;

  package Examples "Example models demonstrating regression testing patterns"
    extends Modelica.Icons.ExamplesPackage;

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
  end Examples;

  package Components "Reusable components for regression testing"
    extends Modelica.Icons.Package;

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
  end Components;
  annotation (version="0.1.0");
end ModelicaTestingLib;
model ModelicaTestingLib_Examples_SimpleTest
 extends ModelicaTestingLib.Examples.SimpleTest;
  annotation(experiment(
  StopTime=10,
  Tolerance=1e-6,
  __Dymola_Algorithm="Dassl"),uses(ModelicaTestingLib(version="0.1.0")));
end ModelicaTestingLib_Examples_SimpleTest;
