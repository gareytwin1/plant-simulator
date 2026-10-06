# Simulation timing

SIMULATION_STEP_SECONDS = 1.0
SIMULATION_SPEED = 1.0


# Compressor timing

LOAD_RATE_PER_SECOND = 0.05


# Pump timing

PUMP_SPEED_RATE_PER_SECOND = 0.10


# Control valve timing

VALVE_STROKE_RATE_PER_SECOND = 0.05


# Heat exchanger timing

EXCHANGER_METAL_RESPONSE_RATE_PER_SECOND = 2.0  # °F/s


# Furnace timing

FURNACE_FIRING_RAMP_RATE_PER_SECOND = 1_000_000.0  # BTU/hr per second


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
# resolves to the device that owns it. The transmitter prefixes name
# instruments (T13-2), which `Engine.instruments` holds by tag instead.

TAG_PREFIXES = {
    "K": "compressor",
    "P": "pump",
    "E": "heat exchanger",
    "H": "furnace",
    "V": "vessel",
    "FV": "control valve",
    "PSV": "relief valve",
    "PT": "pressure transmitter",
    "FT": "flow transmitter",
    "TT": "temperature transmitter",
    "LT": "level transmitter",
    "ZT": "position transmitter",
}


# Session registry
#
# An operational guardrail, not a simulation constant: it bounds how many
# live sessions (and therefore how many background scheduler workers, up
# to two per legacy session and one per training session) SessionRegistry
# keeps at once. Beyond capacity the
# least-recently-touched session is ended to make room. See T2-6.

MAX_SESSIONS = 32


# API boundary
#
# The rate limit is per client address. Behind a reverse proxy or a shared NAT
# every client shares one bucket, so a deployment there needs proxy-header
# handling or larger values. A console polls once a second and sends
# at most ten slider updates a second.

API_MAX_BODY_BYTES = 4096
API_MAX_MAGNITUDE = 1_000_000.0
API_RATE_PER_SECOND = 20.0
API_RATE_BURST = 100.0


# Session lifecycle
#
# An operational guardrail, like MAX_SESSIONS: how long a session may go
# untouched before SessionRegistry.reclaim_idle() ends it and frees its
# workers. A page polls about once a second (a background tab about once a
# minute), so 30 minutes only catches an abandoned one. See T18-5.

SESSION_IDLE_SECONDS = 1800.0


# Alarm history
#
# How many alarm events, acknowledgements and clears one plant's AlarmHistory
# keeps before the oldest is dropped. A seven-device train raises a handful of
# alarms per upset, so this holds many whole scenario runs. See T16-6.

ALARM_HISTORY_CAPACITY = 1000


# Free play
#
# What a training session runs while no scenario is loaded: this plant,
# restored to this initial condition. Both resolve through ScenarioLibrary, so
# free play reads the same files a scenario of the plant does. See T16-8.

FREE_PLAY_PLANT = "olefins_lite"
FREE_PLAY_CONDITION = "normal_operation"
