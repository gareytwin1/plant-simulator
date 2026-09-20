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
