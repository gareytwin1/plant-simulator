class PlantSimulator:
    def __init__(self):
        self.running = False
        self.pressure = 100.0
        self.temperature = 75.0
        self.flow = 0.0

    def start(self):
        self.running = True
        self.flow = 50.0

    def stop(self):
        self.running = False
        self.flow = 0.0

    def step(self):
        if self.running:
            self.pressure += 1.0
            self.temperature += 0.5
            self.flow += 2.0

    def get_state(self):
        return {
            "running": self.running,
            "pressure": self.pressure,
            "temperature": self.temperature,
            "flow": self.flow,
        }
