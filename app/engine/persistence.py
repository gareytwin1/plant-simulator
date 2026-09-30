"""
Plant state save and restore (T12-1).

`capture_state(engine)` reads everything a running `Engine` has integrated or
been told into one JSON-safe dict, and `restore_state(engine, state)` writes it
back onto an engine built from the same plant configuration. Stepping the
restored engine gives state identical to never having captured: this is the
fastest way to build a test fixture and to replay an upset.

The state is what the engine holds **that a configuration does not**:

  - the clock (sim_time, speed, pause);
  - every device's own attributes - slow state and the design values the
    loader applied on top of it. `Equipment` keeps both in one `__dict__`, and
    telling them apart would need a hook on C1, so a device is saved whole;
  - each domain's solved node pressures, branch streams, node temperatures,
    the transport's settled flag and the last solver result. These are solver
    outputs, but the next solve starts from them and a domain that fails to
    converge holds them, so a restore that skipped them would not be exact;
  - every loop's PID integral, derivative memory, gains, setpoint, mode and
    output, and whether the engine has primed it;
  - the demands held on the command arbiter;
  - each instrument's bias;
  - every envelope point's evaluator, excursion tracker and band history.

**Not in the state**, deliberately:

  - **RNG state.** `SeededRNG` has no save/restore and `rng.py` is spine, so
    the engine's randomness is not captured here. Nothing in `Engine` draws a
    random number yet. Who owns a plant's RNG is an open decision, not made
    here.
  - **`Equipment.reset()`.** It restores construction state and so drops the
    loader's design values (see project_state.md). Nothing here calls it or
    works around it: a restore writes the saved attributes over the device
    and does not go through `reset()`.
  - Anything the `Engine` does not own: trips, restart gates, alarms,
    malfunctions and objectives, none of which is wired into it yet.
  - The configuration itself - topology, wiring, envelope limits, loop
    bindings. A save names the plant it came from by shape (its tags, node
    ids, branch ids and loop tags) and a restore onto a different one is
    refused, rather than half-applied.

A restore is all or nothing. The whole save is validated against the engine
first - a missing or unexpected field, a wrong type, a number that is not
finite, a value a constructor, a device's property setter or the engine
refuses - and only then applied, so a refused restore leaves the engine
exactly as it was. A device attribute with no setter is checked for type and
finiteness only. Every refusal is a `StateError` naming the path to the field.

This module reads a few private attributes of `Engine`, `CommandArbiter`,
`Loop`, `PID`, `Evaluator` and `ExcursionTracker`: none has a public accessor,
and adding one is a spine or contract change this task does not make.
"""

import copy
import math
from collections.abc import Callable, Iterable, Mapping
from functools import partial
from typing import TypeGuard

from app.controls.arbitration import PRECEDENCE, CommandArbiter, Source
from app.controls.modes import Mode
from app.controls.pid import Action
from app.engine.engine import Engine
from app.engine.network import SolverResult
from app.envelope.evaluator import Limits, Severity, Side, _Band
from app.envelope.tracker import Excursion
from app.equipment.base import PRESERVED_ON_RESET, Equipment
from app.plant.topology import Stream
from app.statetypes import JSONValue


STATE_VERSION = 1

STATE_KEYS = (
    "version",
    "clock",
    "equipment",
    "instruments",
    "domains",
    "loops",
    "arbiter",
    "envelope",
)

Step = Callable[[], None]

_SIDES = ("lo", "hi")


class StateError(ValueError):
    """A save that cannot be restored onto this engine, or a state that
    cannot be saved."""


def capture_state(engine: Engine) -> dict[str, JSONValue]:
    """Everything `restore_state` needs, as plain JSON-safe values."""
    clock = engine.clock.get_state()

    return {
        "version": STATE_VERSION,
        "clock": {
            "sim_time": clock["sim_time"],
            "speed": clock["speed"],
            "paused": clock["paused"],
        },
        "equipment": {
            tag: dict(_device_state(device))
            for tag, device in engine.equipment.items()
        },
        "instruments": {
            tag: {"bias": instrument.bias}
            for tag, instrument in engine.instruments.items()
        },
        "domains": {
            domain: _capture_domain(engine, domain)
            for domain in engine.topologies
        },
        "loops": {
            tag: _capture_loop(engine, tag)
            for tag in engine.loops
        },
        "arbiter": {
            output: {
                source.value: dict(requesters)
                for source, requesters in by_source.items()
            }
            for output, by_source in _held_demands(engine.arbiter).items()
        },
        "envelope": _capture_envelope(engine),
    }


def restore_state(engine: Engine, state: Mapping[str, JSONValue]) -> None:
    """Write `state` onto `engine`, or raise `StateError` and change nothing."""
    if "version" not in state:
        raise StateError("state: missing field 'version'")

    if state["version"] != STATE_VERSION:
        raise StateError(
            f"state: version {state['version']!r} is not the version "
            f"{STATE_VERSION} this code restores",
        )

    fields = _keyed(state, STATE_KEYS, "state")
    steps: list[Step] = []

    _decode_clock(engine, fields["clock"], steps)
    _decode_equipment(engine, fields["equipment"], steps)
    _decode_instruments(engine, fields["instruments"], steps)
    _decode_domains(engine, fields["domains"], steps)
    _decode_loops(engine, fields["loops"], steps)
    _decode_arbiter(engine, fields["arbiter"], steps)
    _decode_envelope(engine, fields["envelope"], steps)

    for step in steps:
        step()


def _capture_domain(engine: Engine, domain: str) -> dict[str, JSONValue]:
    graph = engine.topologies[domain]
    transport = engine.transports[domain]
    result = engine.solver_results[domain]

    return {
        "nodes": {
            node_id: node.pressure for node_id, node in graph.nodes.items()
        },
        "streams": {
            branch_id: {
                "flow": branch.stream.flow,
                "pressure": branch.stream.pressure,
                "temperature": branch.stream.temperature,
                "composition": dict(branch.stream.composition),
            }
            for branch_id, branch in graph.branches.items()
        },
        "temperatures": dict(transport.temperatures),
        "settled": transport.settled,
        "failure": transport.failure,
        "solver": {
            "converged": result.converged,
            "iterations": result.iterations,
            "residual": result.residual,
            "pressure_residual": result.pressure_residual,
            "flow_residual": result.flow_residual,
            "failure": result.failure,
        },
    }


def _capture_loop(engine: Engine, tag: str) -> dict[str, JSONValue]:
    loop = engine.loops[tag].loop
    pid = loop.pid

    return {
        "primed": tag in engine._primed,
        "mode": loop.mode.value,
        "output": loop.output,
        "manual_output": loop.manual_output,
        "entering": loop._entering,
        "pid": {
            "kp": pid.kp,
            "ki": pid.ki,
            "kd": pid.kd,
            "output_min": pid.output_min,
            "output_max": pid.output_max,
            "setpoint": pid.setpoint,
            "action": pid.action.value,
            "integral": pid._integral,
            "prev_measurement": pid._prev_measurement,
        },
    }


def _capture_envelope(engine: Engine) -> dict[str, JSONValue]:
    points: dict[str, JSONValue] = {}

    for key, evaluator in engine.limits.items():
        tag, variable = key
        tracker = engine.trackers[key]
        severity, side = engine._envelope_band[key]
        peak = tracker._peak

        row: dict[str, JSONValue] = {
            "evaluator": {
                "band": _band_row(evaluator._band),
                "pending": _band_row(evaluator._pending),
                "pending_elapsed": evaluator._pending_elapsed,
            },
            "tracker": {
                "elapsed": tracker._elapsed,
                "time_in_band": {
                    band.name: seconds
                    for band, seconds in tracker._time_in_band.items()
                },
                "peak": None if peak is None else {
                    "severity": peak.severity.name,
                    "magnitude": peak.magnitude,
                    "timestamp": peak.timestamp,
                },
            },
            "band": {"severity": severity.name, "side": side},
            "since": engine._envelope_since[key],
        }

        by_variable = points.setdefault(tag, {})
        assert isinstance(by_variable, dict)
        by_variable[variable] = row

    return points


def _band_row(band: _Band | None) -> JSONValue:
    if band is None:
        return None

    return {
        "severity": band.severity.name,
        "side": band.side,
        "threshold": band.threshold,
    }


def _held_demands(arbiter: CommandArbiter) -> dict[str, dict[Source, dict[str, float]]]:
    return arbiter._demands


def _device_state(device: Equipment) -> dict[str, JSONValue]:
    """A device's own attributes, refusing any that is not a JSON primitive -
    saving one silently would lose it."""
    row: dict[str, JSONValue] = {}

    for name, value in device.__dict__.items():
        if name in PRESERVED_ON_RESET or name == "tag":
            continue

        if not _is_primitive(value):
            raise StateError(
                f"equipment.{device.tag}.{name}: a {type(value).__name__} "
                f"cannot be saved, a device's state must be plain numbers, "
                f"flags and text",
            )

        row[name] = value

    return row


def _is_primitive(value: object) -> TypeGuard[bool | int | float | str | None]:
    return value is None or isinstance(value, (bool, int, float, str))


def _kind(value: bool | int | float | str | None) -> str:
    if value is None:
        return "none"

    if isinstance(value, bool):
        return "bool"

    if isinstance(value, (int, float)):
        return "number"

    return "text"


def _decode_clock(engine: Engine, value: JSONValue, steps: list[Step]) -> None:
    fields = _keyed(value, ("sim_time", "speed", "paused"), "clock")
    # Any finite time and speed: SimulationClock refuses neither a negative
    # speed nor the negative sim_time one steps to, and a save must restore
    # whatever a live clock can hold.
    sim_time = _number(fields["sim_time"], "clock.sim_time")
    speed = _number(fields["speed"], "clock.speed")

    paused = _flag(fields["paused"], "clock.paused")

    def apply() -> None:
        engine.clock.sim_time = sim_time
        engine.clock.set_speed(speed)

        if paused:
            engine.clock.pause()
        else:
            engine.clock.resume()

    steps.append(apply)


def _decode_equipment(engine: Engine, value: JSONValue, steps: list[Step]) -> None:
    rows = _keyed(value, engine.equipment, "equipment")

    for tag, device in engine.equipment.items():
        path = f"equipment.{tag}"
        current = _device_state(device)
        row = _keyed(rows[tag], current, path)

        for name, saved in row.items():
            if not _is_primitive(saved):
                raise StateError(f"{path}.{name}: {saved!r} is not a plain value")

            was = current[name]
            assert _is_primitive(was)

            # None is allowed only where the device holds None now: a device
            # attribute's live value is the only record of its type. So an
            # attribute that can be None or a value would be refused across
            # that change; no device has one, and adding one needs a declared
            # type here first.
            if was is not None and _kind(saved) != _kind(was):
                raise StateError(
                    f"{path}.{name}: expected a {_kind(was)}, got {saved!r}",
                )

            if isinstance(saved, float) and not math.isfinite(saved):
                raise StateError(f"{path}.{name}: {saved!r} is not finite")

        _check_setters(device, row, path)
        steps.append(partial(device.__dict__.update, row))


def _check_setters(device: Equipment, row: Mapping[str, JSONValue], path: str) -> None:
    """Run each property setter a device guards a `_name` attribute with,
    on a copy holding the whole saved row, so a range check and a check
    across two fields (a relief valve's set pressure and blowdown) both see
    the values they will be restored beside."""
    probe = copy.copy(device)
    probe.__dict__.update(row)

    for name, saved in row.items():
        guard = getattr(type(device), name.removeprefix("_"), None)

        if not name.startswith("_") or not isinstance(guard, property) or guard.fset is None:
            continue

        try:
            guard.fset(probe, saved)
        except ValueError as error:
            raise StateError(f"{path}.{name}: {error}") from error


def _decode_instruments(engine: Engine, value: JSONValue, steps: list[Step]) -> None:
    rows = _keyed(value, engine.instruments, "instruments")

    for tag, instrument in engine.instruments.items():
        path = f"instruments.{tag}"
        bias = _number(_keyed(rows[tag], ("bias",), path)["bias"], f"{path}.bias")
        steps.append(partial(setattr, instrument, "bias", bias))


def _decode_domains(engine: Engine, value: JSONValue, steps: list[Step]) -> None:
    domains = _keyed(value, engine.topologies, "domains")

    for domain, graph in engine.topologies.items():
        path = f"domains.{domain}"
        fields = _keyed(
            domains[domain],
            ("nodes", "streams", "temperatures", "settled", "failure", "solver"),
            path,
        )
        transport = engine.transports[domain]

        nodes = _keyed(fields["nodes"], graph.nodes, f"{path}.nodes")

        for node_id, node in graph.nodes.items():
            node_path = f"{path}.nodes.{node_id}"
            pressure = _number(nodes[node_id], node_path)

            if node.is_boundary and pressure <= 0.0:
                raise StateError(
                    f"{node_path}: boundary pressure {pressure!r} is not positive",
                )

            setter = node.set_boundary_pressure if node.is_boundary else node.set_pressure
            steps.append(partial(setter, pressure))

        streams = _keyed(fields["streams"], graph.branches, f"{path}.streams")

        for branch_id, branch in graph.branches.items():
            stream_path = f"{path}.streams.{branch_id}"
            row = _keyed(
                streams[branch_id],
                ("flow", "pressure", "temperature", "composition"),
                stream_path,
            )
            composition = _mapping(row["composition"], f"{stream_path}.composition")

            try:
                stream = Stream(
                    flow=_number(row["flow"], f"{stream_path}.flow"),
                    pressure=_number(row["pressure"], f"{stream_path}.pressure"),
                    temperature=_number(row["temperature"], f"{stream_path}.temperature"),
                    composition={
                        species: _number(fraction, f"{stream_path}.composition.{species}")
                        for species, fraction in composition.items()
                    },
                )
            except ValueError as error:
                if isinstance(error, StateError):
                    raise

                raise StateError(f"{stream_path}: {error}") from error

            steps.append(partial(branch.set_stream, stream))

        temperatures = _keyed(
            fields["temperatures"],
            transport.temperatures,
            f"{path}.temperatures",
        )
        restored_temperatures = {
            node_id: _number(temperature, f"{path}.temperatures.{node_id}")
            for node_id, temperature in temperatures.items()
        }
        settled = _flag(fields["settled"], f"{path}.settled")
        failure = _optional_text(fields["failure"], f"{path}.failure")
        result = _decode_solver_result(fields["solver"], f"{path}.solver")

        def apply(
            domain: str = domain,
            temperatures: dict[str, float] = restored_temperatures,
            settled: bool = settled,
            failure: str | None = failure,
            result: SolverResult = result,
        ) -> None:
            transport = engine.transports[domain]
            transport.temperatures = temperatures
            transport.settled = settled
            transport.failure = failure
            engine.solver_results[domain] = result

        steps.append(apply)


def _decode_solver_result(value: JSONValue, path: str) -> SolverResult:
    fields = _keyed(
        value,
        (
            "converged",
            "iterations",
            "residual",
            "pressure_residual",
            "flow_residual",
            "failure",
        ),
        path,
    )
    iterations = fields["iterations"]

    if isinstance(iterations, bool) or not isinstance(iterations, int):
        raise StateError(f"{path}.iterations: expected a whole number, got {iterations!r}")

    try:
        return SolverResult(
            converged=_flag(fields["converged"], f"{path}.converged"),
            iterations=iterations,
            residual=_number(fields["residual"], f"{path}.residual"),
            pressure_residual=_number(
                fields["pressure_residual"],
                f"{path}.pressure_residual",
            ),
            flow_residual=_number(fields["flow_residual"], f"{path}.flow_residual"),
            failure=_optional_text(fields["failure"], f"{path}.failure"),
        )
    except ValueError as error:
        if isinstance(error, StateError):
            raise

        raise StateError(f"{path}: {error}") from error


def _decode_loops(engine: Engine, value: JSONValue, steps: list[Step]) -> None:
    rows = _keyed(value, engine.loops, "loops")

    for tag, binding in engine.loops.items():
        path = f"loops.{tag}"
        fields = _keyed(
            rows[tag],
            ("primed", "mode", "output", "manual_output", "entering", "pid"),
            path,
        )
        pid = _keyed(
            fields["pid"],
            (
                "kp",
                "ki",
                "kd",
                "output_min",
                "output_max",
                "setpoint",
                "action",
                "integral",
                "prev_measurement",
            ),
            f"{path}.pid",
        )

        primed = _flag(fields["primed"], f"{path}.primed")
        mode = _enum(Mode, fields["mode"], f"{path}.mode")

        # Engine never hands a loop a master, so a CASCADE loop could not step.
        if mode is Mode.CASCADE:
            raise StateError(f"{path}.mode: the engine cannot run a cascade loop")

        output = _number(fields["output"], f"{path}.output")
        manual_output = _number(fields["manual_output"], f"{path}.manual_output")
        entering = _flag(fields["entering"], f"{path}.entering")
        gains = {
            name: _number(pid[name], f"{path}.pid.{name}")
            for name in ("kp", "ki", "kd", "output_min", "output_max", "setpoint", "integral")
        }
        action = _enum(Action, pid["action"], f"{path}.pid.action")

        if gains["output_min"] > gains["output_max"]:
            raise StateError(
                f"{path}.pid: output_min {gains['output_min']!r} exceeds "
                f"output_max {gains['output_max']!r}",
            )

        if gains["ki"] < 0.0:
            raise StateError(f"{path}.pid.ki: {gains['ki']!r} is negative")

        previous = pid["prev_measurement"]
        prev_measurement = (
            None
            if previous is None
            else _number(previous, f"{path}.pid.prev_measurement")
        )

        def apply(
            tag: str = tag,
            primed: bool = primed,
            mode: Mode = mode,
            output: float = output,
            manual_output: float = manual_output,
            entering: bool = entering,
            gains: dict[str, float] = gains,
            action: Action = action,
            prev_measurement: float | None = prev_measurement,
        ) -> None:
            loop = engine.loops[tag].loop
            loop.mode = mode
            loop.output = output
            loop.manual_output = manual_output
            loop._entering = entering

            loop.pid.kp = gains["kp"]
            loop.pid.ki = gains["ki"]
            loop.pid.kd = gains["kd"]
            loop.pid.output_min = gains["output_min"]
            loop.pid.output_max = gains["output_max"]
            loop.pid.setpoint = gains["setpoint"]
            loop.pid.action = action
            loop.pid._integral = gains["integral"]
            loop.pid._prev_measurement = prev_measurement

            if primed:
                engine._primed.add(tag)
            else:
                engine._primed.discard(tag)

        steps.append(apply)


def _decode_arbiter(engine: Engine, value: JSONValue, steps: list[Step]) -> None:
    outputs = _keyed(value, engine.arbiter.outputs, "arbiter")
    demands: dict[str, dict[Source, dict[str, float]]] = {}

    # Posting the saved demands to a scratch arbiter is what finds two
    # requesters of one source that disagree, before the real one is touched.
    scratch = CommandArbiter()

    for output in engine.arbiter.outputs:
        path = f"arbiter.{output}"
        by_source = _keyed(outputs[output], [source.value for source in PRECEDENCE], path)
        demands[output] = {}
        scratch.bind(output, _ignore)

        for source in PRECEDENCE:
            requesters = _mapping(by_source[source.value], f"{path}.{source.value}")
            held = {
                requester: _number(demand, f"{path}.{source.value}.{requester}")
                for requester, demand in requesters.items()
            }
            demands[output][source] = held

            for requester, demand in held.items():
                try:
                    scratch.demand(output, source, requester, demand)
                except ValueError as error:
                    raise StateError(f"{path}.{source.value}.{requester}: {error}") from error

    def apply() -> None:
        for output, by_source_held in _held_demands(engine.arbiter).items():
            for source, requesters in by_source_held.items():
                for requester in list(requesters):
                    engine.arbiter.release(output, source, requester)

        for output, by_source_held in demands.items():
            for source, requesters in by_source_held.items():
                for requester, demand in requesters.items():
                    engine.arbiter.demand(output, source, requester, demand)

    steps.append(apply)


def _decode_envelope(engine: Engine, value: JSONValue, steps: list[Step]) -> None:
    variables: dict[str, list[str]] = {}

    for tag, variable in engine.limits:
        variables.setdefault(tag, []).append(variable)

    tags = _keyed(value, variables, "envelope")

    for tag, names in variables.items():
        by_variable = _keyed(tags[tag], names, f"envelope.{tag}")

        for variable in names:
            key = (tag, variable)
            path = f"envelope.{tag}.{variable}"
            fields = _keyed(
                by_variable[variable],
                ("evaluator", "tracker", "band", "since"),
                path,
            )

            evaluator_fields = _keyed(
                fields["evaluator"],
                ("band", "pending", "pending_elapsed"),
                f"{path}.evaluator",
            )
            limits = engine.limits[key].limits
            held_band = _decode_band(
                evaluator_fields["band"],
                limits,
                f"{path}.evaluator.band",
            )
            pending_band = _decode_band(
                evaluator_fields["pending"],
                limits,
                f"{path}.evaluator.pending",
            )
            pending_elapsed = _non_negative(
                evaluator_fields["pending_elapsed"],
                f"{path}.evaluator.pending_elapsed",
            )

            tracker = engine.trackers[key]
            tracker_fields = _keyed(
                fields["tracker"],
                ("elapsed", "time_in_band", "peak"),
                f"{path}.tracker",
            )
            elapsed = _non_negative(tracker_fields["elapsed"], f"{path}.tracker.elapsed")
            saved_times = _keyed(
                tracker_fields["time_in_band"],
                [band.name for band in tracker._time_in_band],
                f"{path}.tracker.time_in_band",
            )
            time_in_band = {
                band: _non_negative(
                    saved_times[band.name],
                    f"{path}.tracker.time_in_band.{band.name}",
                )
                for band in tracker._time_in_band
            }
            peak = _decode_peak(tracker_fields["peak"], f"{path}.tracker.peak")

            engine_band = _keyed(fields["band"], ("severity", "side"), f"{path}.band")
            severity = _severity(engine_band["severity"], f"{path}.band.severity")
            side = _optional_side(engine_band["side"], f"{path}.band.side")
            since = _number(fields["since"], f"{path}.since")

            # The engine's band is always its evaluator's held band, re-read
            # after every evaluate - a save where they differ never stepped.
            held = (
                (held_band.severity, held_band.side)
                if held_band is not None
                else (Severity.NORMAL, None)
            )

            if (severity, side) != held:
                raise StateError(
                    f"{path}.band: {severity.name}/{side} does not match the "
                    f"evaluator's held band {held[0].name}/{held[1]}",
                )

            def apply(
                key: tuple[str, str] = key,
                held_band: _Band | None = held_band,
                pending_band: _Band | None = pending_band,
                pending_elapsed: float = pending_elapsed,
                elapsed: float = elapsed,
                time_in_band: dict[Severity, float] = time_in_band,
                peak: Excursion | None = peak,
                severity: Severity = severity,
                side: Side | None = side,
                since: float = since,
            ) -> None:
                evaluator = engine.limits[key]
                evaluator._band = held_band
                evaluator._pending = pending_band
                evaluator._pending_elapsed = pending_elapsed

                tracker = engine.trackers[key]
                tracker._elapsed = elapsed
                tracker._time_in_band = time_in_band
                tracker._peak = peak

                engine._envelope_band[key] = (severity, side)
                engine._envelope_since[key] = since

            steps.append(apply)


def _decode_band(value: JSONValue, limits: Limits, path: str) -> _Band | None:
    if value is None:
        return None

    fields = _keyed(value, ("severity", "side", "threshold"), path)
    side = _optional_side(fields["side"], f"{path}.side")

    if side is None:
        raise StateError(f"{path}.side: a held band has a side")

    severity = _severity(fields["severity"], f"{path}.severity")

    if severity is Severity.NORMAL:
        raise StateError(f"{path}.severity: a held band is never NORMAL")

    # A band only ever holds the configured limit it crossed, and the
    # evaluator de-escalates against that threshold.
    threshold = _number(fields["threshold"], f"{path}.threshold")
    limit = f"{severity.name.lower()}_{side}"
    configured: float | None = getattr(limits, limit)

    if configured != threshold:
        raise StateError(
            f"{path}.threshold: {threshold!r} is not the configured {limit} "
            f"({configured!r})",
        )

    return _Band(severity, side, threshold)


def _decode_peak(value: JSONValue, path: str) -> Excursion | None:
    if value is None:
        return None

    fields = _keyed(value, ("severity", "magnitude", "timestamp"), path)

    return Excursion(
        _severity(fields["severity"], f"{path}.severity"),
        _non_negative(fields["magnitude"], f"{path}.magnitude"),
        _non_negative(fields["timestamp"], f"{path}.timestamp"),
    )


def _ignore(_: float) -> None:
    return None


def _mapping(value: object, path: str) -> Mapping[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise StateError(f"{path}: expected an object, got {value!r}")

    return value


def _keyed(value: object, expected: Iterable[str], path: str) -> Mapping[str, JSONValue]:
    """`value` as an object holding exactly the keys in `expected`."""
    fields = _mapping(value, path)
    wanted = set(expected)
    missing = sorted(wanted - fields.keys())
    unexpected = sorted(fields.keys() - wanted)

    if missing:
        raise StateError(
            f"{path}: missing {', '.join(repr(name) for name in missing)} "
            f"- the save does not match this plant",
        )

    if unexpected:
        raise StateError(
            f"{path}: unexpected {', '.join(repr(name) for name in unexpected)} "
            f"- the save does not match this plant",
        )

    return fields


def _number(value: object, path: str) -> float:
    """A finite number: `json.loads` accepts NaN and Infinity, and no saved
    number is one on a plant that stepped there."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StateError(f"{path}: expected a number, got {value!r}")

    if not math.isfinite(value):
        raise StateError(f"{path}: {value!r} is not finite")

    return float(value)


def _non_negative(value: object, path: str) -> float:
    """A finite number no step can drive below zero: an evaluator or tracker
    accumulator, which refuses a negative dt before adding it."""
    number = _number(value, path)

    if number < 0.0:
        raise StateError(f"{path}: {number!r} is negative")

    return number


def _flag(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise StateError(f"{path}: expected true or false, got {value!r}")

    return value


def _optional_text(value: object, path: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise StateError(f"{path}: expected text or null, got {value!r}")

    return value


def _enum[E: (Mode, Action)](kind: type[E], value: object, path: str) -> E:
    if not isinstance(value, str):
        raise StateError(f"{path}: expected a {kind.__name__} name, got {value!r}")

    try:
        return kind(value)
    except ValueError as error:
        raise StateError(f"{path}: {value!r} is not a {kind.__name__}") from error


def _severity(value: object, path: str) -> Severity:
    if not isinstance(value, str) or value not in Severity.__members__:
        raise StateError(f"{path}: {value!r} is not a severity")

    return Severity[value]


def _optional_side(value: object, path: str) -> Side | None:
    if value is None:
        return None

    if value == "lo":
        return "lo"

    if value == "hi":
        return "hi"

    raise StateError(f"{path}: expected one of {_SIDES} or null, got {value!r}")
