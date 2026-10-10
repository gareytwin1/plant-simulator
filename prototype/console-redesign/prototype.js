/* Console redesign prototype - interaction layer.
 *
 * Replays sample-data.js and draws it with the production modules' pure
 * functions, loaded unchanged from static/js/:
 *   ProcessGraphic  binds the real plant.svg to a snapshot
 *   AlarmConsole    replays the alarm history into live alarms
 *   Faceplates      controller display model and entry validation
 *   Trends          chart rendering, scales, limit bands and alarm markers
 *   ScenarioBar     which run control a phase offers
 * Everything here is presentation. Nothing reaches /api: fetch is wrapped so
 * any such request is refused and counted, and every command a control
 * would send is shown as feedback instead.
 */
(function () {
  "use strict";

  var S = window.PROTOTYPE_SAMPLE;
  var G = window.ProcessGraphic;
  var A = window.AlarmConsole;
  var F = window.Faceplates;
  var T = window.Trends;
  var R = window.ScenarioBar;

  var FREE = "free";
  // The simple separator (deferred-mechanics item 7), drawn with the same
  // binding attributes as static/graphics/plant.svg.
  var SVG_URL = "separator.svg";
  var TICK_MS = 250;
  var MISSING = "--";
  var STORE_KEY = "prototype.console.trendWindows";

  /* ---- two-step limit ladder ------------------------------------------- */

  // Production spells a band by severity step (HI, HIHI, HIHIHI), so a
  // high-high that trips reads HIHIHI. The console shows two steps a side:
  // the first limit is HI, the outer one HIHI, whether or not it trips.
  function ladder(text) { return String(text).replace(/\b(HI|LO)\1\1\b/g, "$1$1"); }

  Object.keys(S.recordings).forEach(function (key) {
    S.recordings[key].alarms.forEach(function (a) { if (a.message) a.message = ladder(a.message); });
  });

  /* ---- isolation guard ------------------------------------------------- */

  var blocked = [];
  var commands = []; // what a control would have posted; never sent
  var realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var url = String(input && input.url ? input.url : input);
    if (/(^|\/)api\//.test(url)) {
      blocked.push(url);
      renderGuard();
      return Promise.reject(new Error("Prototype: request to " + url + " blocked"));
    }
    return realFetch(input, init);
  };

  /* ---- presentation catalogue -----------------------------------------
   * What each tag is and which C5 actions its class allows
   * (app/api/action.py ACTIONS). The snapshot does not carry a device class,
   * so the prototype names it; production needs it from the server. */

  var DEVICES = {
    "FV-301": {
      kind: "Feed valve",
      readings: ["equipment.FV-301.position", "equipment.FV-301.position_target", "streams.B-FV-301.flow", "streams.B-FV-302.flow"],
      target: { action: "set_position_target", field: "position_target", label: "Position target" },
    },
    "PV-301": {
      kind: "Overhead pressure valve",
      readings: ["equipment.PV-301.position", "equipment.PV-301.position_target", "streams.B-PV-301.flow"],
      target: { action: "set_position_target", field: "position_target", label: "Position target" },
    },
    "LV-301": {
      kind: "Drain level valve",
      readings: ["equipment.LV-301.position", "equipment.LV-301.position_target", "streams.B-LV-301.flow"],
      target: { action: "set_position_target", field: "position_target", label: "Position target" },
    },
    "V-301": {
      kind: "Separator drum",
      readings: ["equipment.V-301.level", "equipment.V-301.pressure"],
    },
  };

  /* The three loops on the drum. PIC-301 runs in the engine today. FIC-301
   * and LIC-301 are proposed: their measurement is a stream flow and a vessel
   * level, which controllers.pv cannot name yet, so their faceplates show the
   * live measurement and valve with the design setpoint. All three run in
   * AUTO, the normal operating mode (record_sample.py runs the proposed two). */
  var CONTROLLERS = {
    "FIC-301": { kind: "Feed flow controller", proposed: true, pv: "B-FV-301.flow", out: "FV-301.position", sp: 50.0, measures: "FV-301", drives: "FV-301" },
    "LIC-301": { kind: "Drum level controller", proposed: true, pv: "V-301.level", out: "LV-301.position", sp: 0.5, measures: "V-301", drives: "LV-301" },
    "PIC-301": { kind: "Drum pressure controller", pv: "PIC-301.pv", out: "PIC-301.out", sp: null, measures: "V-301", drives: "PV-301" },
  };

  // Operator words for points the plant names only by field.
  var NAMES = {
    "B-FV-301.flow": "feed liquid flow",
    "B-FV-302.flow": "feed vapour flow",
    "B-PV-301.flow": "overhead flow",
    "B-LV-301.flow": "drain flow",
  };

  var PERCENT_FIELDS = ["speed", "speed_target", "load", "load_target", "position", "position_target", "level", "out"];
  var PRESSURE_FIELDS = ["pressure", "inlet_pressure", "outlet_pressure", "pv", "sp"];

  /* ---- state ----------------------------------------------------------- */

  var state = {
    view: "launch",
    selectedKey: S.catalogue[0].key,
    session: { key: FREE, phase: "idle", elapsed: 0 },
    speed: 1,
    armedAbort: false,
    localAcks: [],
    faceplate: null, // {kind: "equipment"|"controller", tag}
    windows: {}, // point -> window record
    z: 10,
    overlayOpen: false,
  };

  var dom = {};
  var svg = null;
  var graphic = null;
  var flowArrows = [];
  var readouts = [];
  var unitByPoint = {};

  /* ---- helpers --------------------------------------------------------- */

  function $(id) { return document.getElementById(id); }

  function el(name, attrs, text) {
    var node = document.createElement(name);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function svgEl(name, attrs) {
    var node = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    return node;
  }

  function isNum(v) { return typeof v === "number" && isFinite(v); }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function mmss(seconds) {
    var s = Math.max(0, Math.floor(seconds));
    return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  }

  function entry(key) {
    return S.catalogue.find(function (c) { return c.key === key; }) || null;
  }

  function recording() { return S.recordings[state.session.key]; }

  function frameIndex() {
    var rec = recording();
    var i = Math.floor(state.session.elapsed / S.source.frame_every_s);
    return clamp(i, 0, rec.frames.length - 1);
  }

  function snapshot() { return recording().frames[frameIndex()]; }

  function startTime() { return recording().frames[0].sim_time; }

  function now() { return snapshot().sim_time; }

  // "equipment.V-301.level" -> "V-301.level"; "streams.B-LV-301.flow" -> "B-LV-301.flow"
  function pointOf(path) { return path.split(".").slice(1).join("."); }

  function fieldOf(point) { var parts = point.split("."); return parts[parts.length - 1]; }

  function tagOf(point) { return point.split(".")[0]; }

  function descriptor(point) {
    if (NAMES[point]) return tagOf(point).replace(/^B-/, "") + " " + NAMES[point];
    var d = recording().trend_points.descriptors[point];
    return tagOf(point) + " " + (d || fieldOf(point).replace(/_/g, " "));
  }

  /* A controllers row for any loop on the drawing: the engine's own, or one
   * built from a proposed loop's live measurement and valve. */
  function controllerRow(tag, snap) {
    var s = snap || snapshot();
    if (s.controllers && s.controllers[tag]) return s.controllers[tag];
    var c = CONTROLLERS[tag];
    if (!c) return null;
    return { pv: pointValue(c.pv, s), sp: c.sp, out: pointValue(c.out, s), mode: "AUTO", out_min: 0, out_max: 1, tunable: false, pv_unit: unitOf(c.pv) };
  }

  function isPercent(point) { return PERCENT_FIELDS.indexOf(fieldOf(point)) >= 0; }

  /* One unit per point, everywhere: what plant.svg already labels, then the
   * quantity's unit. Units per point are a deferred backend task. */
  function unitOf(point) {
    if (isPercent(point)) return "%";
    if (unitByPoint[point]) return unitByPoint[point];
    var field = fieldOf(point);
    if (PRESSURE_FIELDS.indexOf(field) >= 0) return "psia";
    if (field === "flow") {
      var stream = "B-" + tagOf(point) + ".flow";
      if (unitByPoint[stream]) return unitByPoint[stream];
      var own = tagOf(point).replace(/^B-/, "") + ".flow";
      if (unitByPoint[own]) return unitByPoint[own];
    }
    return "";
  }

  /* Overview (canvas) rounds; detail (faceplate, trend) shows one decimal. */
  function detail(point, value) {
    if (!isNum(value)) return MISSING;
    var v = isPercent(point) ? value * 100 : value;
    var text = (Math.abs(v) < 0.05 ? 0 : v).toFixed(1);
    var unit = unitOf(point);
    return unit === "%" ? text + "%" : unit ? text + " " + unit : text;
  }

  function valueAt(path, snap) { return G.resolvePath(snap || snapshot(), path); }

  function pointValue(point, snap) {
    var s = snap || snapshot();
    var tag = tagOf(point);
    var field = fieldOf(point);
    if (s.controllers && s.controllers[tag]) return s.controllers[tag][field];
    if (s.equipment && s.equipment[tag]) return s.equipment[tag][field];
    if (s.nodes && s.nodes[tag]) return s.nodes[tag][field];
    if (s.streams && s.streams[tag]) return s.streams[tag][field];
    return null;
  }

  function bandOfPoint(point) {
    var env = snapshot().envelope || {};
    var row = env[point];
    if (!row || typeof row.band !== "string") return "none";
    return { 2: "warning", 4: "alarm", 6: "trip" }[row.band.length] || "none";
  }

  function notice(text) {
    dom.notice.textContent = text;
    dom.notice.hidden = false;
    clearTimeout(notice.timer);
    notice.timer = setTimeout(function () { dom.notice.hidden = true; }, 3200);
  }

  /* ---- alarms ---------------------------------------------------------- */

  function alarmEntries() {
    var t = now();
    var entries = recording().alarms.filter(function (e) { return e.sim_time <= t; });
    var acks = state.localAcks.filter(function (a) { return a.key === state.session.key; });
    return entries.concat(acks).sort(function (a, b) { return a.sim_time - b.sim_time; });
  }

  function liveAlarms() { return A.deriveAlarms(alarmEntries()); }

  function acknowledge(id) {
    var alarm = liveAlarms().find(function (a) { return a.id === id; });
    if (!alarm || alarm.state === "acked") return;
    state.localAcks.push({ key: state.session.key, type: "acknowledge", id: id, tag: alarm.tag, sim_time: now() });
  }

  var PRIORITY_ORDER = ["critical", "high", "low"];
  var PRIORITY_GLYPH = { critical: "▲", high: "◆", low: "●" };
  var PRIORITY_LABEL = { critical: "CRIT", high: "HIGH", low: "LOW" };

  function renderRibbonAlarms() {
    var alarms = liveAlarms();
    var counts = { critical: 0, high: 0, low: 0 };
    var unack = 0;
    alarms.forEach(function (a) {
      counts[a.priority] = (counts[a.priority] || 0) + 1;
      if (a.state !== "acked") unack += 1;
    });
    var top = PRIORITY_ORDER.find(function (p) { return counts[p] > 0; }) || "none";
    dom.ribbon.setAttribute("data-severity", top);
    dom.ribbon.setAttribute("data-unack", unack > 0 ? "true" : "false");

    var text = dom.alarmSummary;
    text.textContent = "";
    if (!alarms.length) {
      text.textContent = "No active alarms";
      dom.ribbonAlarms.setAttribute("aria-label", "No active alarms. Open the alarm list.");
      return;
    }
    PRIORITY_ORDER.forEach(function (p) {
      if (!counts[p]) return;
      text.appendChild(el("span", { class: "sev-badge", "data-priority": p }, PRIORITY_GLYPH[p] + " " + counts[p] + " " + PRIORITY_LABEL[p]));
    });
    text.appendChild(el("span", { class: "alarm-unack" }, unack ? unack + " new" : "All acknowledged"));
    dom.ribbonAlarms.setAttribute(
      "aria-label",
      PRIORITY_ORDER.filter(function (p) { return counts[p]; }).map(function (p) { return counts[p] + " " + PRIORITY_LABEL[p]; }).join(", ") +
        " alarms, " + (unack ? unack + " not acknowledged" : "all acknowledged") + ". Open the alarm list."
    );
  }

  /* ---- ribbon ---------------------------------------------------------- */

  function renderRibbon() {
    var session = state.session;
    var phase = session.phase;
    var cat = entry(session.key);
    dom.runTitle.textContent = phase === "idle" ? "Free play" : cat ? cat.title : "Scenario";
    dom.runTitle.title = R.modeText(phase, cat ? cat.title : null);
    dom.runState.setAttribute("data-phase", phase);
    dom.runStateText.textContent = phase === "idle" ? "Plant running" : "Scenario \u00B7 " + R.phaseLabel(S.phase_labels, phase);

    var limit = cat ? " / " + mmss(cat.time_limit_s) : "";
    dom.runClock.textContent = mmss(session.elapsed) + limit;
    $("run-clock-inline").textContent = " \u00B7 " + mmss(session.elapsed) + limit;

    var controls = R.controlsFor(phase, state.armedAbort);
    dom.runButton.hidden = !(controls.start || controls.abort);
    if (controls.start) {
      dom.runButton.setAttribute("data-action", "start");
      dom.runButton.querySelector(".btn-glyph").textContent = "▶";
      dom.runButton.querySelector(".btn-label").textContent = "Start run";
      dom.runButton.className = "btn btn-primary btn-run";
    } else if (controls.abort) {
      dom.runButton.setAttribute("data-action", "abort");
      dom.runButton.querySelector(".btn-glyph").textContent = "■";
      dom.runButton.querySelector(".btn-label").textContent = "Abort run";
      dom.runButton.className = "btn btn-run";
    }
    dom.runConfirm.hidden = !controls.confirm;
    dom.changeScenario.querySelector(".label-long").textContent = phase === "complete" || phase === "aborted" ? "Choose a scenario" : "Change scenario";

    document.querySelectorAll("[data-nav]").forEach(function (a) {
      if (a.closest(".ribbon-nav")) {
        if (a.getAttribute("data-nav") === state.view) a.setAttribute("aria-current", "page");
        else a.removeAttribute("aria-current");
      }
    });
    renderRibbonAlarms();
  }

  /* ---- landing --------------------------------------------------------- */

  function renderLaunch() {
    var cat = entry(state.selectedKey);
    $("hero-title").textContent = cat.title;
    var diff = $("hero-difficulty");
    diff.setAttribute("data-difficulty", cat.difficulty);
    diff.textContent = " " + cat.difficulty.charAt(0).toUpperCase() + cat.difficulty.slice(1);
    diff.setAttribute("aria-label", "Difficulty: " + cat.difficulty);
    $("hero-limit").textContent = "Time limit " + Math.round(cat.time_limit_s / 60) + " min";
    $("hero-briefing").textContent = cat.briefing;

    var grid = $("scenario-grid");
    grid.textContent = "";
    S.catalogue.forEach(function (c) {
      var li = el("li");
      var b = el("button", { type: "button", class: "scenario-option", "data-key": c.key, "aria-pressed": c.key === state.selectedKey ? "true" : "false" });
      b.appendChild(el("span", { class: "option-title" }, c.title));
      var chip = el("span", { class: "chip chip-difficulty", "data-difficulty": c.difficulty }, " " + c.difficulty.charAt(0).toUpperCase() + c.difficulty.slice(1));
      chip.style.alignSelf = "flex-start";
      b.appendChild(chip);
      b.appendChild(el("span", { class: "option-briefing" }, c.briefing));
      if (c.key === state.selectedKey) b.appendChild(el("span", { class: "option-selected" }, "✓ Selected"));
      li.appendChild(b);
      grid.appendChild(li);
    });

    var s = state.session;
    var strip = $("session-strip");
    if (s.key !== FREE && (s.phase === "loaded" || s.phase === "running")) {
      strip.hidden = false;
      $("session-text").textContent = "Your plant: " + entry(s.key).title + " · " + R.phaseLabel(S.phase_labels, s.phase) + " " + mmss(s.elapsed);
    } else {
      strip.hidden = true;
    }
  }

  function selectScenario(key) {
    state.selectedKey = key;
    renderLaunch();
    var btn = document.querySelector('.scenario-option[data-key="' + key + '"]');
    if (btn) btn.focus();
  }

  /* START: the existing load, then the existing start (two C5 calls, in order).
   * A run in progress must be aborted first, as the server requires (load is a
   * 409 while running), so START asks before it replaces one. */
  function startSelected() {
    var s = state.session;
    if (s.key !== FREE && s.phase === "running") {
      if (!window.confirm("A run of “" + entry(s.key).title + "” is in progress. Abort it and start “" + entry(state.selectedKey).title + "”?")) return;
      notice("Run aborted.");
    }
    beginSession(state.selectedKey, "running");
    go("console");
  }

  function beginSession(key, phase) {
    closeFaceplate();
    Object.keys(state.windows).forEach(function (p) { closeWindow(p, true); });
    state.session = { key: key, phase: phase, elapsed: 0 };
    state.armedAbort = false;
    tick(0);
  }

  /* ---- views ----------------------------------------------------------- */

  function go(view) {
    state.view = view;
    document.body.setAttribute("data-view", view);
    dom.viewLaunch.hidden = view !== "launch";
    dom.viewConsole.hidden = view !== "console";
    if (view === "launch") {
      closeOverlay(true);
      renderLaunch();
    }
    if (location.hash !== "#" + view) history.replaceState(null, "", "#" + view);
    renderRibbon();
    if (view === "console") {
      requestAnimationFrame(function () { refreshHits(); clampAllWindows(); });
    }
  }

  /* ---- process graphic ------------------------------------------------- */

  function mountGraphic(container, interactive) {
    var g = G.mount(container, { url: SVG_URL, fetch: realFetch });
    return g.ready.then(function () {
      var root = container.querySelector("svg");
      if (!root) return null;
      // The thumbnail and the canvas hold the same SVG: give each copy its
      // own clip ids, or both would resolve to whichever comes first.
      root.querySelectorAll("clipPath[id]").forEach(function (clip) {
        var id = clip.id, own = id + "-" + container.id;
        clip.id = own;
        root.querySelectorAll('[clip-path="url(#' + id + ')"]').forEach(function (n) { n.setAttribute("clip-path", "url(#" + own + ")"); });
      });
      root.removeAttribute("aria-labelledby");
      if (interactive) enhance(root);
      return { svg: root, update: g.update };
    });
  }

  function enhance(root) {
    svg = root;
    svg.setAttribute("aria-label", "Plant overview. Equipment and readings are buttons.");

    // Units the graphic already labels, by point.
    svg.querySelectorAll("[data-bind][data-unit]").forEach(function (t) {
      unitByPoint[pointOf(t.getAttribute("data-bind"))] = t.getAttribute("data-unit");
    });

    // Static flow arrows at the middle of each pipe that carries a flow,
    // pointing the way positive flow runs along the drawn path.
    svg.querySelectorAll("path.pipe[data-flow]").forEach(function (pipe) {
      var length = pipe.getTotalLength ? pipe.getTotalLength() : 0;
      if (!length) return;
      var mid = pipe.getPointAtLength(length / 2);
      var a = pipe.getPointAtLength(Math.max(0, length / 2 - 1));
      var b = pipe.getPointAtLength(Math.min(length, length / 2 + 1));
      var angle = Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI;
      var g = svgEl("g", { class: "flow-arrow", "data-dir": "unknown", transform: "translate(" + mid.x + " " + mid.y + ") rotate(" + angle + ")" });
      g.appendChild(svgEl("path", { class: "arrow-shape", d: "M -4 -4.5 L 5 0 L -4 4.5 Z" }));
      pipe.parentNode.insertBefore(g, pipe.nextSibling);
      flowArrows.push({ pipe: pipe, arrow: g });
    });

    // Each reading becomes a trend button.
    svg.querySelectorAll("text[data-bind]").forEach(function (t) {
      var point = pointOf(t.getAttribute("data-bind"));
      var wrap = svgEl("g", { class: "readout", tabindex: "0", role: "button", "data-point": point });
      t.parentNode.insertBefore(wrap, t);
      var hit = svgEl("rect", { class: "readout-hit" });
      wrap.appendChild(hit);
      wrap.appendChild(t);
      readouts.push({ wrap: wrap, text: t, hit: hit, point: point });
    });

    // Each device becomes a faceplate button, with a hit area behind it.
    svg.querySelectorAll(".eq[data-tag]").forEach(function (g) {
      var tag = g.getAttribute("data-tag");
      g.setAttribute("tabindex", "0");
      g.setAttribute("role", "button");
      g.setAttribute("aria-label", tag + " " + ((DEVICES[tag] || {}).kind || "") + ". Open faceplate.");
      var hit = svgEl("rect", { class: "eq-hit" });
      g.insertBefore(hit, g.firstChild);
    });

    // Each controller balloon becomes a faceplate button.
    svg.querySelectorAll(".ctl[data-controller]").forEach(function (g) {
      var tag = g.getAttribute("data-controller");
      var c = CONTROLLERS[tag] || {};
      g.setAttribute("tabindex", "0");
      g.setAttribute("role", "button");
      g.setAttribute("aria-label", tag + " " + (c.kind || "controller") + (c.proposed ? " (proposed)" : "") + ". Open faceplate.");
    });

    svg.addEventListener("click", onCanvasClick);
    svg.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      var target = e.target.closest(".readout, .ctl, .eq");
      if (!target) return;
      e.preventDefault();
      activate(target);
    });
  }

  function onCanvasClick(e) {
    var target = e.target.closest(".readout, .ctl, .eq");
    if (!target) {
      closeFaceplate();
      return;
    }
    activate(target);
  }

  function activate(target) {
    if (target.classList.contains("readout")) openTrend(target.getAttribute("data-point"), target);
    else if (target.classList.contains("ctl")) openFaceplate("controller", target.getAttribute("data-controller"));
    else openFaceplate("equipment", target.getAttribute("data-tag"));
  }

  function refreshHits() {
    if (!svg || !svg.getBBox) return;
    readouts.forEach(function (r) {
      var box = r.text.getBBox();
      if (!box.width) return;
      r.hit.setAttribute("x", box.x - 4);
      r.hit.setAttribute("y", box.y - 2);
      r.hit.setAttribute("width", box.width + 8);
      r.hit.setAttribute("height", box.height + 4);
      r.wrap.setAttribute("aria-label", descriptor(r.point) + ", " + r.text.textContent + ". Open trend.");
      r.wrap.setAttribute("data-open", state.windows[r.point] ? "true" : "false");
    });
    svg.querySelectorAll(".eq[data-tag]").forEach(function (g) {
      var hit = g.querySelector(".eq-hit");
      hit.setAttribute("width", 0);
      // getBBox ignores clipping, so the drum's liquid (drawn past the drum
      // and clipped to it) is left out of the measurement.
      var clipped = Array.prototype.slice.call(g.querySelectorAll("[clip-path]"));
      clipped.forEach(function (n) { n.style.display = "none"; });
      var box = g.getBBox();
      clipped.forEach(function (n) { n.style.display = ""; });
      hit.setAttribute("x", box.x - 6);
      hit.setAttribute("y", box.y - 6);
      hit.setAttribute("width", box.width + 12);
      hit.setAttribute("height", box.height + 12);
    });
  }

  function updateCanvas(snap) {
    if (!graphic) return;
    graphic.update(snap);
    svg.querySelectorAll("[data-band-of]").forEach(function (t) { t.textContent = ladder(t.textContent); });
    flowArrows.forEach(function (f) { f.arrow.setAttribute("data-dir", f.pipe.getAttribute("data-flow-dir") || "unknown"); });
    Object.keys(CONTROLLERS).forEach(function (tag) {
      var row = controllerRow(tag, snap);
      var t = svg.querySelector('[data-controller-mode="' + tag + '"]');
      if (t) t.textContent = row ? F.model(tag, row).modeLabel : MISSING;
    });
    refreshHits();
    markSelected();
  }

  function markSelected() {
    if (!svg) return;
    var fp = state.faceplate;
    svg.querySelectorAll(".eq[data-tag]").forEach(function (g) {
      g.setAttribute("data-selected", fp && fp.kind === "equipment" && fp.tag === g.getAttribute("data-tag") ? "true" : "false");
    });
    svg.querySelectorAll(".ctl").forEach(function (g) {
      g.setAttribute("data-selected", fp && fp.kind === "controller" && fp.tag === g.getAttribute("data-controller") ? "true" : "false");
    });
  }

  /* ---- faceplates ------------------------------------------------------ */

  function closeFaceplate() {
    if (!state.faceplate) return;
    var opener = state.faceplate.opener;
    state.faceplate = null;
    dom.faceplateLayer.textContent = "";
    markSelected();
    if (opener && document.activeElement && dom.faceplateLayer.contains(document.activeElement) === false) {
      try { opener.focus(); } catch (e) { /* ignore */ }
    }
  }

  function anchorFor(kind, tag) {
    if (!svg) return null;
    var node = kind === "controller" ? svg.querySelector('.ctl[data-controller="' + tag + '"] .ctl-balloon') : svg.querySelector('.eq[data-tag="' + tag + '"] .eq-hit');
    return node ? node.getBoundingClientRect() : null;
  }

  function workspaceRect() { return dom.workspace.getBoundingClientRect(); }

  /* Where a w x h panel goes: beside `anchor` if it fits there, else wherever
   * it covers least. `avoid` is a list of {rect, weight}; the anchor itself is
   * always avoided hardest, so a panel never sits on what it is about. Extra
   * candidates (beside other rects, the workspace corners) let it move away
   * when the space beside the anchor is taken. */
  function placeBeside(box, anchor, w, h, avoid, besideOnly) {
    var ws = workspaceRect();
    var W = ws.width, H = ws.height, gap = 14, m = 8;
    var a = anchor ? { left: anchor.left - ws.left, right: anchor.right - ws.left, top: anchor.top - ws.top, bottom: anchor.bottom - ws.top } : null;
    var avoids = (Array.isArray(avoid) ? avoid : [avoid]).filter(Boolean).map(function (r) { return r.rect ? r : { rect: r, weight: 1 }; });
    if (a) avoids.push({ rect: a, weight: 20 });
    // Every piece of equipment and every controller: covered only if nothing better is free.
    var drawn = svg ? Array.prototype.slice.call(svg.querySelectorAll(".eq .eq-hit, .ctl .ctl-balloon")) : [];
    var others = drawn.map(function (n) { return rectIn(n); }).filter(Boolean);
    others.forEach(function (r) { avoids.push({ rect: r, weight: 1, quiet: true }); });

    function around(r) {
      var cy = (r.top + r.bottom) / 2, cx = (r.left + r.right) / 2;
      return [
        { x: r.right + gap, y: cy - h / 2 },
        { x: r.left - gap - w, y: cy - h / 2 },
        { x: cx - w / 2, y: r.bottom + gap },
        { x: cx - w / 2, y: r.top - gap - h },
      ];
    }
    var options = a ? around(a) : [{ x: W / 2 - w / 2, y: H / 2 - h / 2 }];
    if (!besideOnly || !a) {
      avoids.forEach(function (o) { if (o.rect !== a && !o.quiet) options = options.concat(around(o.rect)); });
      options = options.concat([{ x: m, y: m }, { x: W - w - m, y: m }, { x: m, y: H - h - m }, { x: W - w - m, y: H - h - m }]);
    }
    options = options.map(function (o) { return { x: clamp(o.x, m, Math.max(m, W - w - m)), y: clamp(o.y, m, Math.max(m, H - h - m)) }; });

    function overlap(o, r) {
      var x = Math.max(0, Math.min(o.x + w, r.right) - Math.max(o.x, r.left));
      var y = Math.max(0, Math.min(o.y + h, r.bottom) - Math.max(o.y, r.top));
      return x * y;
    }
    function cost(o, i) {
      var covered = avoids.reduce(function (sum, v) { return sum + v.weight * overlap(o, v.rect); }, 0);
      // Prefer, at equal cover, the earlier (closer) candidate.
      return covered + i;
    }
    var best = options[0], bestCost = Infinity;
    options.forEach(function (o, i) { var c = cost(o, i); if (c < bestCost) { best = o; bestCost = c; } });
    return best;
  }

  function openFaceplate(kind, tag) {
    if (state.faceplate && state.faceplate.kind === kind && state.faceplate.tag === tag) {
      var existing = dom.faceplateLayer.querySelector(".faceplate");
      if (existing) existing.querySelector(".fp-close").focus();
      return;
    }
    closeFaceplate();
    var opener = kind === "controller" ? svg.querySelector('.ctl[data-controller="' + tag + '"]') : svg.querySelector('.eq[data-tag="' + tag + '"]');
    state.faceplate = { kind: kind, tag: tag, opener: opener, feedback: null };
    var panel = kind === "controller" ? buildControllerFaceplate(tag) : buildEquipmentFaceplate(tag);
    dom.faceplateLayer.appendChild(panel);
    updateFaceplate();
    var windows = Object.keys(state.windows).filter(function (k) { return !state.windows[k].minimized; }).map(function (k) { return rectIn(state.windows[k].node); });
    // A faceplate always sits on one side of its component.
    var pos = placeBeside(panel, anchorFor(kind, tag), panel.offsetWidth, panel.offsetHeight, windows, true);
    panel.style.left = pos.x + "px";
    panel.style.top = pos.y + "px";
    markSelected();
    panel.querySelector(".fp-close").focus();
  }

  function fpHead(tag, kind) {
    var head = el("div", { class: "fp-head" });
    var titles = el("div", { class: "fp-titles" });
    titles.appendChild(el("p", { class: "fp-tag", id: "fp-title" }, tag));
    titles.appendChild(el("p", { class: "fp-kind" }, kind));
    titles.appendChild(el("div", { class: "fp-badges", "data-fp": "badges" }));
    head.appendChild(titles);
    var close = el("button", { type: "button", class: "btn btn-quiet btn-icon fp-close", "aria-label": "Close " + tag + " faceplate" }, "✕");
    close.addEventListener("click", closeFaceplate);
    head.appendChild(close);
    return head;
  }

  var TREND_ICON = '<svg viewBox="0 0 14 10" aria-hidden="true"><path d="M1 8 L5 3 L8 6 L13 1"/></svg>';

  function readingRow(point, label) {
    var row = el("div", { class: "fp-reading" });
    var word = label || descriptor(point).replace(/^\S+\s/, "");
    row.appendChild(el("dt", {}, word.charAt(0).toUpperCase() + word.slice(1)));
    var dd = el("dd", { "data-fp-point": point }, MISSING);
    row.appendChild(dd);
    var btn = el("button", { type: "button", class: "btn-trend", "data-trend": point, "aria-label": "Show trend of " + descriptor(point) });
    btn.innerHTML = TREND_ICON + "<span>Trend</span>";
    btn.addEventListener("click", function () { openTrend(point, btn); });
    row.appendChild(btn);
    return row;
  }

  function section(title) {
    var s = el("section", { class: "fp-section" });
    if (title) s.appendChild(el("h3", {}, title));
    return s;
  }

  function feedbackLine() { return el("p", { class: "fp-feedback", "data-fp": "feedback", role: "status", hidden: "" }); }

  /* Command feedback: pending, then accepted or rejected - never "done",
   * because acceptance is not the plant reaching the value. Nothing is sent. */
  function command(tag, action, value, words) {
    var fb = dom.faceplateLayer.querySelector('[data-fp="feedback"]');
    if (!fb) return;
    var request = F.buildActionRequest(tag, action, value);
    commands.push(request.init.body);
    fb.hidden = false;
    fb.setAttribute("data-kind", "pending");
    fb.textContent = "Sending " + words + "…";
    fb.title = "Would POST " + request.url + " " + request.init.body;
    clearTimeout(command.timer);
    command.timer = setTimeout(function () {
      fb.setAttribute("data-kind", "accepted");
      fb.textContent = words.charAt(0).toUpperCase() + words.slice(1) + " accepted. The plant responds over the next steps.";
      fb.appendChild(el("small", { style: "display:block;font-weight:500;color:var(--text-muted)" }, "Prototype: not sent. The sample data will not change."));
    }, 650);
  }

  function reject(text) {
    var fb = dom.faceplateLayer.querySelector('[data-fp="feedback"]');
    if (!fb) return;
    clearTimeout(command.timer);
    fb.hidden = false;
    fb.setAttribute("data-kind", "rejected");
    fb.textContent = text;
  }

  function entryRow(id, label, unit, onSubmit) {
    var form = el("form", { class: "fp-entry" });
    form.appendChild(el("label", { for: id }, label));
    var input = el("input", { id: id, type: "text", inputmode: "decimal", autocomplete: "off", "aria-describedby": id + "-unit" });
    form.appendChild(input);
    var btn = el("button", { type: "submit", class: "btn" }, "Set");
    btn.appendChild(el("span", { id: id + "-unit", class: "visually-hidden" }, unit));
    form.appendChild(btn);
    form.addEventListener("submit", function (e) { e.preventDefault(); onSubmit(input.value, input); });
    return form;
  }

  function parsePercent(text) {
    var trimmed = String(text).trim().replace(/%$/, "");
    if (!/^[-+]?(\d+\.?\d*|\.\d+)$/.test(trimmed)) return { error: "Enter a number." };
    var v = Number(trimmed);
    if (v < 0 || v > 100) return { error: "Enter a value between 0 and 100 %." };
    return { value: v / 100 };
  }

  function loopDriving(tag) {
    return Object.keys(CONTROLLERS).find(function (c) { return CONTROLLERS[c].drives === tag; }) || null;
  }

  function buildEquipmentFaceplate(tag) {
    var dev = DEVICES[tag] || { kind: "", readings: [] };
    var panel = el("section", { class: "faceplate", role: "dialog", "aria-labelledby": "fp-title", "data-faceplate": tag });
    panel.appendChild(fpHead(tag, dev.kind));

    var readings = section("Readings");
    var dl = el("dl", { class: "fp-readings" });
    dev.readings.forEach(function (path) { dl.appendChild(readingRow(pointOf(path))); });
    readings.appendChild(dl);
    panel.appendChild(readings);

    var loop = loopDriving(tag);
    var operate = section("Operate " + tag);
    if (loop) {
      var note = el("p", { class: "fp-note" });
      note.innerHTML = "<strong>" + tag + " is driven by " + loop + ".</strong> Operate it from the controller; a direct command would be overwritten on the next step.";
      operate.appendChild(note);
      var open = el("button", { type: "button", class: "btn", style: "margin-top:8px" }, "Open " + loop);
      open.addEventListener("click", function () { openFaceplate("controller", loop); });
      operate.appendChild(open);
    } else if (dev.startStop || dev.target) {
      if (dev.startStop) {
        var actions = el("div", { class: "fp-actions", role: "group", "aria-label": tag + " start and stop" });
        var start = el("button", { type: "button", class: "btn btn-equip", "data-equip-action": "start" });
        start.innerHTML = '<span class="btn-glyph" aria-hidden="true">▶</span>Start ' + tag;
        var stop = el("button", { type: "button", class: "btn btn-equip", "data-equip-action": "stop" });
        stop.innerHTML = '<span class="btn-glyph" aria-hidden="true">■</span>Stop ' + tag;
        start.addEventListener("click", function () { command(tag, "start", null, "start " + tag); });
        stop.addEventListener("click", function () { command(tag, "stop", null, "stop " + tag); });
        actions.appendChild(start);
        actions.appendChild(stop);
        operate.appendChild(actions);
      }
      if (dev.target) {
        operate.appendChild(el("p", { class: "fp-bar-caption", "data-fp": "barcaption" }));
        var bar = el("div", { class: "fp-bar", "data-fp": "bar", "aria-hidden": "true" });
        bar.appendChild(el("span"));
        bar.appendChild(el("i", { class: "target" }));
        operate.appendChild(bar);
        operate.appendChild(entryRow("fp-" + tag + "-target", dev.target.label + " (%)", "percent", function (text) {
          var parsed = parsePercent(text);
          if (parsed.error) { reject(parsed.error); return; }
          command(tag, dev.target.action, parsed.value, dev.target.label.toLowerCase() + " " + (parsed.value * 100).toFixed(1) + "% for " + tag);
        }));
      }
    } else {
      operate.appendChild(el("p", { class: "fp-note" }, "No operator actions on " + tag + ". Its level and pressure follow what flows in and out."));
    }
    operate.appendChild(feedbackLine());
    panel.appendChild(operate);
    return panel;
  }

  function buildControllerFaceplate(tag) {
    var c = CONTROLLERS[tag];
    var panel = el("section", { class: "faceplate", role: "dialog", "aria-labelledby": "fp-title", "data-faceplate": tag });
    panel.appendChild(fpHead(tag, c.kind));

    var readings = section("Loop");
    if (c.proposed) {
      var p = el("p", { class: "fp-note fp-proposed" });
      p.innerHTML = "<strong>Proposed loop.</strong> The simulator cannot run this loop yet. PV and OUT are live readings; SP is the design value.";
      readings.appendChild(p);
    }
    var dl = el("dl", { class: "fp-readings" });
    dl.appendChild(readingRow(c.pv, "Process value (PV)"));
    if (c.proposed) {
      var spRow = el("div", { class: "fp-reading" });
      spRow.appendChild(el("dt", {}, "Setpoint (SP)"));
      spRow.appendChild(el("dd", {}, detail(c.pv, c.sp)));
      spRow.appendChild(el("span"));
      dl.appendChild(spRow);
    } else {
      dl.appendChild(readingRow(tag + ".sp", "Setpoint (SP)"));
    }
    dl.appendChild(readingRow(c.out, "Output (OUT)"));
    readings.appendChild(dl);
    var bar = el("div", { class: "fp-bar", "data-fp": "outbar", "aria-hidden": "true" });
    bar.appendChild(el("span"));
    readings.appendChild(bar);
    var wiring = el("p", { class: "fp-note" });
    wiring.appendChild(document.createTextNode("Measures "));
    wiring.appendChild(locateLink(c.measures));
    wiring.appendChild(document.createTextNode(" \u00B7 Drives "));
    wiring.appendChild(locateLink(c.drives));
    readings.appendChild(wiring);
    panel.appendChild(readings);

    var operate = section("Operate " + tag);
    var mode = el("div", { class: "fp-mode", role: "group", "aria-label": tag + " mode", "data-fp": "mode" });
    [["manual", "MAN"], ["auto", "AUTO"]].forEach(function (pair) {
      var b = el("button", { type: "button", "data-mode": pair[0], "aria-pressed": "false" }, pair[1]);
      b.addEventListener("click", function () { command(tag, pair[0], null, "switch " + tag + " to " + pair[1]); });
      mode.appendChild(b);
    });
    operate.appendChild(mode);
    var spUnit = unitOf(c.pv);
    operate.appendChild(entryRow("fp-" + tag + "-sp", "Setpoint" + (spUnit === "%" ? " (%)" : ""), spUnit, function (text) {
      var r = F.parseEntry("sp", text, controllerRow(tag));
      if (r.error) { reject(r.error); return; }
      var value = spUnit === "%" ? r.value / 100 : r.value;
      if (spUnit === "%" && value > 1) { reject("Enter a value between 0 and 100 %."); return; }
      command(tag, r.action, value, "setpoint " + detail(c.pv, value) + " for " + tag);
    }));
    var outForm = entryRow("fp-" + tag + "-out", "Output (%)", "percent", function (text) {
      var r = F.parseEntry("out", text, controllerRow(tag));
      if (r.error) { reject(r.error); return; }
      command(tag, r.action, r.value, "output " + (r.value * 100).toFixed(1) + "% for " + tag);
    });
    outForm.setAttribute("data-fp", "outform");
    operate.appendChild(outForm);
    operate.appendChild(el("p", { class: "fp-note", "data-fp": "outnote" }));
    operate.appendChild(feedbackLine());
    panel.appendChild(operate);
    return panel;
  }

  function locateLink(tag) {
    var b = el("button", { type: "button", class: "btn-trend", "aria-label": "Locate " + tag }, tag);
    b.addEventListener("click", function () { locate(tag); });
    return b;
  }

  /* Locate: point at a tag on the canvas without opening anything. */
  function locate(tag) {
    if (!svg) return;
    var target = svg.querySelector('.eq[data-tag="' + tag + '"]');
    var node = target;
    if (!target) {
      // A node: its pressure reading.
      var r = readouts.find(function (x) { return x.point === tag + ".pressure"; });
      node = r && r.wrap;
    }
    if (!node) return;
    node.setAttribute("data-located", "true");
    if (node.classList.contains("readout")) node.setAttribute("data-open", "true");
    node.focus();
    setTimeout(function () { node.removeAttribute("data-located"); refreshHits(); }, 1600);
  }

  function updateFaceplate() {
    var fp = state.faceplate;
    if (!fp) return;
    var panel = dom.faceplateLayer.querySelector(".faceplate");
    if (!panel) return;
    var snap = snapshot();
    panel.querySelectorAll("[data-fp-point]").forEach(function (dd) {
      var p = dd.getAttribute("data-fp-point");
      dd.textContent = detail(p, pointValue(p));
      dd.setAttribute("data-band", bandOfPoint(p));
    });
    var badges = panel.querySelector('[data-fp="badges"]');
    badges.textContent = "";
    if (fp.kind === "equipment") {
      var st = G.deviceState(snap, fp.tag);
      if (st === "running" || st === "stopped") {
        badges.appendChild(el("span", { class: "state-badge", "data-state": st }, st === "running" ? "▶ RUN" : "■ STOP"));
      }
      var band = G.worstBand(snap, fp.tag);
      if (band) badges.appendChild(el("span", { class: "band-badge", "data-severity": band.severity }, ({ warning: "●", alarm: "◆", trip: "▲" })[band.severity] + " " + ladder(band.band.toUpperCase())));
      var dev = DEVICES[fp.tag] || {};
      if (dev.startStop) {
        panel.querySelector('[data-equip-action="start"]').disabled = st === "running";
        panel.querySelector('[data-equip-action="stop"]').disabled = st === "stopped";
      }
      var bar = panel.querySelector('[data-fp="bar"]');
      if (bar && dev.target) {
        var field = dev.target.field.replace("_target", "");
        var actual = valueAt("equipment." + fp.tag + "." + field, snap);
        var target = valueAt("equipment." + fp.tag + "." + dev.target.field, snap);
        bar.querySelector("span").style.width = (isNum(actual) ? clamp(actual, 0, 1) * 100 : 0) + "%";
        bar.querySelector(".target").style.left = "calc(" + (isNum(target) ? clamp(target, 0, 1) * 100 : 0) + "% - 1px)";
        var word = field.charAt(0).toUpperCase() + field.slice(1);
        panel.querySelector('[data-fp="barcaption"]').textContent = word + " " + detail(fp.tag + "." + field, actual) + " \u00B7 target " + detail(fp.tag + "." + dev.target.field, target);
        var input = panel.querySelector(".fp-entry input");
        if (input && isNum(target)) input.placeholder = (target * 100).toFixed(1);
      }
    } else {
      var row = controllerRow(fp.tag, snap);
      var m = F.model(fp.tag, row);
      badges.appendChild(el("span", { class: "state-badge", "data-mode": m.mode || "" }, m.modeLabel));
      if (CONTROLLERS[fp.tag].proposed) badges.appendChild(el("span", { class: "state-badge proposed-badge" }, "PROPOSED"));
      panel.querySelectorAll('[data-fp="mode"] button').forEach(function (b) {
        b.setAttribute("aria-pressed", (b.getAttribute("data-mode") === "manual") === m.manual ? "true" : "false");
      });
      var outBar = panel.querySelector('[data-fp="outbar"] span');
      outBar.style.width = (m.outPercent || 0) + "%";
      var outInput = panel.querySelector('[data-fp="outform"] input');
      var outBtn = panel.querySelector('[data-fp="outform"] button');
      outInput.disabled = !m.manual;
      var cfg = CONTROLLERS[fp.tag];
      if (row && isNum(row.sp)) panel.querySelector('#fp-' + fp.tag + '-sp').placeholder = (unitOf(cfg.pv) === "%" ? row.sp * 100 : row.sp).toFixed(1);
      if (row && isNum(row.out)) outInput.placeholder = (m.outPercent || 0).toFixed(1);
      outBtn.disabled = !m.manual;
      panel.querySelector('[data-fp="outnote"]').textContent = m.manual ? "" : "Output is set by the loop in AUTO. Switch to MAN to set it.";
    }
  }

  /* ---- trend windows --------------------------------------------------- */

  function readStore() {
    try { return JSON.parse(sessionStorage.getItem(STORE_KEY) || "{}") || {}; } catch (e) { return {}; }
  }

  function writeStore() {
    var stored = readStore();
    Object.keys(state.windows).forEach(function (p) {
      var w = state.windows[p];
      stored[p] = { x: w.x, y: w.y, w: w.w, h: w.h, span: w.span };
    });
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify(stored)); } catch (e) { /* storage unavailable: geometry is kept for this page only */ }
  }

  function rectIn(node) {
    if (!node || !node.getBoundingClientRect) return null;
    var r = node.getBoundingClientRect();
    var ws = workspaceRect();
    return { left: r.left - ws.left, right: r.right - ws.left, top: r.top - ws.top, bottom: r.bottom - ws.top };
  }

  function openTrend(point, origin) {
    var existing = state.windows[point];
    if (existing) {
      restoreWindow(point);
      raise(existing);
      existing.node.animate && !reducedMotion() && existing.node.animate([{ transform: "scale(1.02)" }, { transform: "none" }], { duration: 180 });
      existing.node.querySelector(".tw-bar").focus();
      return;
    }
    var stored = readStore()[point];
    var ws = workspaceRect();
    var w = stored ? stored.w : Math.min(420, ws.width - 16);
    var h = stored ? stored.h : Math.min(290, ws.height - 16);
    var pos;
    if (stored) {
      pos = { x: stored.x, y: stored.y };
    } else {
      // Beside the equipment the reading belongs to (or the faceplate's own
      // equipment), clear of the faceplate and of other open windows.
      var anchorNode = origin && origin.closest ? (origin.closest(".readout, .eq, .ctl") || origin) : origin;
      var fp = dom.faceplateLayer.querySelector(".faceplate");
      if (fp && origin && fp.contains(origin) && state.faceplate) {
        anchorNode = state.faceplate.kind === "controller"
          ? svg.querySelector('.ctl[data-controller="' + state.faceplate.tag + '"] .ctl-balloon')
          : svg.querySelector('.eq[data-tag="' + state.faceplate.tag + '"] .eq-hit');
      }
      var parentEq = anchorNode && anchorNode.closest ? anchorNode.closest(".eq") : null;
      if (parentEq && parentEq.querySelector(".eq-hit")) anchorNode = parentEq.querySelector(".eq-hit");
      var anchor = anchorNode ? anchorNode.getBoundingClientRect() : null;
      var avoid = [fp ? { rect: rectIn(fp), weight: 4 } : null].concat(Object.keys(state.windows).filter(function (k) { return !state.windows[k].minimized; }).map(function (k) { return { rect: rectIn(state.windows[k].node), weight: 3 }; }));
      pos = placeBeside(null, anchor, w, h, avoid);
      // Cascade if a window already sits there.
      var taken = Object.keys(state.windows).map(function (k) { return state.windows[k]; });
      while (taken.some(function (o) { return Math.abs(o.x - pos.x) < 12 && Math.abs(o.y - pos.y) < 12; })) {
        pos = { x: pos.x + 28, y: pos.y + 28 };
      }
    }
    var win = { point: point, x: pos.x, y: pos.y, w: w, h: h, span: stored ? stored.span : T.DEFAULT_SPAN, minimized: false, hold: null };
    win.node = buildWindow(win);
    state.windows[point] = win;
    dom.windowLayer.appendChild(win.node);
    applyGeometry(win);
    raise(win);
    renderWindow(win);
    writeStore();
    refreshHits();
    win.node.querySelector(".tw-bar").focus();
  }

  function buildWindow(win) {
    var node = el("section", { class: "trend-window", role: "dialog", "aria-label": "Trend of " + descriptor(win.point), "data-point": win.point });
    var bar = el("div", { class: "tw-bar", tabindex: "0", "aria-label": "Trend of " + descriptor(win.point) + ". Drag, or use arrow keys to move and Shift with arrow keys to resize." });
    var name = el("span", { class: "tw-name" }, descriptor(win.point));
    bar.appendChild(name);
    var min = el("button", { type: "button", class: "tw-ctl", "aria-label": "Minimise trend of " + descriptor(win.point), title: "Minimise" }, "–");
    var close = el("button", { type: "button", class: "tw-ctl", "aria-label": "Close trend of " + descriptor(win.point), title: "Close" }, "✕");
    min.addEventListener("click", function () { minimizeWindow(win.point); });
    close.addEventListener("click", function () { closeWindow(win.point); });
    bar.appendChild(min);
    bar.appendChild(close);
    node.appendChild(bar);

    var meta = el("div", { class: "tw-meta" });
    meta.appendChild(el("span", { class: "tw-value", "data-tw": "value", "aria-live": "off" }, MISSING));
    var spans = el("div", { class: "tw-spans", role: "group", "aria-label": "Time range" });
    T.SPANS.forEach(function (m) {
      var b = el("button", { type: "button", "data-span": m, "aria-pressed": m === win.span ? "true" : "false", "aria-label": m + " minute" + (m > 1 ? "s" : "") }, m + "m");
      b.addEventListener("click", function () {
        win.span = m; win.hold = null;
        spans.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
        renderWindow(win); writeStore();
      });
      spans.appendChild(b);
    });
    meta.appendChild(spans);
    node.appendChild(meta);
    node.appendChild(el("div", { class: "tw-chart", "data-tw": "chart" }));
    node.appendChild(el("p", { class: "tw-sample" }, "Sample data · own vertical scale"));
    var grip = el("div", { class: "tw-resize", "aria-hidden": "true" });
    node.appendChild(grip);

    node.addEventListener("pointerdown", function () { raise(win); }, true);
    dragHandle(bar, win, "move");
    dragHandle(grip, win, "resize");
    bar.addEventListener("keydown", function (e) {
      var step = e.altKey ? 2 : 16;
      var dx = { ArrowLeft: -step, ArrowRight: step }[e.key] || 0;
      var dy = { ArrowUp: -step, ArrowDown: step }[e.key] || 0;
      if (!dx && !dy) return;
      e.preventDefault();
      if (e.shiftKey) { win.w += dx; win.h += dy; } else { win.x += dx; win.y += dy; }
      applyGeometry(win); renderWindow(win); writeStore();
    });
    if (window.ResizeObserver) {
      new ResizeObserver(function () { renderWindow(win); }).observe(node.querySelector('[data-tw="chart"]'));
    }
    return node;
  }

  function dragHandle(handle, win, mode) {
    handle.addEventListener("pointerdown", function (e) {
      if (e.button !== 0 || e.target.closest("button")) return;
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      var start = { px: e.clientX, py: e.clientY, x: win.x, y: win.y, w: win.w, h: win.h };
      function move(ev) {
        var dx = ev.clientX - start.px, dy = ev.clientY - start.py;
        if (mode === "move") { win.x = start.x + dx; win.y = start.y + dy; } else { win.w = start.w + dx; win.h = start.h + dy; }
        applyGeometry(win);
      }
      function up() {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        handle.removeEventListener("pointercancel", up);
        renderWindow(win);
        writeStore();
      }
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
      handle.addEventListener("pointercancel", up);
    });
  }

  /* Keep a window's title bar and close control reachable: the window may hang
   * off the right or bottom, but never so far that its bar leaves the workspace. */
  function applyGeometry(win) {
    var ws = workspaceRect();
    win.w = clamp(win.w, 220, Math.max(220, ws.width - 16));
    win.h = clamp(win.h, 170, Math.max(170, ws.height - 16));
    win.x = clamp(win.x, 8, Math.max(8, ws.width - win.w - 8));
    win.y = clamp(win.y, 8, Math.max(8, ws.height - 48));
    win.node.style.left = win.x + "px";
    win.node.style.top = win.y + "px";
    win.node.style.width = win.w + "px";
    win.node.style.height = win.h + "px";
  }

  function clampAllWindows() {
    Object.keys(state.windows).forEach(function (p) { var w = state.windows[p]; if (!w.minimized) applyGeometry(w); });
    var panel = dom.faceplateLayer.querySelector(".faceplate");
    if (panel) {
      var ws = workspaceRect();
      panel.style.left = clamp(parseFloat(panel.style.left) || 8, 8, Math.max(8, ws.width - panel.offsetWidth - 8)) + "px";
      panel.style.top = clamp(parseFloat(panel.style.top) || 8, 8, Math.max(8, ws.height - panel.offsetHeight - 8)) + "px";
    }
  }

  function raise(win) {
    state.z += 1;
    win.node.style.zIndex = state.z;
    Object.keys(state.windows).forEach(function (p) { state.windows[p].node.setAttribute("data-active", state.windows[p] === win ? "true" : "false"); });
  }

  function minimizeWindow(point) {
    var win = state.windows[point];
    if (!win) return;
    win.minimized = true;
    win.node.hidden = true;
    renderTray();
    var chip = dom.tray.querySelector('[data-point="' + point + '"]');
    if (chip) chip.focus();
  }

  function restoreWindow(point) {
    var win = state.windows[point];
    if (!win || !win.minimized) return;
    win.minimized = false;
    win.node.hidden = false;
    applyGeometry(win);
    renderWindow(win);
    renderTray();
  }

  function closeWindow(point, quiet) {
    var win = state.windows[point];
    if (!win) return;
    writeStore();
    win.node.remove();
    delete state.windows[point];
    renderTray();
    if (!quiet) refreshHits();
  }

  function renderTray() {
    var minimized = Object.keys(state.windows).filter(function (p) { return state.windows[p].minimized; });
    dom.tray.hidden = minimized.length === 0;
    dom.tray.textContent = "";
    minimized.forEach(function (p) {
      var b = el("button", { type: "button", class: "tray-chip", "data-point": p, "aria-label": "Restore trend of " + descriptor(p) });
      b.innerHTML = TREND_ICON.replace('aria-hidden="true"', 'aria-hidden="true" width="14" height="10" style="fill:none;stroke:currentColor;stroke-width:1.6"') + "<span></span><span aria-hidden=\"true\">▴</span>";
      b.querySelector("span").textContent = descriptor(p) + " " + detail(p, pointValue(p));
      b.addEventListener("click", function () {
        restoreWindow(p);
        raise(state.windows[p]);
        state.windows[p].node.querySelector(".tw-bar").focus();
      });
      dom.tray.appendChild(b);
    });
  }

  function historyFor(point, from, to) {
    var samples = recording().trend_history[point] || [];
    var scale = isPercent(point) ? 100 : 1;
    return samples.filter(function (s) { return s[0] >= from && s[0] <= to; }).map(function (s) {
      return [s[0], isNum(s[1]) ? s[1] * scale : s[1]];
    });
  }

  function limitsFor(point) {
    var raw = recording().trend_points.limits[point];
    if (!raw) return null;
    var scale = isPercent(point) ? 100 : 1;
    var out = {};
    Object.keys(raw).forEach(function (k) { out[k] = raw[k] * scale; });
    return out;
  }

  /* A held scale: it widens when a reading leaves it and never narrows while
   * the window shows the same span, so a change can be judged by eye. */
  function heldLimits(win, samples, limits) {
    var values = samples.map(function (s) { return s[1]; }).filter(isNum);
    Object.keys(limits || {}).forEach(function (k) { values.push(limits[k]); });
    if (!values.length) return limits || {};
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    if (win.hold) { lo = Math.min(lo, win.hold.lo); hi = Math.max(hi, win.hold.hi); }
    win.hold = { lo: lo, hi: hi };
    var out = {};
    Object.keys(limits || {}).forEach(function (k) { out[k] = limits[k]; });
    out.hold_lo = lo;
    out.hold_hi = hi;
    return out;
  }

  function renderWindow(win) {
    if (!win || win.minimized || !win.node.isConnected) return;
    var point = win.point;
    var value = pointValue(point);
    var valueEl = win.node.querySelector('[data-tw="value"]');
    var text = detail(point, value);
    var unit = unitOf(point);
    var number = unit && text !== MISSING ? text.replace(unit === "%" ? "%" : " " + unit, "") : text;
    valueEl.textContent = number;
    if (unit && text !== MISSING) valueEl.appendChild(el("small", {}, unit));
    valueEl.setAttribute("data-band", bandOfPoint(point));

    var chart = win.node.querySelector('[data-tw="chart"]');
    var w = Math.max(160, Math.floor(chart.clientWidth - 12));
    var h = Math.max(80, Math.floor(chart.clientHeight - 12));
    var t0 = startTime();
    var elapsed = now() - t0;
    var spanS = win.span * 60;
    var range = elapsed < spanS ? { from: 0, to: spanS } : { from: elapsed - spanS, to: elapsed };
    var samples = historyFor(point, t0 + range.from, t0 + Math.min(elapsed, range.to)).map(function (s) { return [s[0] - t0, s[1]]; });
    var limits = limitsFor(point);
    var tag = tagOf(point), field = fieldOf(point);
    var markers = T.markersFor(alarmEntries().filter(function (e) {
      return e.tag === tag && (!e.data || e.data.pv === field);
    }).map(function (e) { return Object.assign({}, e, { sim_time: e.sim_time - t0 }); }), range);
    chart.innerHTML = ladder(T.renderChart({
      width: w,
      height: h,
      window: range,
      pens: [{ point: point, samples: samples }],
      limits: (function () { var o = {}; o[point] = heldLimits(win, samples, limits); return o; })(),
      focus: point,
      markers: markers,
    }));
    var svgNode = chart.querySelector("svg");
    if (svgNode) { svgNode.setAttribute("aria-label", "Trend of " + descriptor(point) + ", " + win.span + " minute range, time since the run began, sample data"); svgNode.removeAttribute("width"); svgNode.removeAttribute("height"); }
  }

  function reducedMotion() { return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches; }

  /* ---- alarm overlay --------------------------------------------------- */

  function openOverlay() {
    if (state.view !== "console") go("console");
    state.overlayOpen = true;
    dom.overlay.hidden = false;
    dom.ribbonAlarms.setAttribute("aria-expanded", "true");
    // The workspace stays as it was; the overlay only covers it.
    dom.workspace.setAttribute("inert", "");
    renderOverlay();
    $("alarm-overlay-close").focus();
  }

  function closeOverlay(quiet) {
    if (!state.overlayOpen) return;
    state.overlayOpen = false;
    dom.overlay.hidden = true;
    dom.ribbonAlarms.setAttribute("aria-expanded", "false");
    dom.workspace.removeAttribute("inert");
    if (!quiet) dom.ribbonAlarms.focus();
  }

  function filters() {
    var pr = Array.prototype.slice.call(document.querySelectorAll('input[name="priority"]')).filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
    return { priority: pr, state: $("filter-state").value, tag: $("filter-tag").value, search: $("filter-search").value.trim().toLowerCase() };
  }

  function renderOverlay() {
    if (!state.overlayOpen) return;
    var alarms = A.sortAlarms(liveAlarms(), "priority", false);
    var counts = { critical: 0, high: 0, low: 0 };
    alarms.forEach(function (a) { counts[a.priority] += 1; });
    Object.keys(counts).forEach(function (p) { document.querySelector('[data-count="' + p + '"]').textContent = counts[p]; });

    var tagSelect = $("filter-tag");
    var current = tagSelect.value;
    var tags = alarms.map(function (a) { return a.tag; }).filter(function (t, i, all) { return all.indexOf(t) === i; }).sort();
    var wanted = ["all"].concat(tags).join("|");
    if (tagSelect.getAttribute("data-tags") !== wanted) {
      tagSelect.textContent = "";
      tagSelect.appendChild(el("option", { value: "all" }, "All equipment"));
      tags.forEach(function (t) { tagSelect.appendChild(el("option", { value: t }, t)); });
      tagSelect.value = tags.indexOf(current) >= 0 ? current : "all";
      tagSelect.setAttribute("data-tags", wanted);
    }

    var f = filters();
    var shown = alarms.filter(function (a) {
      if (f.priority.indexOf(a.priority) < 0) return false;
      if (f.state === "unacked" && a.state === "acked") return false;
      if (f.state === "acked" && a.state !== "acked") return false;
      if (f.tag !== "all" && a.tag !== f.tag) return false;
      if (f.search && (a.tag + " " + a.message).toLowerCase().indexOf(f.search) < 0) return false;
      return true;
    });
    var unack = alarms.filter(function (a) { return a.state !== "acked"; }).length;
    $("alarm-overlay-sub").textContent = alarms.length + " active · " + unack + " not acknowledged · showing " + shown.length;

    var tbody = $("alarm-rows");
    var focusedId = document.activeElement && document.activeElement.getAttribute("data-row-action") ? document.activeElement.getAttribute("data-row-action") + "|" + document.activeElement.getAttribute("data-alarm") : null;
    tbody.textContent = "";
    shown.forEach(function (a) {
      var tr = el("tr", { "data-state": a.state, "data-alarm-id": a.id });
      var badge = el("td");
      badge.appendChild(el("span", { class: "sev-badge", "data-priority": a.priority }, PRIORITY_GLYPH[a.priority] + " " + PRIORITY_LABEL[a.priority]));
      tr.appendChild(badge);
      tr.appendChild(el("td", { class: "num" }, mmss(a.raisedAt - startTime())));
      tr.appendChild(el("td", { class: "num" }, a.tag));
      tr.appendChild(el("td", { class: "msg" }, a.message));
      var returned = a.state === "rtn_unack";
      tr.appendChild(el("td", { "data-label": "Condition" }, "")).appendChild(el("span", { class: "cond", "data-cond": returned ? "returned" : "active" }, returned ? "Returned to normal" : "Active"));
      var acked = a.state === "acked";
      tr.appendChild(el("td", { "data-label": "Acknowledged" }, "")).appendChild(el("span", { class: "ackd", "data-ack": acked ? "yes" : "no" }, acked ? "Yes" : "No"));
      var actions = el("td");
      var wrap = el("div", { class: "row-actions" });
      if (!acked) {
        var ack = el("button", { type: "button", class: "btn", "data-row-action": "ack", "data-alarm": a.id, "aria-label": "Acknowledge " + a.message }, "Acknowledge");
        ack.addEventListener("click", function () { acknowledge(a.id); renderAll(); });
        wrap.appendChild(ack);
      }
      var pv = alarmPoint(a);
      if (pv) {
        var tr2 = el("button", { type: "button", class: "btn btn-quiet", "data-row-action": "trend", "data-alarm": a.id, "aria-label": "Show trend of " + descriptor(pv) }, "Show trend");
        tr2.addEventListener("click", function () { closeOverlay(true); openTrend(pv, svg && svg.querySelector('.eq[data-tag="' + a.tag + '"]')); });
        wrap.appendChild(tr2);
      }
      var loc = el("button", { type: "button", class: "btn btn-quiet", "data-row-action": "locate", "data-alarm": a.id, "aria-label": "Locate " + a.tag }, "Locate");
      loc.addEventListener("click", function () { closeOverlay(true); locate(a.tag); });
      wrap.appendChild(loc);
      actions.appendChild(wrap);
      tr.appendChild(actions);
      tbody.appendChild(tr);
    });
    var empty = $("alarm-empty");
    empty.hidden = shown.length > 0;
    empty.textContent = alarms.length ? "No alarms match these filters." : "No active alarms.";
    $("ack-visible").disabled = !shown.some(function (a) { return a.state !== "acked"; });
    if (focusedId) {
      var parts = focusedId.split("|");
      var again = tbody.querySelector('[data-row-action="' + parts[0] + '"][data-alarm="' + parts[1] + '"]') || tbody.querySelector("[data-row-action]");
      if (again) again.focus();
    }
    renderOverlay.shown = shown;
  }

  function alarmPoint(alarm) {
    var raw = recording().alarms.find(function (e) { return e.id === alarm.id && e.type === "alarm"; });
    var field = raw && raw.data && raw.data.pv;
    return field ? alarm.tag + "." + field : null;
  }

  /* ---- clock ----------------------------------------------------------- */

  function tick(dtSeconds) {
    var s = state.session;
    var rec = recording();
    var advancing = s.phase === "running" || s.phase === "idle";
    if (advancing && dtSeconds > 0) {
      var maxElapsed = (rec.frames.length - 1) * S.source.frame_every_s;
      s.elapsed = Math.min(maxElapsed, s.elapsed + dtSeconds);
      if (s.phase === "running" && s.elapsed >= maxElapsed && rec.result && rec.result.phase === "complete") s.phase = "complete";
    }
    renderAll();
  }

  function renderAll() {
    var snap = snapshot();
    updateCanvas(snap);
    updateFaceplate();
    Object.keys(state.windows).forEach(function (p) { renderWindow(state.windows[p]); });
    renderTray();
    renderRibbon();
    if (state.overlayOpen) renderOverlay();
    if (state.view === "launch") {
      var strip = $("session-strip");
      if (!strip.hidden) $("session-text").textContent = "Your plant: " + entry(state.session.key).title + " · " + R.phaseLabel(S.phase_labels, state.session.phase) + " " + mmss(state.session.elapsed);
    }
  }

  function renderGuard() {
    var g = $("review-guard");
    if (g) g.textContent = "Requests to /api blocked: " + blocked.length + (blocked.length ? " (" + blocked.slice(-3).join(", ") + ")" : "");
  }

  /* ---- review harness -------------------------------------------------- */

  function scenarioKey(title) { return S.catalogue.find(function (c) { return c.title === title; }).key; }
  var LEVEL_RUN = scenarioKey("Rising drum level");
  var SURGE_RUN = scenarioKey("Feed surge");

  function resetConsole(elapsed, key) {
    closeOverlay(true);
    beginSession(key || LEVEL_RUN, "running");
    state.session.elapsed = elapsed;
    state.speed = 0;
    markSpeed();
    try { sessionStorage.removeItem(STORE_KEY); } catch (e) { /* ignore */ }
    go("console");
    tick(0);
  }

  function readoutNode(point) { var r = readouts.find(function (x) { return x.point === point; }); return r && r.wrap; }

  var DEMOS = [
    ["Landing and scenario selection", function () { closeOverlay(true); go("launch"); $("scenario-picker").hidden = true; $("choose-another").setAttribute("aria-expanded", "false"); }],
    ["Landing: other scenarios revealed", function () { go("launch"); $("scenario-picker").hidden = false; $("choose-another").setAttribute("aria-expanded", "true"); selectScenario(S.catalogue[2].key); }],
    ["Clean canvas, panels closed", function () { resetConsole(120); }],
    ["Equipment faceplate open (V-301)", function () { resetConsole(120); openFaceplate("equipment", "V-301"); }],
    ["Controller faceplate open (LIC-301, proposed)", function () { resetConsole(120); openFaceplate("controller", "LIC-301"); }],
    ["Trend opened from a faceplate", function () { resetConsole(300); openFaceplate("equipment", "V-301"); var b = document.querySelector('[data-trend="V-301.level"]'); openTrend("V-301.level", b); }],
    ["Trend opened from a reading", function () { resetConsole(300); openTrend("V-301.level", readoutNode("V-301.level")); }],
    ["Two trend windows", function () { resetConsole(300); openTrend("V-301.level", readoutNode("V-301.level")); openTrend("B-LV-301.flow", readoutNode("B-LV-301.flow")); }],
    ["Trend moved, resized and one minimised", function () {
      resetConsole(300);
      openTrend("V-301.level", readoutNode("V-301.level"));
      openTrend("B-LV-301.flow", readoutNode("B-LV-301.flow"));
      var a = state.windows["V-301.level"];
      a.x = 24; a.y = 24; a.w = 520; a.h = 300; applyGeometry(a); renderWindow(a); writeStore();
      minimizeWindow("B-LV-301.flow");
    }],
    ["Minimised trend restored", function () { DEMOS[8][1](); restoreWindow("B-LV-301.flow"); raise(state.windows["B-LV-301.flow"]); }],
    ["Active alarm ribbon", function () { resetConsole(420, SURGE_RUN); }],
    ["Alarm overlay open", function () { resetConsole(420, SURGE_RUN); openTrend("V-301.level", readoutNode("V-301.level")); openTrend("V-301.pressure", readoutNode("V-301.pressure")); openOverlay(); }],
    ["Alarm overlay filtered, one acknowledged", function () {
      DEMOS[11][1]();
      var first = liveAlarms().find(function (a) { return /level/.test(a.message); });
      if (first) acknowledge(first.id);
      $("filter-state").value = "acked";
      renderAll();
    }],
    ["Overlay closed: workspace as it was", function () { DEMOS[11][1](); closeOverlay(); }],
    ["Run finished (Drum emptying)", function () {
      closeOverlay(true);
      beginSession(scenarioKey("Drum emptying"), "running"); state.session.elapsed = 9999; state.speed = 0; markSpeed(); go("console"); tick(0.001);
    }],
  ];

  function markSpeed() {
    document.querySelectorAll("[data-speed]").forEach(function (b) { b.setAttribute("aria-pressed", Number(b.getAttribute("data-speed")) === state.speed ? "true" : "false"); });
  }

  window.Prototype = {
    demo: function (n) { DEMOS[n][1](); return DEMOS[n][0]; },
    demos: function () { return DEMOS.map(function (d) { return d[0]; }); },
    state: state,
    blocked: blocked,
    commands: commands,
    windowGeometry: function () { return Object.keys(state.windows).map(function (p) { var w = state.windows[p]; return { point: p, x: w.x, y: w.y, w: w.w, h: w.h, minimized: w.minimized }; }); },
  };

  /* ---- wiring ---------------------------------------------------------- */

  function init() {
    dom.ribbon = $("ribbon");
    dom.runTitle = $("run-title");
    dom.runState = $("run-state");
    dom.runStateText = $("run-state-text");
    dom.runClock = $("run-clock");
    dom.runButton = $("run-button");
    dom.runConfirm = $("run-confirm");
    dom.changeScenario = $("change-scenario");
    dom.ribbonAlarms = $("ribbon-alarms");
    dom.alarmSummary = $("alarm-summary-text");
    dom.notice = $("notice");
    dom.viewLaunch = $("view-launch");
    dom.viewConsole = $("view-console");
    dom.workspace = $("workspace");
    dom.faceplateLayer = $("faceplate-layer");
    dom.windowLayer = $("window-layer");
    dom.tray = $("tray");
    dom.overlay = $("alarm-overlay");

    $("review-source").textContent = "Sample data recorded from the real engine at " + S.source.commit + ", " + S.source.seconds + " s per run, no operator action. Nothing here is sent to the app.";
    var list = $("review-states");
    DEMOS.forEach(function (d, i) {
      var li = el("li");
      var b = el("button", { type: "button" }, d[0]);
      b.addEventListener("click", function () { DEMOS[i][1](); });
      li.appendChild(b);
      list.appendChild(li);
    });
    $("review-toggle").addEventListener("click", function () {
      var body = $("review-body");
      body.hidden = !body.hidden;
      this.setAttribute("aria-expanded", body.hidden ? "false" : "true");
    });
    document.querySelectorAll("[data-speed]").forEach(function (b) {
      b.addEventListener("click", function () { state.speed = Number(b.getAttribute("data-speed")); markSpeed(); });
    });
    markSpeed();
    renderGuard();

    document.querySelectorAll("[data-nav]").forEach(function (a) {
      a.addEventListener("click", function (e) { e.preventDefault(); go(a.getAttribute("data-nav")); });
    });

    $("hero-start").addEventListener("click", startSelected);
    $("choose-another").addEventListener("click", function () {
      var picker = $("scenario-picker");
      picker.hidden = !picker.hidden;
      this.setAttribute("aria-expanded", picker.hidden ? "false" : "true");
      if (!picker.hidden) { var sel = picker.querySelector('[aria-pressed="true"]'); if (sel) sel.focus(); }
    });
    $("scenario-grid").addEventListener("click", function (e) {
      var b = e.target.closest(".scenario-option");
      if (b) selectScenario(b.getAttribute("data-key"));
    });
    $("free-play").addEventListener("click", function () {
      var s = state.session;
      if (s.key !== FREE && s.phase === "running") {
        if (!window.confirm("A run is in progress. Abort it and return to free play?")) return;
      }
      beginSession(FREE, "idle");
      go("console");
    });
    $("return-to-console").addEventListener("click", function () { go("console"); });

    dom.runButton.addEventListener("click", function () {
      if (dom.runButton.getAttribute("data-action") === "start") {
        state.session.phase = "running";
        notice("Run started.");
      } else {
        state.armedAbort = true;
      }
      renderRibbon();
      var focus = state.armedAbort ? $("run-abort-keep") : dom.runButton;
      if (focus && !focus.hidden) focus.focus();
    });
    $("run-abort-confirm").addEventListener("click", function () {
      state.session.phase = "aborted";
      state.armedAbort = false;
      notice("Run aborted. The plant holds where it stopped.");
      renderAll();
      dom.changeScenario.focus();
    });
    $("run-abort-keep").addEventListener("click", function () {
      state.armedAbort = false;
      renderRibbon();
      dom.runButton.focus();
    });
    dom.changeScenario.addEventListener("click", function () {
      go("launch");
      var picker = $("scenario-picker");
      picker.hidden = false;
      $("choose-another").setAttribute("aria-expanded", "true");
      var sel = picker.querySelector('[aria-pressed="true"]');
      if (sel) sel.focus();
    });

    dom.ribbonAlarms.addEventListener("click", function () { if (state.overlayOpen) closeOverlay(); else openOverlay(); });
    $("alarm-overlay-close").addEventListener("click", function () { closeOverlay(); });
    dom.overlay.addEventListener("click", function (e) { if (e.target === dom.overlay) closeOverlay(); });
    ["filter-state", "filter-tag"].forEach(function (id) { $(id).addEventListener("change", renderOverlay); });
    $("filter-search").addEventListener("input", renderOverlay);
    document.querySelectorAll('input[name="priority"]').forEach(function (c) { c.addEventListener("change", renderOverlay); });
    $("ack-visible").addEventListener("click", function () {
      (renderOverlay.shown || []).forEach(function (a) { if (a.state !== "acked") acknowledge(a.id); });
      renderAll();
    });

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (state.overlayOpen) { closeOverlay(); return; }
      if (state.armedAbort) { state.armedAbort = false; renderRibbon(); dom.runButton.focus(); return; }
      if (state.faceplate) { closeFaceplate(); return; }
      });
    dom.workspace.addEventListener("click", function (e) {
      if (e.target === dom.workspace || e.target.id === "canvas") closeFaceplate();
    });
    window.addEventListener("resize", function () { clampAllWindows(); refreshHits(); });
    window.addEventListener("hashchange", function () {
      var v = location.hash.replace("#", "");
      if (v === "launch" || v === "console") go(v);
    });

    renderLaunch();
    go(location.hash === "#console" ? "console" : "launch");

    mountGraphic($("plant-thumb"), false);
    mountGraphic($("canvas"), true).then(function (g) {
      graphic = g;
      tick(0);
    });

    var last = performance.now();
    setInterval(function () {
      var t = performance.now();
      var dt = ((t - last) / 1000) * state.speed;
      last = t;
      if (dt > 0) tick(dt);
    }, TICK_MS);
  }

  init();
})();
