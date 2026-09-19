"""
Seeded random source: the only place the plant is allowed to draw a random number.

Scenario replay and repeatable scoring both rest on the determinism rule —
same config, same seed, same sequence of step(dt) calls gives bit-identical
state — and one stray `random.random()` anywhere in the models breaks it
silently. So every random draw comes from a SeededRNG instance, and
tests/test_rng.py fails the build if any other module imports `random`.

There is deliberately no module-level generator and no free `uniform()` or
`set_seed()`. A process-wide stream would be shared by every browser session
(SessionRegistry gives each its own plant), and two sessions drawing from it
would each see the other's draws. Whoever owns a plant's randomness owns an
instance and passes it to what needs it; who that owner is, is decided by the
first task that needs randomness, not here.

A seed is required. An unseeded generator is a nondeterministic one, and that
is the thing this module exists to prevent.
"""

import random
from collections.abc import MutableSequence, Sequence


class SeededRNG:
    seed: int

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._rng = random.Random(seed)

    def reseed(self, seed: int) -> None:
        self.seed = seed
        self._rng.seed(seed)

    def random(self) -> float:
        return self._rng.random()

    def uniform(self, low: float, high: float) -> float:
        return self._rng.uniform(low, high)

    def gauss(self, mu: float, sigma: float) -> float:
        return self._rng.gauss(mu, sigma)

    def randint(self, low: int, high: int) -> int:
        return self._rng.randint(low, high)

    def choice[T](self, options: Sequence[T]) -> T:
        return self._rng.choice(options)

    def shuffle[T](self, items: MutableSequence[T]) -> None:
        self._rng.shuffle(items)
