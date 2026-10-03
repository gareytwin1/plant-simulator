"""
Configuration version (T18-5) - one semantic version over everything in config/.

MAJOR changes when anything under config/ can move the result of the same
operator actions (a plant, scenario, sequence, initial condition, malfunction,
scoring weights or a schema's meaning): results recorded under different
majors are not comparable. MINOR is additive and leaves existing results
alone (a new scenario or plant file). PATCH changes no behaviour.

This is a leaf module: it imports nothing from app, so any loader or store can
use it. tests/test_config_version_guard.py fails when a file under config/
changes without VERSION being bumped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CONFIG_VERSION_PATH = Path(__file__).resolve().parent.parent / "config" / "VERSION"

_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


class ConfigVersionError(ValueError):
    """The version text is malformed or the VERSION file is missing."""


@dataclass(frozen=True, order=True)
class ConfigVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> ConfigVersion:
        match = _PATTERN.fullmatch(text.strip())

        if match is None:
            raise ConfigVersionError(
                f"config version must be MAJOR.MINOR.PATCH, got {text.strip()!r}"
            )

        major, minor, patch = (int(group) for group in match.groups())

        return cls(major, minor, patch)

    def comparable_with(self, other: ConfigVersion) -> bool:
        return self.major == other.major

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def read_config_version(path: Path | str = CONFIG_VERSION_PATH) -> ConfigVersion:
    try:
        text = Path(path).read_text()
    except OSError as error:
        raise ConfigVersionError(f"cannot read config version file {path}: {error}") from error

    return ConfigVersion.parse(text)
