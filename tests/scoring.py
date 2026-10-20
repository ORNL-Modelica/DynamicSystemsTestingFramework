"""Score one variable the way a criterion is scored: the override's mode,
built by ``resolve_mode``, compares the two trajectories. Tests write the
override in a criterion's own keys (``{"mode": "tube", "tube_abs": 0.1}``)."""

from dstf.comparison.modes import resolve_mode
from dstf.comparison.types import VariableComparison


def score(
    override, ref_time, ref_values, act_time, act_values, tolerance=1e-4
) -> VariableComparison:
    """The mode ``override`` names, with ``tolerance`` as the base tolerance,
    scoring the actual trajectory against the reference."""
    return resolve_mode(override, tolerance).compare(
        ref_time, ref_values, act_time, act_values
    )
