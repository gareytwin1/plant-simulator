class PlantSimulator:
    def __init__(self):
        self.running = False
        self.pressure = 100.0
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
            self.pressure += 1.0
            self.temperature += 0.5
           
            if self.flow < self.flow_target:
                self.flow = min(
                        self.flow + self.flow_rate,
                        self.flow_target,
                )
            elif self.flow > self.flow_target:
                self.flow = max(
                        self.flow - self.flow_rate,
                        self.flow_target
                )
     

    def get_state(self):
        return {
            "running": self.running,
            "pressure": self.pressure,
            "temperature": self.temperature,
            "flow": self.flow,
        }
