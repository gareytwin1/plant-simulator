"""
Trend points (T17-3) - which values of a snapshot a trend may draw.

A point is `<id>.<field>`, the key the snapshot's envelope section uses, so a
band can be overlaid on its pen by key. Points are read from the operator view
(`app.api.visibility.operator_view`), never the raw snapshot, so a trend cannot
show a field `VISIBLE` hides.

`TREND_FIELDS` is an explicit allowlist in the `VISIBLE` style: a section not
listed here draws nothing. The `equipment` section is already cut by the
operator view, so every numeric field it still carries is a pen (`running` is
a bool and is not). A controller's tuning and output limits are configuration,
not process, and stay off.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.statetypes import JSONValue


# None: every numeric field the section's row carries.
TREND_FIELDS: dict[str, frozenset[str] | None] = {
    "equipment": None,
    "nodes": frozenset({"pressure"}),
    "streams": frozenset({"flow"}),
    "controllers": frozenset({"pv", "sp", "out"}),
}


def trend_values(view: Mapping[str, JSONValue]) -> dict[str, float]:
    """Every trend point of an operator view, with its value. NaN is kept: it
    is a reading, and the route is what writes it as null."""
    values: dict[str, float] = {}

    for section, fields in TREND_FIELDS.items():
        rows = view.get(section)

        if not isinstance(rows, dict):
            continue

        for row_id, row in rows.items():
            if not isinstance(row, dict):
                continue

            for name, value in row.items():
                if fields is not None and name not in fields:
                    continue

                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue

                point = f"{row_id}.{name}"

                if point in values:
                    raise ValueError(f"trend point {point} is published by two sections")

                values[point] = float(value)

    return values
