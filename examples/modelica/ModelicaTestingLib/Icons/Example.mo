within ModelicaTestingLib.Icons;
partial class Example "Marker for runnable example/test models"

  annotation (Documentation(info="<html>
<p>
Extending this class marks a model as an example. The PTA demo recognizer
(see <code>testing.json</code>) discovers any class that
<code>extends ModelicaTestingLib.Icons.Example</code> and treats it as a
simulate-only test — the test passes iff the model simulates without error,
no per-variable comparison.
</p>
</html>"));
end Example;
