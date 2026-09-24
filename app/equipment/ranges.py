"""Numeric range check shared by device design-value property setters.

Lifted out of `vessel.py`, where it originated, so `compressor.py` and
`pump.py` can enforce their own design ranges (R3) without each device
restating the same finite/bool/bounds checks.
"""

import math


def checked(
    tag: str,
    name: str,
    value: float,
    lowest: float,
    highest: float | None = None,
    above: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{tag}.{name} must be a number, got {type(value).__name__} "
            f"{value!r}",
        )

    number = float(value)

    if not math.isfinite(number):
        raise ValueError(
            f"{tag}.{name} must be finite, got {value!r}",
        )

    floor = f"above {lowest}" if above else f"at least {lowest}"
    ceiling = "" if highest is None else f" and at most {highest}"

    if number < lowest or (above and number == lowest):
        raise ValueError(f"{tag}.{name} must be {floor}{ceiling}, got {value!r}")

    if highest is not None and number > highest:
        raise ValueError(f"{tag}.{name} must be {floor}{ceiling}, got {value!r}")

    return number
