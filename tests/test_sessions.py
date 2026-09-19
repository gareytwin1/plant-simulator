from app.engine.sessions import Session, SessionRegistry


def test_create_returns_a_session_with_fresh_equipment():
    registry = SessionRegistry()

    session = registry.create("abc")

    assert isinstance(session, Session)
    assert session.compressor.running is False
    assert session.pump.running is False


def test_get_returns_the_same_session_on_repeated_lookups():
    registry = SessionRegistry()
    created = registry.create("abc")

    assert registry.get("abc") is created
    assert registry.get("abc") is created


def test_get_unknown_id_returns_none():
    registry = SessionRegistry()

    assert registry.get("nope") is None


def test_get_or_create_creates_once_then_reuses():
    registry = SessionRegistry()

    first = registry.get_or_create("abc")
    second = registry.get_or_create("abc")

    assert first is second
    assert len(registry) == 1


def test_two_sessions_have_independent_equipment_with_no_cross_talk():
    registry = SessionRegistry()
    session_a = registry.create("a")
    session_b = registry.create("b")

    session_a.compressor.set_load_target(0.8)
    session_a.compressor.start()
    session_a.compressor.step()

    assert session_a.compressor.running is True
    assert session_b.compressor.running is False
    assert session_b.compressor.load_target == 0.0
    assert session_a.compressor is not session_b.compressor
    assert session_a.pump is not session_b.pump


def test_end_releases_the_session():
    registry = SessionRegistry()
    registry.create("abc")

    registry.end("abc")

    assert registry.get("abc") is None
    assert len(registry) == 0


def test_end_unknown_id_is_a_no_op():
    registry = SessionRegistry()

    registry.end("nope")

    assert len(registry) == 0
