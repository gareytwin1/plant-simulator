import pytest

from app.engine.rng import SeededRNG


# Determinism is a bit-identical requirement, so these compare with == on
# purpose: an approximate match would hide exactly the drift being guarded.


def draw(rng, count=50):
    return [rng.random() for _ in range(count)]


def test_same_seed_gives_identical_sequence():
    assert draw(SeededRNG(42)) == draw(SeededRNG(42))


def test_different_seeds_give_different_sequences():
    assert draw(SeededRNG(42)) != draw(SeededRNG(123))


def test_every_draw_method_is_deterministic():
    def everything(rng):
        items = list(range(10))
        rng.shuffle(items)

        return (
            rng.random(),
            rng.uniform(-5.0, 5.0),
            rng.gauss(0.0, 1.0),
            rng.randint(1, 1000),
            rng.choice(["a", "b", "c", "d"]),
            items,
        )

    assert everything(SeededRNG(7)) == everything(SeededRNG(7))


def test_reseed_restarts_the_sequence():
    rng = SeededRNG(42)
    first = draw(rng)

    rng.reseed(42)

    assert draw(rng) == first


def test_reseed_records_the_new_seed():
    rng = SeededRNG(1)

    rng.reseed(2)

    assert rng.seed == 2


def test_a_seed_is_required():
    with pytest.raises(TypeError):
        SeededRNG()


def test_independent_streams_do_not_interfere():
    alone = draw(SeededRNG(1), 20)

    a = SeededRNG(1)
    b = SeededRNG(2)
    interleaved = []

    for _ in range(20):
        interleaved.append(a.random())
        b.random()
        b.gauss(0.0, 1.0)

    assert interleaved == alone


def test_drawing_from_one_stream_leaves_another_at_its_own_position():
    a = SeededRNG(1)
    b = SeededRNG(1)

    draw(a, 100)

    assert draw(b, 5) == draw(SeededRNG(1), 5)


def test_shuffle_mutates_in_place_and_keeps_the_items():
    items = list(range(20))

    SeededRNG(3).shuffle(items)

    assert items != list(range(20))
    assert sorted(items) == list(range(20))


def test_choice_returns_a_member():
    options = ["x", "y", "z"]

    assert SeededRNG(5).choice(options) in options
