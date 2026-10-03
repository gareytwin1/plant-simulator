"""
The JSON-safe types every get_state() returns, and the StateError a
checkpoint refusal raises.

A device (C1), a topology object (C2) and the clock all publish the same
kind of thing: a flat row of JSON-safe values that ends up inside a
Snapshot (C4). Naming that row once keeps those signatures identical,
which matters more than it looks — a dict return type is invariant, so a
subclass that narrowed its row to dict[str, float] would not be a valid
override of a base returning dict[str, JSONValue].

Nothing here imports from app, so any layer may depend on it.
"""

JSONValue = (
    bool
    | int
    | float
    | str
    | None
    | list["JSONValue"]
    | dict[str, "JSONValue"]
)

StateRow = dict[str, JSONValue]


class StateError(ValueError):
    """A save that cannot be restored, or a state that cannot be saved.

    `path` names the refused field relative to whoever raised it - a class
    validating its own checkpoint gives a path inside that checkpoint, or
    "" for a check across its fields - and `within()` prefixes it with the
    path to that object, so the caller that knows where it sits in a save
    adds the rest."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}" if path else reason)
        self.path = path
        self.reason = reason

    def within(self, prefix: str) -> "StateError":
        return StateError(f"{prefix}.{self.path}" if self.path else prefix, self.reason)
