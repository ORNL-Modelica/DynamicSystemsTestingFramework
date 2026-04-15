"""Entry point for `python -m modelica_testing`.

Delegates to the same `main_entry` used by the `modelica-testing` console
script so both invocation forms share one code path.
"""

from .cli import main_entry

if __name__ == "__main__":
    main_entry()
