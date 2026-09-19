"""
The JSON-safe types every get_state() returns.

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
