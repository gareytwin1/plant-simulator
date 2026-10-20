"""Free-play pin shared by tests/conftest.py and the tests that patch per test."""

from app import config


def pin_olefins_free_play(patch):
    """Point free play at olefins_lite, at normal_operation, through `patch`."""
    patch.setattr(config, "FREE_PLAY_PLANT", "olefins_lite")
    patch.setattr(config, "FREE_PLAY_CONDITION", "normal_operation")
