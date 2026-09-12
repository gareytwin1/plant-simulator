class PlantSimulator:
    def __init__(self):
        self.running = False

        self.suction_pressure = 100.0
        self.discharge_pressure = 100.0
        self.discharge_pressure_target = 150.0
        self.pressure_rate = 5.0

        self.temperature = 75.0
        self.flow = 0.0
        self.flow_target = 60.0
        self.flow_rate = 5.0

    def start(self):
        self.running = True
        self.flow = 50.0

    def stop(self):
        self.running = False
        self.flow = 0.0

    def step(self):
        if self.running:
            self.temperature += 0.5

            if self.flow < self.flow_target:
                self.flow = min(
                    self.flow + self.flow_rate,
                    self.flow_target,
                )
            elif self.flow > self.flow_target:
                self.flow = max(
                    self.flow - self.flow_rate,
                    self.flow_target,
                )

            if self.discharge_pressure < self.discharge_pressure_target:
                self.discharge_pressure = min(
                    self.discharge_pressure + self.pressure_rate,
                    self.discharge_pressure_target,
                )
            elif self.discharge_pressure > self.discharge_pressure_target:
                self.discharge_pressure = max(
                    self.discharge_pressure - self.pressure_rate,
                    self.discharge_pressure_target,
                )

        elif self.discharge_pressure > self.suction_pressure:
            self.discharge_pressure = max(
                self.discharge_pressure - self.pressure_rate,
                self.suction_pressure,
            )

    def get_state(self):
        return {
            "running": self.running,
            "pressure": self.discharge_pressure,
            "suction_pressure": self.suction_pressure,
            "discharge_pressure": self.discharge_pressure,
            "temperature": self.temperature,
            "flow": self.flow,
        }
