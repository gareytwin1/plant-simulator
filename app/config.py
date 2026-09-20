# Simulation timing

SIMULATION_STEP_SECONDS = 1.0
SIMULATION_SPEED = 1.0


# Compressor timing

LOAD_RATE_PER_SECOND = 0.05
DISCHARGE_VALVE_RATE_PER_SECOND = 0.05


# Pump timing

PUMP_SPEED_RATE_PER_SECOND = 0.10


# Control valve timing

VALVE_STROKE_RATE_PER_SECOND = 0.05


# Gas inventory
#
# Standard conditions for SCFM, from docs/UNITS_CONVENTION.md: 60 F and 1 atm.
# A vessel's gas is held isothermal at the standard temperature, so the only
# constant its rate law needs is the standard pressure, and 1 atm is 14.696
# psia. It is a second spelling of topology.ATMOSPHERIC_PRESSURE on purpose:
# equipment imports nothing from the plant package.

STANDARD_PRESSURE = 14.696  # psia


# Equipment tag prefixes
#
# ISA-style equipment codes: a tag is PREFIX-NNN. This is the reference
# table for what each prefix means; app.equipment.registry is where a tag
# resolves to the device that owns it.

TAG_PREFIXES = {
    "K": "compressor",
    "P": "pump",
    "E": "heat exchanger",
    "V": "vessel",
    "FV": "control valve",
}


# Session registry
#
# An operational guardrail, not a simulation constant: it bounds how many
# live sessions (and therefore how many background scheduler workers,
# two per session) SessionRegistry keeps at once. Beyond capacity the
# least-recently-touched session is ended to make room. See T2-6.

MAX_SESSIONS = 32
