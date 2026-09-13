class PlantSimulator:
    def __init__(self):
        self.running = False

        self.supply_pressure = 750.0
        self.discharge_header_pressure = 750.0

        self.suction_pressure = self.supply_pressure
        self.suction_pressure_target = 675.0

        self.discharge_pressure = self.discharge_header_pressure
        self.discharge_pressure_target = 875.0

        self.load = 0.0
        self.load_target = 0.0
        self.load_rate = 0.05

        self.flow = 0.0
        self.flow_target = 100.0
        self.max_flow = 120.0

        self.shutoff_pressure_rise = 220.0
        self.compressor_resistance = 0.002
        self.suction_resistance = 0.0075
        self.discharge_resistance = 0.0125

        self.base_temperature = 75.0
        self.max_temperature = 120.0

        self.max_spread = 220.0

        self.downstream_restriction = 0.0

        self.discharge_valve_position = 1.0
        self.valve_resistance_scale = 0.0025 

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        self.load_target = 0.0

    def set_load_target(self, target):
        self.load_target = max(
            0.0,
            min(target, 1.0),
        )

    def set_discharge_valve_position(self, position):
        self.discharge_valve_position = max(
            0.10,
            min(position, 1.0),
        )

    @property
    def spread(self):
        return self.discharge_pressure - self.suction_pressure

    @property
    def temperature(self):
        if self.spread <= 150.0:
            return (
                self.base_temperature
                + self.spread / 150.0 * 3.0
            )

        if self.spread <= 200.0:
            return (
                78.0
                + (self.spread - 150.0) * 0.18
            )

        return min(
            87.0
            + (self.spread - 200.0) * 1.65,
            self.max_temperature,
        )

    @property
    def system_resistance(self):
        return (
            self.suction_resistance
            + self.discharge_resistance
            + self.downstream_restriction
            + self.valve_resistance
        ) 

    @property
    def valve_resistance(self):
        return self.valve_resistance_scale * (
            1.0 / self.discharge_valve_position ** 2
            - 1.0
        )
     
    @property
    def boundary_pressure_difference(self):
        return (
            self.discharge_header_pressure
            - self.supply_pressure
        ) 

    def step(self):
        if self.running:
            if self.load < self.load_target:
                self.load = min(
                    self.load + self.load_rate,
                    self.load_target,
                )
            elif self.load > self.load_target:
                self.load = max(
                    self.load - self.load_rate,
                    self.load_target,
                )
        else:
            self.load = max(
                self.load - self.load_rate,
                0.0,
            )

        (
            self.flow,
            self.suction_pressure,
            self.discharge_pressure,
        ) = self._calculate_operating_point()    
    

    @property
    def compressor_pressure_rise(self):
        return max(
            self.shutoff_pressure_rise * self.load ** 2
            - self.compressor_resistance * self.flow ** 2,
            0.0,
        )

    @property
    def valve_pressure_drop(self):
        return self.valve_resistance * self.flow ** 2

    def _calculate_operating_point(self):
        available_pressure_rise = (
            self.shutoff_pressure_rise * self.load ** 2
            - self.boundary_pressure_difference
        )

        if available_pressure_rise <= 0.0:
            return (
                0.0,
                self.supply_pressure,
                self.discharge_header_pressure,
            )

        flow = (
            available_pressure_rise
            / (
                self.compressor_resistance
                + self.system_resistance
            )
        ) ** 0.5

        flow = min(flow, self.max_flow)

        suction_pressure = (
            self.supply_pressure
            - self.suction_resistance * flow ** 2
        )

        discharge_pressure = (
            self.discharge_header_pressure
            + (
                self.discharge_resistance
                + self.downstream_restriction
                + self.valve_resistance
            ) * flow ** 2
        )

        return (
            flow,
            suction_pressure,
            discharge_pressure,
        )

    def get_state(self):
        return {
            "running": self.running,
            "pressure": self.discharge_pressure,
            "suction_pressure": self.suction_pressure,
            "discharge_pressure": self.discharge_pressure,
            "spread": self.spread,
            "temperature": self.temperature,
            "flow": self.flow,
            "load": self.load,
            "load_target": self.load_target,
            "suction_pressure_target": self.suction_pressure_target,
            "discharge_pressure_target": self.discharge_pressure_target,
            "flow_target": self.flow_target,
            "max_spread": self.max_spread,
            "max_flow": self.max_flow,
            "max_temperature": self.max_temperature,
            "compressor_pressure_rise": self.compressor_pressure_rise,
            "discharge_valve_position": self.discharge_valve_position,
            "valve_pressure_drop": self.valve_pressure_drop,
        }
