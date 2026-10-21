/* Faceplates (T16-4, reshaped by T20-10).
 *
 * A faceplate is the panel that opens beside a device or a loop's balloon on
 * the canvas. canvas.js owns when one opens, where it sits and when it closes;
 * this file owns what is in it:
 *
 *   device  its kind and service, a run badge and its worst band, every
 *           numeric reading the plant lists for it, and - when a loop drives
 *           it - a note saying so and a button to that loop. A device a loop
 *           does not drive offers exactly the actions the plant lists for it
 *           (T20-13): Start and Stop, and an entry for each target with the
 *           actual value and the target on one bar. A device an interlock
 *           holds offers a Reset for each tripped, manual-reset interlock.
 *   loop    PV, SP and OUT with an OUT bar, MAN and AUTO, and setpoint and
 *           output entry. There is no tuning anywhere on the console; the
 *           server's tuning actions are unchanged.
 *
 * Every reading, on either panel, has a Trend button that opens the point's
 * trend window (trendwindows.js, T20-11) when the page gives one.
 *
 * This file knows no tag: what a device is, which points it has, which actions
 * it offers, which loop drives it and which interlocks hold it come from
 * GET /api/plant (T20-2), and the values and interlock states from the
 * snapshot. A reading's words and unit come from the trend listing
 * (GET /api/trend/points: `descriptors`, `units`), which names every point the
 * plant lists; with neither, a reading shows its field name and a bare number.
 *
 * Every command goes through the one action endpoint (C5, app/api/action.py):
 *   POST /api/action  {"target": "PIC-101", "action": "set_output", "value": 0.6}
 * The server is the authority on what is allowed. This file only refuses what
 * it can already see is wrong - an output outside the range, an output entry
 * outside MANUAL, text that is not a number - so an operator is told at once
 * rather than after a round trip. A refusal from the server is shown as the
 * server worded it.
 *
 * Output is entered and shown in percent of the loop's output range, the way an
 * operator reads a valve; the action carries the loop's own units. A loop whose
 * measurement is a fraction (its row's pv_unit, T20-4) - a level - shows its PV
 * and SP in percent too, and a setpoint entered in percent is posted as the
 * fraction; the unit comes from the row, never from a field name. A device
 * target is entered in percent and posted as the fraction the device takes. A command
 * reads "accepted" once the server takes it and never "done": the values on a
 * faceplate are the snapshot's, so a change appears when the next snapshot
 * carries it, never before.
 *
 * Everything above the "browser glue" marker is pure (rows in, strings and
 * numbers out) so it runs under Node without a DOM; tests/test_faceplate.py
 * drives it that way. The glue builds a panel's elements once and afterwards
 * only rewrites their values, so a number the operator is typing is never
 * overwritten by the next snapshot. Live values carry data-live-value, so
 * connection.css dims them when the stream goes stale. Colour comes from
 * static/css/tokens.css through faceplate.css, never from here.
 */
(function (root) {
  "use strict";

  var ACTION_URL = "/api/action";
  var MISSING = "--";

  var MODE_LABEL = { MANUAL: "MAN", AUTO: "AUTO", CASCADE: "CAS" };

  // Units a setpoint may go below zero in, with the value it must stay above,
  // as app/controls/loader.py's SETPOINT_FLOORS: a temperature can be below
  // zero but not below absolute zero; a pressure or a flow cannot be negative.
  var SETPOINT_FLOORS = { "°F": -459.67 };

  // Which action each entry posts.
  var ENTRY_ACTION = { sp: "set_setpoint", out: "set_output" };

  // The snapshot sections a point's id can name, in the order they are tried.
  var POINT_SECTIONS = ["equipment", "streams", "nodes", "controllers"];

  var RUN_TEXT = { running: "▶ RUN", stopped: "■ STOP", tripped: "✖ TRIP" };

  function isObject(value) {
    return value !== null && typeof value === "object";
  }

  function numeric(value) {
    return typeof value === "number" && isFinite(value) ? value : null;
  }

  function fixed(value, digits) {
    var n = numeric(value);
    if (n === null) return MISSING;
    // A value that rounds to zero reads 0, never -0.0.
    return (Math.abs(n) < 0.5 * Math.pow(10, -digits) ? 0 : n).toFixed(digits);
  }

  function capitalised(text) {
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  /* graphic.js owns band and run-state semantics; a page has it as a global
   * and Node requires it. Without it a faceplate simply shows no bands. */
  function graphic() {
    if (root.ProcessGraphic) return root.ProcessGraphic;
    return typeof require === "function" ? require("./graphic.js") : null;
  }

  /* ---- loops ----------------------------------------------------------- */

  /* The output as a fraction of the loop's range, or null when the row cannot
   * say (no output, or a range with no width). */
  function outputFraction(row) {
    var out = numeric(row.out);
    var lo = numeric(row.out_min);
    var hi = numeric(row.out_max);
    if (out === null || lo === null || hi === null || !(hi > lo)) return null;
    return (out - lo) / (hi - lo);
  }

  /* What one loop faceplate shows for one controllers row: display strings,
   * plus the facts the controls depend on. Anything the row does not hold
   * reads "--" and never throws. */
  function model(tag, row) {
    var r = isObject(row) ? row : {};
    var unit = typeof r.pv_unit === "string" ? r.pv_unit : "";
    var fraction = outputFraction(r);
    var mode = typeof r.mode === "string" ? r.mode : null;
    var percent = unit === "fraction";

    return {
      tag: tag,
      mode: mode,
      modeLabel: mode === null ? MISSING : MODE_LABEL[mode] || mode,
      unit: percent ? "%" : unit,
      percent: percent,
      pv: readingText(r.pv, unit),
      sp: readingText(r.sp, unit),
      out: fraction === null ? MISSING : fixed(fraction * 100, 1) + " %",
      outPercent: fraction === null ? null : Math.min(100, Math.max(0, fraction * 100)),
      manual: mode === "MANUAL",
    };
  }

  /* The number `text` spells, or null. Number("") is 0 and Number("1e") is
   * NaN; only a plain decimal is a value. */
  function plainNumber(text) {
    var trimmed = String(text).trim();
    var value = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(trimmed) ? Number(trimmed) : NaN;
    return isFinite(value) ? value : null;
  }

  /* Read one entry: `kind` is sp or out and `text` what the operator typed.
   * Returns {action, value} to post, or {error} to show. */
  function parseEntry(kind, text, row) {
    var r = isObject(row) ? row : {};
    var value = plainNumber(text);
    if (value === null) return { error: "Enter a number." };

    if (kind === "sp") {
      if (r.pv_unit === "fraction") {
        if (value < 0 || value > 100) return { error: "Setpoint must be between 0 and 100 %." };
        return { action: ENTRY_ACTION.sp, value: value === 100 ? 1 : value / 100 };
      }
      var floor = Object.prototype.hasOwnProperty.call(SETPOINT_FLOORS, r.pv_unit) ? SETPOINT_FLOORS[r.pv_unit] : null;
      if (floor !== null && value <= floor) return { error: "Setpoint must be above " + floor + " " + r.pv_unit + "." };
      if (floor === null && value < 0) return { error: "Setpoint cannot be negative." };
      return { action: ENTRY_ACTION.sp, value: value };
    }

    if (kind === "out") {
      if (r.mode !== "MANUAL") return { error: "Switch to MAN to set the output." };
      if (value < 0 || value > 100) return { error: "Output must be between 0 and 100 %." };
      var lo = numeric(r.out_min);
      var hi = numeric(r.out_max);
      if (lo === null || hi === null || !(hi >= lo)) return { error: "Output range unknown." };
      // The top of the range is sent exactly, never a rounding short of it.
      return { action: ENTRY_ACTION.out, value: value === 100 ? hi : lo + (value / 100) * (hi - lo) };
    }

    return { error: "Unknown entry." };
  }

  function buildActionRequest(tag, action, value) {
    return {
      url: ACTION_URL,
      init: {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: tag, action: action, value: value === undefined ? null : value }),
      },
    };
  }

  /* The operator's words for a command, for the line under the controls:
   * "Sending <words>..." and "<Words> accepted." */
  function describeCommand(tag, action, value, row) {
    var m = model(tag, row);
    if (action === "start" || action === "stop" || action === "reset") return action + " " + tag;
    var field = targetField(action);
    if (field !== null) return field.replace(/_/g, " ") + " target " + fixed(value * 100, 1) + " % for " + tag;
    if (action === "manual") return "switch " + tag + " to MAN";
    if (action === "auto") return "switch " + tag + " to AUTO";
    if (action === "set_setpoint") {
      if (m.percent) return "setpoint " + fixed(value * 100, 1) + " % for " + tag;
      return "setpoint " + (m.unit ? value + " " + m.unit : String(value)) + " for " + tag;
    }
    if (action === "set_output") {
      var r = isObject(row) ? row : {};
      var lo = numeric(r.out_min);
      var hi = numeric(r.out_max);
      var shown = lo !== null && hi !== null && hi > lo ? fixed(((value - lo) / (hi - lo)) * 100, 1) + " %" : String(value);
      return "output " + shown + " for " + tag;
    }
    return action + " for " + tag;
  }

  function acceptedText(words) {
    return capitalised(words) + " accepted. The plant responds over the next steps.";
  }

  /* The field a target action sets ("set_speed_target" -> "speed"), or null
   * for any other action. */
  function targetField(action) {
    var found = /^set_(\w+)_target$/.exec(String(action));
    return found ? found[1] : null;
  }

  /* Read a target entry, typed in percent: {value} as the fraction the device
   * takes, or {error}. */
  function parseTarget(text) {
    var value = plainNumber(text);
    if (value === null) return { error: "Enter a number." };
    if (value < 0 || value > 100) return { error: "Target must be between 0 and 100 %." };
    return { value: value === 100 ? 1 : value / 100 };
  }

  /* The interlocks of GET /api/plant's `interlocks` section that hold `tag`. */
  function heldBy(interlocks, tag) {
    if (!isObject(interlocks)) return [];
    return Object.keys(interlocks).filter(function (name) {
      var row = interlocks[name];
      return isObject(row) && Array.isArray(row.devices) && row.devices.indexOf(tag) !== -1;
    });
  }

  /* Of the interlocks `held`, those a Reset is offered for: tripped, and
   * needing a manual reset. Whether the condition has cleared is the server's
   * to say, on the press. */
  function resetOffers(snapshot, held) {
    var rows = isObject(snapshot) && isObject(snapshot.interlocks) ? snapshot.interlocks : {};
    return held.filter(function (name) {
      var row = rows[name];
      return isObject(row) && row.state === "tripped" && row.reset === "manual";
    });
  }

  /* ---- devices --------------------------------------------------------- */

  /* The value of a point ("<id>.<field>"), looked up in each section a point
   * can name, or undefined when none holds it. */
  function pointValue(snapshot, point) {
    var cut = String(point).lastIndexOf(".");
    if (cut < 0 || !isObject(snapshot)) return undefined;
    var id = point.slice(0, cut);
    var field = point.slice(cut + 1);
    for (var i = 0; i < POINT_SECTIONS.length; i++) {
      var rows = snapshot[POINT_SECTIONS[i]];
      var row = isObject(rows) && Object.prototype.hasOwnProperty.call(rows, id) ? rows[id] : null;
      if (isObject(row) && Object.prototype.hasOwnProperty.call(row, field)) return row[field];
    }
    return undefined;
  }

  /* A reading's name: the trend listing's descriptor, else the field. */
  function readingLabel(point, vocab) {
    var words = isObject(vocab) && isObject(vocab.descriptors) ? vocab.descriptors[point] : null;
    if (typeof words !== "string" || !words) words = point.slice(point.lastIndexOf(".") + 1).replace(/_/g, " ");
    return capitalised(words);
  }

  /* A 0..1 fraction reads as a percent, as the schematic's own readings do. */
  function readingText(value, unit) {
    var n = numeric(value);
    if (n === null) return MISSING;
    if (unit === "fraction") return fixed(n * 100, 1) + " %";
    return unit ? fixed(n, 1) + " " + unit : fixed(n, 1);
  }

  /* What one device faceplate shows: `info` is the device's entry in
   * GET /api/plant, `vocab` the trend listing ({descriptors, units}). A
   * reading is a listed point whose value is not a flag (a run flag is the
   * badge, not a number to trend). */
  function deviceModel(tag, info, snapshot, vocab) {
    var i = isObject(info) ? info : {};
    var g = graphic();
    var units = isObject(vocab) && isObject(vocab.units) ? vocab.units : {};
    var points = Array.isArray(i.points) ? i.points : [];

    var readings = points
      .filter(function (point) {
        return typeof point === "string" && typeof pointValue(snapshot, point) !== "boolean";
      })
      .map(function (point) {
        var band = g ? g.pointBand(snapshot, point) : null;
        // A point on the device's branch rather than on the device itself is
        // its stream: "Flow" twice would read as one number shown twice.
        var label = readingLabel(point, vocab) + (point.indexOf(tag + ".") === 0 ? "" : " (stream)");
        return {
          point: point,
          label: label,
          text: readingText(pointValue(snapshot, point), units[point]),
          severity: band ? band.severity : null,
          bandText: band ? g.bandText(band) : "",
        };
      });

    var state = g ? g.deviceState(snapshot, tag) : "unknown";
    var worst = g ? g.worstBand(snapshot, tag) : null;

    var actions = (Array.isArray(i.actions) ? i.actions : []).filter(function (a) {
      return typeof a === "string";
    });
    var running = pointValue(snapshot, tag + ".running");
    var targets = actions
      .filter(function (a) {
        return targetField(a) !== null;
      })
      .map(function (a) {
        var field = targetField(a);
        var actual = numeric(pointValue(snapshot, tag + "." + field));
        var target = numeric(pointValue(snapshot, tag + "." + field + "_target"));
        var word = field.replace(/_/g, " ");
        return {
          action: a,
          label: capitalised(word),
          actual: actual,
          target: target,
          actualPercent: actual === null ? null : Math.min(100, Math.max(0, actual * 100)),
          targetPercent: target === null ? null : Math.min(100, Math.max(0, target * 100)),
          caption: capitalised(word) + " " + readingText(actual, "fraction") + " · target " + readingText(target, "fraction"),
        };
      });

    return {
      tag: tag,
      kind: typeof i.kind === "string" ? i.kind : "",
      service: typeof i.service === "string" && i.service ? i.service : null,
      state: state,
      stateText: RUN_TEXT[state] || "",
      severity: worst ? worst.severity : null,
      bandText: worst ? g.bandText(worst) : "",
      readings: readings,
      drivenBy: typeof i.driven_by === "string" ? i.driven_by : null,
      actions: actions,
      // Start is off while it runs and Stop while it is stopped; with no run
      // flag to read, neither is.
      canStart: running !== true,
      canStop: running !== false,
      targets: targets,
    };
  }

  var api = {
    ACTION_URL: ACTION_URL,
    model: model,
    parseEntry: parseEntry,
    buildActionRequest: buildActionRequest,
    describeCommand: describeCommand,
    capitalised: capitalised,
    acceptedText: acceptedText,
    pointValue: pointValue,
    readingLabel: readingLabel,
    readingText: readingText,
    deviceModel: deviceModel,
    parseTarget: parseTarget,
    targetField: targetField,
    heldBy: heldBy,
    resetOffers: resetOffers,
  };

  /* ---- browser glue ---------------------------------------------------- */

  function el(doc, name, attrs, text) {
    var node = doc.createElement(name);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /* The transport for commands: returns post(tag, action, value), which
   * resolves to the server's refusal text, or null when the action was taken.
   * `doFetch` exists so a page or test can supply its own. */
  function makePost(doFetch) {
    return function post(tag, action, value) {
      var request = buildActionRequest(tag, action, value);
      return doFetch(request.url, request.init).then(
        function (response) {
          if (response.ok) return null;
          return response.json().then(
            function (body) {
              return (body && body.error) || "Refused (HTTP " + response.status + ")";
            },
            function () {
              return "Refused (HTTP " + response.status + ")";
            }
          );
        },
        function () {
          return "Not sent: the server could not be reached.";
        }
      );
    };
  }

  /* Send one command and walk `feedback` through pending, then accepted or
   * the server's refusal; never "done" (see the module comment). `done` runs
   * on acceptance. */
  function sendCommand(feedback, post, tag, action, value, words, done) {
    feedback.show("pending", "Sending " + words + "…");
    post(tag, action, value).then(function (error) {
      if (error) {
        feedback.show("rejected", error);
      } else {
        feedback.show("accepted", acceptedText(words));
        if (done) done();
      }
    });
  }

  function entryForm(doc, tag, kind, label, unit) {
    var id = "fp-" + tag + "-" + kind;
    var form = el(doc, "form", { class: "fp-entry", "data-entry": kind });
    form.appendChild(el(doc, "label", { for: id }, label));
    form.appendChild(
      el(doc, "input", { id: id, type: "text", inputmode: "decimal", autocomplete: "off", "aria-describedby": id + "-unit" })
    );
    // Outside the button, so the button's name stays "Set".
    form.appendChild(el(doc, "span", { id: id + "-unit", class: "visually-hidden" }, unit));
    form.appendChild(el(doc, "button", { type: "submit", class: "btn" }, "Set"));
    return form;
  }

  function head(doc, tag, closePanel) {
    var node = el(doc, "div", { class: "fp-head" });
    var titles = el(doc, "div", { class: "fp-titles" });
    var kind = el(doc, "p", { class: "fp-kind" });
    var badges = el(doc, "div", { class: "fp-badges", "data-live-value": "" });
    titles.appendChild(el(doc, "p", { class: "fp-tag", id: "fp-title-" + tag }, tag));
    titles.appendChild(kind);
    titles.appendChild(badges);
    node.appendChild(titles);
    var close = el(doc, "button", { type: "button", class: "btn btn-quiet btn-icon fp-close", "aria-label": "Close " + tag + " faceplate" }, "✕");
    close.addEventListener("click", closePanel);
    node.appendChild(close);
    return { element: node, kind: kind, badges: badges, close: close };
  }

  var SVG_NS = "http://www.w3.org/2000/svg";

  /* A reading's Trend button, or null when the page has no trend windows or
   * the trend listing does not publish the point. */
  function trendButton(doc, point, label, ctx) {
    if (typeof ctx.trend !== "function") return null;
    var listed = isObject(ctx.vocab) && Array.isArray(ctx.vocab.points) ? ctx.vocab.points : [];
    if (listed.indexOf(point) < 0) return null;
    var button = el(doc, "button", { type: "button", class: "btn-trend", "data-trend": point, "aria-label": "Show trend of " + label });
    var icon = doc.createElementNS(SVG_NS, "svg");
    icon.setAttribute("viewBox", "0 0 14 10");
    icon.setAttribute("aria-hidden", "true");
    var line = doc.createElementNS(SVG_NS, "path");
    line.setAttribute("d", "M1 8 L5 3 L8 6 L13 1");
    icon.appendChild(line);
    button.appendChild(icon);
    button.appendChild(el(doc, "span", {}, "Trend"));
    button.addEventListener("click", function () {
      ctx.trend(point, button);
    });
    return button;
  }

  function section(doc, title) {
    var node = el(doc, "section", { class: "fp-section" });
    if (title) node.appendChild(el(doc, "h3", {}, title));
    return node;
  }

  function panelShell(doc, kind, tag) {
    return el(doc, "section", {
      class: "faceplate",
      role: "dialog",
      "aria-labelledby": "fp-title-" + tag,
      "data-faceplate": tag,
      "data-kind": kind,
    });
  }

  /* The line under the controls: pending, then accepted or rejected. */
  function feedbackLine(doc) {
    var node = el(doc, "p", { class: "fp-feedback", role: "status", hidden: "" });
    return {
      element: node,
      show: function (kind, text) {
        node.removeAttribute("hidden");
        node.setAttribute("data-kind", kind);
        node.textContent = text;
      },
    };
  }

  function badge(doc, className, attrs, text) {
    return el(doc, "span", Object.assign({ class: className }, attrs), text);
  }

  /* A device's panel. `ctx` is {info, vocab, interlocks, post, close,
   * open(kind, tag), trend}.
   * Returns {element, update(snapshot), close (the close button)}. */
  function buildDevicePanel(doc, tag, ctx) {
    var panel = panelShell(doc, "device", tag);
    var top = head(doc, tag, ctx.close);
    panel.appendChild(top.element);

    var readings = section(doc, "Readings");
    var list = el(doc, "dl", { class: "fp-readings", "data-live-value": "" });
    readings.appendChild(list);
    panel.appendChild(readings);

    var info = isObject(ctx.info) ? ctx.info : {};
    var driver = typeof info.driven_by === "string" ? info.driven_by : null;
    var actions = (Array.isArray(info.actions) ? info.actions : []).filter(function (a) {
      return typeof a === "string";
    });
    var targetActions = actions.filter(function (a) {
      return targetField(a) !== null;
    });
    var held = heldBy(ctx.interlocks, tag);
    var operate = section(doc, "Operate " + tag);
    var feedback = feedbackLine(doc);
    var startButton = null;
    var stopButton = null;
    var bars = {};
    var resets = {};

    function send(action, value, done, target) {
      sendCommand(feedback, ctx.post, target || tag, action, value, describeCommand(target || tag, action, value), done);
    }

    if (driver !== null) {
      var note = el(doc, "p", { class: "fp-note" });
      note.appendChild(el(doc, "strong", {}, tag + " is driven by " + driver + "."));
      note.appendChild(doc.createTextNode(" Operate it from the controller; a direct command would be overwritten on the next step."));
      operate.appendChild(note);
      var open = el(doc, "button", { type: "button", class: "btn fp-open", "data-open": driver }, "Open " + driver);
      open.addEventListener("click", function () {
        ctx.open("loop", driver);
      });
      operate.appendChild(open);
    } else {
      var hasStart = actions.indexOf("start") >= 0;
      var hasStop = actions.indexOf("stop") >= 0;
      if (hasStart || hasStop) {
        var group = el(doc, "div", { class: "fp-actions", role: "group", "aria-label": tag + " start and stop" });
        [["start", "▶", hasStart], ["stop", "■", hasStop]].forEach(function (spec) {
          if (!spec[2]) return;
          var button = el(doc, "button", { type: "button", class: "btn btn-equip", "data-equip-action": spec[0] });
          button.appendChild(el(doc, "span", { class: "btn-glyph", "aria-hidden": "true" }, spec[1]));
          button.appendChild(doc.createTextNode(capitalised(spec[0]) + " " + tag));
          button.addEventListener("click", function () {
            send(spec[0], null);
          });
          group.appendChild(button);
          if (spec[0] === "start") startButton = button;
          else stopButton = button;
        });
        operate.appendChild(group);
      }

      targetActions.forEach(function (action) {
        var t = { action: action, label: capitalised(targetField(action).replace(/_/g, " ")) };
        var caption = el(doc, "p", { class: "fp-bar-caption", "data-live-value": "" });
        var bar = el(doc, "div", {
          class: "fp-bar",
          role: "meter",
          "aria-label": tag + " " + t.label.toLowerCase(),
          "aria-valuemin": "0",
          "aria-valuemax": "100",
          "data-live-value": "",
        });
        var fill = el(doc, "span", { class: "fp-bar-fill" });
        var mark = el(doc, "i", { class: "fp-bar-target" });
        bar.appendChild(fill);
        bar.appendChild(mark);
        var form = entryForm(doc, tag, t.action, t.label + " target (%)", "percent");
        operate.appendChild(caption);
        operate.appendChild(bar);
        operate.appendChild(form);
        bars[t.action] = { caption: caption, bar: bar, fill: fill, mark: mark, input: form.querySelector("input") };
        form.addEventListener("submit", function (event) {
          event.preventDefault();
          var sent = bars[t.action].input.value;
          var parsed = parseTarget(sent);
          if (parsed.error) {
            feedback.show("rejected", parsed.error);
            return;
          }
          send(t.action, parsed.value, function () {
            // Only what was sent is cleared, as on a loop.
            if (bars[t.action].input.value === sent) bars[t.action].input.value = "";
          });
        });
      });
    }

    held.forEach(function (name) {
      var button = el(doc, "button", { type: "button", class: "btn btn-equip fp-reset", "data-reset": name, hidden: "" }, "Reset " + name);
      button.addEventListener("click", function () {
        send("reset", null, null, name);
      });
      operate.appendChild(button);
      resets[name] = button;
    });

    if (driver === null && actions.length === 0 && held.length === 0) {
      operate.appendChild(el(doc, "p", { class: "fp-note" }, "No operator actions on " + tag + ". It follows what flows in and out."));
    }
    operate.appendChild(feedback.element);
    panel.appendChild(operate);

    var rowsKey = null;
    var rows = {};

    function update(snapshot) {
      var m = deviceModel(tag, ctx.info, snapshot, ctx.vocab);

      top.kind.textContent = m.service ? m.kind + " · " + m.service : m.kind;

      top.badges.textContent = "";
      if (m.stateText) top.badges.appendChild(badge(doc, "state-badge", { "data-state": m.state }, m.stateText));
      if (m.severity) top.badges.appendChild(badge(doc, "band-badge", { "data-severity": m.severity }, m.bandText));

      // Rows are rebuilt only when the set of readings changes.
      var key = m.readings
        .map(function (r) {
          return r.point;
        })
        .join("\n");
      if (key !== rowsKey) {
        rowsKey = key;
        rows = {};
        list.textContent = "";
        m.readings.forEach(function (r) {
          var row = el(doc, "div", { class: "fp-reading", "data-point": r.point });
          var value = el(doc, "dd", {});
          var band = el(doc, "span", { class: "fp-band" });
          row.appendChild(el(doc, "dt", {}, r.label));
          row.appendChild(value);
          row.appendChild(band);
          var trend = trendButton(doc, r.point, tag + " " + r.label.toLowerCase(), ctx);
          if (trend) row.appendChild(trend);
          list.appendChild(row);
          rows[r.point] = { value: value, band: band };
        });
      }

      var holder = doc.activeElement;
      if (startButton) startButton.disabled = !m.canStart;
      if (stopButton) stopButton.disabled = !m.canStop;
      // A button disabled under keyboard focus drops it to the page. Focus
      // stays in the panel but never on the opposite action, so a repeated
      // key cannot stop the device it just started.
      if (holder && holder.disabled && (holder === startButton || holder === stopButton)) {
        top.close.focus();
      }

      m.targets.forEach(function (t) {
        var cells = bars[t.action];
        if (!cells) return;
        cells.caption.textContent = t.caption;
        cells.fill.style.width = (t.actualPercent === null ? 0 : t.actualPercent) + "%";
        cells.mark.style.left = "calc(" + (t.targetPercent === null ? 0 : t.targetPercent) + "% - 1px)";
        if (t.actualPercent === null) {
          cells.bar.removeAttribute("aria-valuenow");
          cells.bar.setAttribute("aria-hidden", "true");
        } else {
          cells.bar.setAttribute("aria-valuenow", t.actualPercent.toFixed(1));
          cells.bar.removeAttribute("aria-hidden");
        }
        if (t.target !== null) cells.input.placeholder = fixed(t.targetPercent, 1);
      });

      var offered = resetOffers(snapshot, held);
      held.forEach(function (name) {
        if (offered.indexOf(name) >= 0) resets[name].removeAttribute("hidden");
        else resets[name].setAttribute("hidden", "");
      });

      m.readings.forEach(function (r) {
        var cells = rows[r.point];
        cells.value.textContent = r.text;
        cells.value.setAttribute("data-band", r.severity || "none");
        cells.band.textContent = r.bandText;
        if (r.severity) cells.band.setAttribute("data-severity", r.severity);
        else cells.band.removeAttribute("data-severity");
      });
    }

    return { element: panel, update: update, close: top.close };
  }

  /* A loop's panel. `ctx` is {info, close, open(kind, tag), post, trend}. */
  function buildLoopPanel(doc, tag, ctx) {
    var info = isObject(ctx.info) ? ctx.info : {};
    var panel = panelShell(doc, "loop", tag);
    var top = head(doc, tag, ctx.close);
    top.kind.textContent = typeof info.service === "string" && info.service ? "Controller · " + info.service : "Controller";
    var mode = badge(doc, "state-badge", {});
    top.badges.appendChild(mode);
    panel.appendChild(top.element);

    var loop = section(doc, "Loop");
    var values = el(doc, "dl", { class: "fp-readings", "data-live-value": "" });
    var fields = {};
    [["PV", "Process value (PV)"], ["SP", "Setpoint (SP)"], ["OUT", "Output (OUT)"]].forEach(function (pair) {
      var point = tag + "." + pair[0].toLowerCase();
      var row = el(doc, "div", { class: "fp-reading", "data-point": point });
      fields[pair[0]] = el(doc, "dd", {}, MISSING);
      row.appendChild(el(doc, "dt", {}, pair[1]));
      row.appendChild(fields[pair[0]]);
      // The device rows' band column, empty: the Trend buttons line up.
      row.appendChild(el(doc, "span", { class: "fp-band" }));
      var trend = trendButton(doc, point, tag + " " + pair[0], ctx);
      if (trend) row.appendChild(trend);
      values.appendChild(row);
    });
    loop.appendChild(values);

    var bar = el(doc, "div", {
      class: "fp-bar",
      role: "meter",
      "aria-label": tag + " output",
      "aria-valuemin": "0",
      "aria-valuemax": "100",
      "data-live-value": "",
    });
    var fill = el(doc, "span", { class: "fp-bar-fill" });
    bar.appendChild(fill);
    loop.appendChild(bar);

    var wiring = el(doc, "p", { class: "fp-note" });
    if (typeof info.pv === "string") wiring.appendChild(doc.createTextNode("Measures " + info.pv));
    if (typeof info.out === "string") {
      var target = info.out;
      if (typeof info.pv === "string") wiring.appendChild(doc.createTextNode(" · "));
      wiring.appendChild(doc.createTextNode("Drives "));
      var drives = el(doc, "button", { type: "button", class: "fp-link fp-open", "data-open": target, "aria-label": "Open " + target + " faceplate" }, target);
      drives.addEventListener("click", function () {
        ctx.open("device", target);
      });
      wiring.appendChild(drives);
    }
    loop.appendChild(wiring);
    panel.appendChild(loop);

    var operate = section(doc, "Operate " + tag);
    var modes = el(doc, "div", { class: "fp-mode", role: "group", "aria-label": tag + " mode" });
    var buttons = {
      manual: el(doc, "button", { type: "button", "data-action": "manual", "aria-pressed": "false" }, "MAN"),
      auto: el(doc, "button", { type: "button", "data-action": "auto", "aria-pressed": "false" }, "AUTO"),
    };
    modes.appendChild(buttons.manual);
    modes.appendChild(buttons.auto);
    operate.appendChild(modes);

    var forms = { sp: entryForm(doc, tag, "sp", "Setpoint", ""), out: entryForm(doc, tag, "out", "Output (%)", "percent") };
    operate.appendChild(forms.sp);
    operate.appendChild(forms.out);
    var outNote = el(doc, "p", { class: "fp-note", id: "fp-" + tag + "-out-note" });
    forms.out.querySelector("input").setAttribute("aria-describedby", "fp-" + tag + "-out-note fp-" + tag + "-out-unit");
    operate.appendChild(outNote);
    var feedback = feedbackLine(doc);
    operate.appendChild(feedback.element);
    panel.appendChild(operate);

    var row = null;

    function send(action, value, done) {
      sendCommand(feedback, ctx.post, tag, action, value, describeCommand(tag, action, value, row), done);
    }

    Object.keys(buttons).forEach(function (action) {
      buttons[action].addEventListener("click", function () {
        send(action, null);
      });
    });

    Object.keys(forms).forEach(function (kind) {
      var form = forms[kind];
      var input = form.querySelector("input");
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var sent = input.value;
        var parsed = parseEntry(kind, sent, row);
        if (parsed.error) {
          feedback.show("rejected", parsed.error);
          return;
        }
        send(parsed.action, parsed.value, function () {
          // Only what was sent is cleared: text typed while the request was in
          // flight is the operator's next entry and stays.
          if (input.value === sent) input.value = "";
        });
      });
    });

    function update(snapshot) {
      row = isObject(snapshot) && isObject(snapshot.controllers) ? snapshot.controllers[tag] : null;
      var m = model(tag, row);

      panel.setAttribute("data-mode", m.mode || "unknown");
      mode.textContent = m.modeLabel;
      mode.setAttribute("data-mode", m.mode || "");
      fields.PV.textContent = m.pv;
      fields.SP.textContent = m.sp;
      fields.OUT.textContent = m.out;

      fill.style.width = (m.outPercent === null ? 0 : m.outPercent) + "%";
      // A meter must carry a value; with none to show, the bar is hidden from
      // assistive tech (the OUT reading above it already says "--").
      if (m.outPercent === null) {
        bar.removeAttribute("aria-valuenow");
        bar.setAttribute("aria-hidden", "true");
      } else {
        bar.setAttribute("aria-valuenow", m.outPercent.toFixed(1));
        bar.removeAttribute("aria-hidden");
      }

      buttons.manual.setAttribute("aria-pressed", String(m.mode === "MANUAL"));
      buttons.auto.setAttribute("aria-pressed", String(m.mode === "AUTO"));

      // The setpoint is entered in the unit it is shown in.
      forms.sp.querySelector("label").textContent = m.percent ? "Setpoint (%)" : "Setpoint";
      forms.sp.querySelector(".visually-hidden").textContent = m.percent ? "percent" : m.unit;
      forms.sp.querySelector("input").placeholder = m.sp === MISSING ? "" : m.sp.split(" ")[0];
      var outInput = forms.out.querySelector("input");
      outInput.placeholder = m.out === MISSING ? "" : m.out.split(" ")[0];
      outInput.disabled = !m.manual;
      forms.out.querySelector("button").disabled = !m.manual;
      outNote.textContent = m.manual
        ? ""
        : m.mode === null
          ? "Mode unknown, so output entry is unavailable."
          : "Output is set by the loop in " + m.modeLabel + ". Switch to MAN to set it.";
    }

    return { element: panel, update: update, close: top.close };
  }

  /* Build one panel: `kind` is "device" or "loop". `ctx` is {info, vocab,
   * post, close, open(kind, tag), trend(point, button)}; without trend no
   * reading has a Trend button. The panel builds its elements once and
   * update(snapshot) rewrites their values. */
  function createPanel(doc, kind, tag, ctx) {
    return kind === "loop" ? buildLoopPanel(doc, tag, ctx) : buildDevicePanel(doc, tag, ctx);
  }

  api.makePost = makePost;
  api.createPanel = createPanel;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Faceplates = api;
})(typeof window !== "undefined" ? window : globalThis);
