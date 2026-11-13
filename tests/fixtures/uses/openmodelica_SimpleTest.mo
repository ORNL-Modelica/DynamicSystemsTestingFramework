package ModelicaTestingLib "Library for regression testing Modelica models"
  package Examples "Example models demonstrating regression testing patterns"
    model SimpleTest "Simple dynamics with two state variables"
      Real x(start = 0, fixed = true) "Linear ramp";
      Real y(start = 1, fixed = true) "Exponential decay";
      Components.UnitTests unitTests(n = 2, x = {x, y});
    equation
      der(x) = 1;
      der(y) = -y;
      annotation(experiment(StopTime = 10, Tolerance = 1e-6, __Dymola_Algorithm = "Dassl"));
    end SimpleTest;
  end Examples;

  package Components "Reusable components for regression testing"
    model UnitTests "Track variables for regression testing"
      parameter Integer n = 0 "Number of tracked variables";
      input Real x[n] "Tracked variable values";
    end UnitTests;
  end Components;

  package Icons "Icon-only base classes for tests" end Icons;
  annotation(version = "0.1.0");
end ModelicaTestingLib;

package Modelica "Modelica Standard Library"
  package Icons "Library of icons"
    partial package ExamplesPackage "Icon for packages containing runnable examples" end ExamplesPackage;

    partial package Package "Icon for standard packages" end Package;

    partial package IconsPackage "Icon for packages containing icons" end IconsPackage;
  end Icons;
  annotation(version = "4.1.0", versionDate = "2025-05-23", dateModified = "2025-05-23 15:00:00Z");
end Modelica;

model SimpleTest_total  "Simple dynamics with two state variables"
  extends ModelicaTestingLib.Examples.SimpleTest;
 annotation(experiment(StopTime = 10, Tolerance = 1e-6, __Dymola_Algorithm = "Dassl"));
end SimpleTest_total;
