function setText(id, value) {
  const element = document.getElementById(id);

  if (element) {
    element.textContent = value;
  }
}

async function getPumpState() {
  const response = await fetch("/api/pump/state");
  if (!response.ok) return;

  const state = await response.json();

  updatePumpDisplay(state);
}

function updatePumpDisplay(state) {
    setText("status", state.running ? "Running" : "Stopped");
    setText("speed", `${(state.speed * 100).toFixed(1)}%`);
    setText(
        "speed-target",
        `${(state.speed_target * 100).toFixed(1)}%`
    );

    const speedSlider = document.getElementById("speed-slider");

    if (speedSlider) {
        speedSlider.value = state.speed_target * 100;
    }

    setText(
        "suction-pressure",
        `${state.suction_pressure.toFixed(1)} psi`
    );

    setText(
        "discharge-pressure",
        `${state.discharge_pressure.toFixed(1)} psi`
    );

    setText(
        "spread",
        `${state.spread.toFixed(1)} psi`
    );

    setText(
        "flow",
        `${state.flow.toFixed(1)} GPM`
    );
}

async function startPump() {
    const speedSlider = document.getElementById("speed-slider");
    const speedTarget = Number(speedSlider.value) / 100;

    await fetch("/api/pump/speed", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            speed_target: speedTarget,
        }),
    });

    const response = await fetch("/api/pump/start", {
        method: "POST",
    });

    if (!response.ok) return;

    const state = await response.json();

    updatePumpDisplay(state);
}

async function stopPump() {
    const response = await fetch("/api/pump/stop", {
        method: "POST",
    });

    if (!response.ok) return;

    const state = await response.json();

    updatePumpDisplay(state);
}

const startButton =
  document.getElementById("start-button");

const stopButton =
  document.getElementById("stop-button");

const speedSlider = document.getElementById("speed-slider");

// A drag fires `input` dozens of times a second; the API is rate limited, so
// send the latest value at most this often.
const SLIDER_SEND_MS = 100;
let speedSendTimer = null;

if (speedSlider) {
    speedSlider.addEventListener("input", () => {
        if (speedSendTimer !== null) return;

        speedSendTimer = setTimeout(sendSpeedTarget, SLIDER_SEND_MS);
    });
}

async function sendSpeedTarget() {
    speedSendTimer = null;

    const speedTarget = Number(speedSlider.value) / 100;

    const response = await fetch("/api/pump/speed", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            speed_target: speedTarget,
        }),
    });

    if (!response.ok) return;

    const state = await response.json();

    setText(
        "speed-target",
        `${(state.speed_target * 100).toFixed(1)}%`
    );
}

if (startButton) {
  startButton.addEventListener(
    "click",
    startPump,
  );
}

if (stopButton) {
  stopButton.addEventListener(
    "click",
    stopPump,
  );
}

getPumpState();

// Physics runs on the server's own scheduler now (T2-6); this timer only
// polls for the state that worker publishes, it no longer steps anything.
setInterval(
  getPumpState,
  1000,
);
