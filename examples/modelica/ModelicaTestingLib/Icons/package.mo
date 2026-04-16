within ModelicaTestingLib;
package Icons "Icon-only base classes for tests"
  extends Modelica.Icons.IconsPackage;

  annotation (Documentation(info="<html>
<p>
Lightweight icon classes used as base classes for example/test models.
The PTA demo recognizer in <code>Resources/ReferenceResults/testing.json</code>
finds models that extend <code>ModelicaTestingLib.Icons.Example</code> and
treats them as simulate-only tests (pass iff the model simulates).
</p>
</html>"));
end Icons;
