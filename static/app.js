async function getPlantState() {
  const response = await fetch("/api/state");
  const state = await response.json();

  updatePlantDisplay(state);
}

function updatePlantDisplay(state) {
  document.getElementById("status").textContent =
    state.running ? "Running" : "Stopped";

  document.getElementById("pressure").textContent =
    `${state.pressure} psi`;

  document.getElementById("temperature").textContent =
    `${state.temperature} °F`;

  document.getElementById("flow").textContent =
    state.flow;
}

async function startPlant() {
  const response = await fetch("/api/start", {
    method: "POST",
  });

  const state = await response.json();
  updatePlantDisplay(state);
}

async function stopPlant() {
  const response = await fetch("/api/stop", {
    method: "POST",
  });

  const state = await response.json();
  updatePlantDisplay(state);
}

async function stepPlant() {
  const response = await fetch("/api/step", {
    method: "POST",
  });

  const state = await response.json();
  updatePlantDisplay(state);
}

document
  .getElementById("start-button")
  .addEventListener("click", startPlant);

document
  .getElementById("stop-button")
  .addEventListener("click", stopPlant);

getPlantState();

setInterval(stepPlant, 1000);
