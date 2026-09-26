"""
Instruments: how the plant's true state becomes what the operator reads (T13-2).

The physics never knows it is being measured. A device's `get_state()`, a
node pressure and a stream flow are all true values, and they stay true
whatever an instrument does. An `Instrument` sits between that truth and the
snapshot: it reads one point - a section, a row in it and a field in the row,
such as `("nodes", "N-103", "pressure")` - and reports an indicated value for
it. `indicate()` applies every instrument to the true sections at once, and
the engine publishes the result as the snapshot's measured sections, with the
truth kept behind `Snapshot.truth` (see app/engine/snapshot.py).

A point with no instrument on it indicates its true value exactly. So does a
healthy instrument: `bias` is zero until something moves it, and a zero bias
returns the true value bit for bit rather than adding zero to it. That is
what leaves every plant without a fault - and every golden trace - reading
what it read before the split existed.

`bias` is the parameter an instrument fault writes. It is a design parameter
an engineer could change, so a malfunction may write it (C8), and a drifting
transmitter is that malfunction with a ramp profile. It is additive, in the
unit of the value it biases. An instrument has no slow state: its indication
is a pure function of the true value and the bias, so moving the bias
changes the very next snapshot and moves nothing in the plant.

Instruments measure numbers. An instrument on a flag or a label - a valve's
`signal_ok`, a stream's `composition` - is refused, because there is nothing
for a bias to be added to.
"""

import copy
import math
from collections.abc import Iterable, Mapping

from app.engine.snapshot import MEASURED_SECTIONS, SectionInput
from app.statetypes import JSONValue


Point = tuple[str, str, str]


class Instrument:
    def __init__(
        self,
        tag: str,
        section: str,
        source: str,
        variable: str,
        bias: float = 0.0,
    ) -> None:
        if section not in MEASURED_SECTIONS:
            raise ValueError(
                f"instrument {tag} reads section {section!r}; an instrument "
                f"reads one of {MEASURED_SECTIONS}",
            )

        self.tag = tag
        self.section = section
        self.source = source
        self.variable = variable
        self.bias = bias

    @property
    def point(self) -> Point:
        return (self.section, self.source, self.variable)

    @property
    def bias(self) -> float:
        return self._bias

    @bias.setter
    def bias(self, value: float) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(
                f"instrument {self.tag} bias must be a finite number, got {value!r}",
            )

        self._bias = float(value)

    def indicate(self, true_value: float) -> float:
        if self._bias == 0.0:
            return true_value

        return true_value + self._bias

    def __repr__(self) -> str:
        section, source, variable = self.point

        return f"<Instrument {self.tag} {section}.{source}.{variable} bias={self._bias}>"


def indicate(
    truth: Mapping[str, SectionInput],
    instruments: Iterable[Instrument],
) -> dict[str, dict[str, dict[str, JSONValue]]]:
    """The indicated view of `truth`: a deep copy with every instrument applied.

    Raises ValueError if an instrument reads a point `truth` does not have,
    or one that is not a number.
    """
    indicated = {
        name: {tag: dict(copy.deepcopy(row)) for tag, row in truth[name].items()}
        for name in MEASURED_SECTIONS
    }

    for instrument in instruments:
        _, source, variable = instrument.point
        row = indicated[instrument.section][source]
        row[variable] = instrument.indicate(true_reading(instrument, truth))

    return indicated


def true_reading(instrument: Instrument, truth: Mapping[str, SectionInput]) -> float:
    """The true value `instrument` reads in `truth`, or ValueError if it can read none."""
    section, source, variable = instrument.point
    row = truth[section].get(source)

    if row is None or variable not in row:
        raise ValueError(
            f"instrument {instrument.tag} reads {section}.{source}.{variable}, "
            f"which the plant does not publish",
        )

    value = row[variable]

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"instrument {instrument.tag} reads {section}.{source}.{variable}, "
            f"which is {value!r}, not a number",
        )

    return value
