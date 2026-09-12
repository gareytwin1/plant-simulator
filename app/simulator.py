class PlantSimulator:
    def __init__(self):
        self.running = False

        self.equalized_pressure = 750.0

        self.suction_pressure = 750.0
        self.suction_pressure_target = 675.0

        self.discharge_pressure = 750.0
        self.discharge_pressure_target = 875.0

        self.load = 0.0
        self.load_rate = 0.05
        self.load_target = 0.0 

        self.minimum_running_flow = 50.0
        self.flow = 0.0
        self.flow_target = 100.0
        self.max_flow = 120.0

        self.base_temperature = 75.0
        self.max_temperature = 120.0

        self.max_spread = 220.0

        self.passive_flow_coefficient = 0.20

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

        self.suction_pressure = (
            self.equalized_pressure
            - (
                self.equalized_pressure
                - self.suction_pressure_target
            ) * self.load
        )

        self.discharge_pressure = (
            self.equalized_pressure
            + (
                self.discharge_pressure_target
                - self.equalized_pressure
            ) * self.load
        )

        if self.running:
            if self.load == 0.0:
                self.flow = 0.0
            else:
                self.flow = min(
                    self.minimum_running_flow
                    + (
                        self.flow_target
                        - self.minimum_running_flow
                    ) * self.load,
                    self.max_flow,
                )
        else:
            self.flow = min(
                self.spread * self.passive_flow_coefficient,
                self.max_flow,
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
        }
