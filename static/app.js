async function getPlantState() {
  const response = await fetch("/api/state");
  const state = await response.json();

  document.getElementById("status").textContent =
    state.running ? "Running" : "Stopped";

  document.getElementById("pressure").textContent = state.pressure;
  document.getElementById("temperature").textContent = state.temperature;
  document.getElementById("flow").textContent = state.flow;
}

getPlantState();
