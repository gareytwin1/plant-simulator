import os


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
# live sessions (and therefore how many background scheduler workers, one per
# session) SessionRegistry keeps at once. Beyond capacity the
# least-recently-touched session is ended to make room. See T2-6.

MAX_SESSIONS = 32


# API boundary
#
# The rate limit is per client address. A console polls once a second and
# sends at most ten slider updates a second. Behind a reverse proxy, list the
# proxy's addresses or CIDRs in API_TRUSTED_PROXIES (env PLANT_TRUSTED_PROXIES,
# comma-separated) and each client behind it gets its own bucket, read from
# X-Forwarded-For. Empty trusts no one: every request keys on its peer address
# and the header is ignored. Clients behind a shared NAT still share a bucket.
# Forwarded entries may carry a port (1.2.3.4:5678, [::1]:5678). See T18-8.

API_MAX_BODY_BYTES = 4096
API_MAX_MAGNITUDE = 1_000_000.0
API_RATE_PER_SECOND = 20.0
API_RATE_BURST = 100.0


def trusted_proxies_from_env(value: str) -> tuple[str, ...]:
    return tuple(entry.strip() for entry in value.split(",") if entry.strip())


API_TRUSTED_PROXIES = trusted_proxies_from_env(os.environ.get("PLANT_TRUSTED_PROXIES", ""))


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


# Console stream
#
# Seconds between the snapshots /api/stream pushes to a console. The page's
# connection indicator reads the same value to decide when data has gone stale.
# See T16-9.

STREAM_INTERVAL_SECONDS = 1.0


# Trend history
#
# What each plant's Historian keeps for /api/trend: at most one sample per point
# per TREND_SAMPLE_PERIOD_SECONDS of simulated time (one per step at speed 1),
# the last TREND_CAPACITY of them. 1800 samples at one a second is 30
# simulated minutes, which covers the longest scenario (1200 s). Memory is
# about 5.5 MB per runtime at 27 points, and a session holds a free-play
# runtime plus at most one scenario's, so the worst case is about
# MAX_SESSIONS * 2 * 5.5 MB, roughly 350 MB, accepted for now; a slotted
# Sample or per-tag arrays would cut it if sessions grow. A request is
# bounded by the three limits below and refused, not clamped, beyond them.
# See T17-3.

TREND_SAMPLE_PERIOD_SECONDS = 1.0
TREND_CAPACITY = 1800
TREND_DEFAULT_POINTS = 500
TREND_MAX_POINTS = 2000
TREND_MAX_TAGS = 8
