"""
Equipment registry — the single place a tag resolves to a device.

Config, alarms, trends, scoring and the console all need to refer to a
device by name rather than by holding a reference to the object itself, so
a tag ("K-101", "P-101") is the one handle that stays valid across the API,
the scenario layer and the plant configuration.

Tags follow ISA-style equipment codes — a letter prefix for the kind of
equipment, a dash, then a number: K compressors, P pumps, E heat
exchangers, V vessels, FV control valves. See app.config.TAG_PREFIXES for
the full table. The registry itself does not enforce the convention; it
only guarantees a tag maps to exactly one device.
"""


class EquipmentRegistry:
    def __init__(self):
        self._devices = {}

    def register(self, device):
        if device.tag in self._devices:
            raise ValueError(
                f"tag {device.tag!r} is already registered to "
                f"{self._devices[device.tag]!r}",
            )

        self._devices[device.tag] = device

        return device

    def resolve(self, tag):
        if tag not in self._devices:
            raise KeyError(
                f"no device registered under tag {tag!r}, only {sorted(self._devices)}",
            )

        return self._devices[tag]
