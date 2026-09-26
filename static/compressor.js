const pressureHistory = [];
const maxPressureSamples = 60;

function setText(id, value) {
  const element = document.getElementById(id);

  if (element) {
    element.textContent = value;
  }
}

async function getPlantState() {
  const response = await fetch("/api/state");
  const state = await response.json();

  updatePlantDisplay(state);
  addPressureSample(state);
}

function updatePlantDisplay(state) {
    setText("status", state.running ? "Running" : "Stopped");
    setText("load", `${(state.load * 100).toFixed(1)}%`);
    setText(
        "load-target",
        `${(state.load_target * 100).toFixed(1)}%`
    );

    const loadSlider = document.getElementById("load-slider");

    if (loadSlider) {
        loadSlider.value = state.load_target * 100;
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
        `${state.flow.toFixed(1)} SCFM`
    );

    setText(
        "temperature",
        `${state.temperature.toFixed(1)} °F`
    );
}

function addPressureSample(state) {
  pressureHistory.push({
    suction: state.suction_pressure,
    discharge: state.discharge_pressure,
  });

  if (pressureHistory.length > maxPressureSamples) {
    pressureHistory.shift();
  }

  drawPressureChart();
}

function drawPressureChart() {
  const canvas = document.getElementById(
    "pressure-chart",
  );

  if (!canvas) {
    return;
  }

  const ctx = canvas.getContext("2d");

  if (!ctx) {
    return;
  }

  const width = canvas.width;
  const height = canvas.height;

  const paddingLeft = 60;
  const paddingRight = 20;
  const paddingTop = 20;
  const paddingBottom = 40;

  const chartWidth =
    width - paddingLeft - paddingRight;

  const chartHeight =
    height - paddingTop - paddingBottom;

  const minPressure = 650;
  const maxPressure = 900;

  ctx.clearRect(
    0,
    0,
    width,
    height,
  );

  ctx.strokeStyle = "#cccccc";
  ctx.lineWidth = 1;
  ctx.font = "12px sans-serif";

  const pressureMarks = [
    650,
    700,
    750,
    800,
    850,
    900,
  ];

  pressureMarks.forEach((pressure) => {
    const y =
      paddingTop +
      (
        (maxPressure - pressure) /
        (maxPressure - minPressure)
      ) *
        chartHeight;

    ctx.beginPath();
    ctx.moveTo(
      paddingLeft,
      y,
    );

    ctx.lineTo(
      width - paddingRight,
      y,
    );

    ctx.stroke();

    ctx.fillStyle = "#666666";

    ctx.fillText(
      `${pressure} psi`,
      5,
      y + 4,
    );
  });

  if (pressureHistory.length < 2) {
    return;
  }

  function getX(index) {
    return (
      paddingLeft +
      (
        index /
        (maxPressureSamples - 1)
      ) *
        chartWidth
    );
  }

  function getY(pressure) {
    return (
      paddingTop +
      (
        (maxPressure - pressure) /
        (maxPressure - minPressure)
      ) *
        chartHeight
    );
  }

  ctx.strokeStyle = "#2563eb";
  ctx.lineWidth = 2;
  ctx.beginPath();

  pressureHistory.forEach(
    (sample, index) => {
      const x = getX(index);
      const y = getY(sample.suction);

      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    },
  );

  ctx.stroke();

  ctx.strokeStyle = "#dc2626";
  ctx.lineWidth = 2;
  ctx.beginPath();

  pressureHistory.forEach(
    (sample, index) => {
      const x = getX(index);
      const y = getY(sample.discharge);

      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    },
  );

  ctx.stroke();

  ctx.fillStyle = "#2563eb";

  ctx.fillText(
    "Suction Pressure",
    paddingLeft,
    height - 10,
  );

  ctx.fillStyle = "#dc2626";

  ctx.fillText(
    "Discharge Pressure",
    paddingLeft + 130,
    height - 10,
  );
}

async function startPlant() {
    const loadSlider = document.getElementById("load-slider");
    const loadTarget = Number(loadSlider.value) / 100;

    await fetch("/api/load", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            load_target: loadTarget,
        }),
    });

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

const startButton =
  document.getElementById("start-button");

const stopButton =
  document.getElementById("stop-button");

const loadSlider = document.getElementById("load-slider");

if (loadSlider) {
    loadSlider.addEventListener("input", async () => {
        const loadTarget = Number(loadSlider.value) / 100;

        const response = await fetch("/api/load", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                load_target: loadTarget,
            }),
        });

        const state = await response.json();

        setText(
            "load-target",
            `${(state.load_target * 100).toFixed(1)}%`
        );
    });
}

if (startButton) {
  startButton.addEventListener(
    "click",
    startPlant,
  );
}

if (stopButton) {
  stopButton.addEventListener(
    "click",
    stopPlant,
  );
}

getPlantState();

// Physics runs on the server's own scheduler now (T2-6); this timer only
// polls for the state that worker publishes, it no longer steps anything.
setInterval(
  getPlantState,
  1000,
);
