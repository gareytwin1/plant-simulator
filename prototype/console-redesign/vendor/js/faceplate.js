/* Controller faceplates (T16-4).
 *
 * One faceplate per row of the snapshot's controllers section (T16-14), in the
 * order the snapshot gives them: tag and mode, PV, SP and OUT, an OUT bar over
 * the loop's output range, MAN and AUTO, setpoint and output entry, and tuning.
 * This file knows no tag: a loop the plant adds gets a faceplate with no change
 * here.
 *
 * Every command goes through the one action endpoint (C5, app/api/action.py):
 *   POST /api/action  {"target": "PIC-101", "action": "set_output", "value": 0.6}
 * The server is the authority on what is allowed. This file only refuses what
 * it can already see is wrong - an output outside the range, an output entry
 * outside MANUAL, a gain on a loop that is not tunable, text that is not a
 * number - so an operator is told at once rather than after a round trip. A
 * refusal from the server is shown on the faceplate as the server worded it.
 *
 * Output is entered and shown in percent of the loop's output range, the way
 * an operator reads a valve; the action carries the loop's own units. The
 * snapshot is what the faceplate shows: an accepted command appears when the
 * next snapshot carries it, never before.
 *
 * Everything above the "browser glue" marker is pure (a row in, strings and
 * numbers out) so it runs under Node without a DOM; tests/test_faceplate.py
 * drives it that way. The glue builds a loop's elements once and afterwards
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

  // Which action each entry posts, and which gain a tuning entry sets.
  var ENTRY_ACTION = { sp: "set_setpoint", out: "set_output", kp: "set_kp", ki: "set_ki", kd: "set_kd" };
  var GAINS = ["kp", "ki", "kd"];

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

  /* The output as a fraction of the loop's range, or null when the row cannot
   * say (no output, or a range with no width). */
  function outputFraction(row) {
    var out = numeric(row.out);
    var lo = numeric(row.out_min);
    var hi = numeric(row.out_max);
    if (out === null || lo === null || hi === null || !(hi > lo)) return null;
    return (out - lo) / (hi - lo);
  }

  /* What one faceplate shows for one controllers row: display strings, plus
   * the facts the controls depend on. Anything the row does not hold reads
   * "--" and never throws. */
  function model(tag, row) {
    var r = isObject(row) ? row : {};
    var unit = typeof r.pv_unit === "string" ? r.pv_unit : "";
    var fraction = outputFraction(r);
    var mode = typeof r.mode === "string" ? r.mode : null;

    function withUnit(text) {
      return text === MISSING || !unit ? text : text + " " + unit;
    }

    var gains = {};
    GAINS.forEach(function (name) {
      gains[name] = numeric(r[name]) === null ? MISSING : String(r[name]);
    });

    return {
      tag: tag,
      mode: mode,
      modeLabel: mode === null ? MISSING : MODE_LABEL[mode] || mode,
      unit: unit,
      pv: withUnit(fixed(r.pv, 1)),
      sp: withUnit(fixed(r.sp, 1)),
      out: fraction === null ? MISSING : fixed(fraction * 100, 1) + " %",
      outPercent: fraction === null ? null : Math.min(100, Math.max(0, fraction * 100)),
      manual: mode === "MANUAL",
      tunable: r.tunable === true,
      gains: gains,
    };
  }

  /* Read one entry: `kind` is sp, out, kp, ki or kd and `text` what the
   * operator typed. Returns {action, value} to post, or {error} to show. */
  function parseEntry(kind, text, row) {
    var r = isObject(row) ? row : {};
    var trimmed = String(text).trim();
    // Number("") is 0 and Number("1e") is NaN; only a plain decimal is a value.
    var value = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(trimmed) ? Number(trimmed) : NaN;
    if (!isFinite(value)) return { error: "Enter a number." };

    if (kind === "sp") {
      if (value < 0) return { error: "Setpoint cannot be negative." };
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

    if (GAINS.indexOf(kind) >= 0) {
      if (r.tunable !== true) return { error: "Tuning is locked for this loop." };
      if (value < 0) return { error: "Gains cannot be negative." };
      return { action: ENTRY_ACTION[kind], value: value };
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

  /* The loop tags a snapshot publishes, in its order. */
  function controllerTags(snapshot) {
    return isObject(snapshot) && isObject(snapshot.controllers) ? Object.keys(snapshot.controllers) : [];
  }

  var api = {
    ACTION_URL: ACTION_URL,
    model: model,
    parseEntry: parseEntry,
    buildActionRequest: buildActionRequest,
    controllerTags: controllerTags,
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

  function entryForm(doc, tag, kind, label) {
    var id = "fp-" + tag + "-" + kind;
    var form = el(doc, "form", { class: "faceplate-entry", "data-entry": kind });
    form.appendChild(el(doc, "label", { for: id }, label));
    form.appendChild(
      el(doc, "input", { id: id, type: "text", inputmode: "decimal", autocomplete: "off", size: "7" })
    );
    form.appendChild(el(doc, "button", { type: "submit" }, "Set"));
    return form;
  }

  /* One loop's elements, built once. Returns the element and a setter that
   * writes a model into it. */
  function buildFaceplate(doc, tag, post) {
    var section = el(doc, "section", { class: "faceplate", "data-tag": tag, "aria-label": tag + " faceplate" });

    var head = el(doc, "div", { class: "faceplate-head" });
    head.appendChild(el(doc, "h3", { class: "faceplate-tag" }, tag));
    var mode = el(doc, "span", { class: "faceplate-mode", "data-live-value": "" });
    head.appendChild(mode);
    section.appendChild(head);

    var values = el(doc, "dl", { class: "faceplate-values", "data-live-value": "" });
    var fields = {};
    ["PV", "SP", "OUT"].forEach(function (name) {
      var row = el(doc, "div", { class: "faceplate-value" });
      row.appendChild(el(doc, "dt", {}, name));
      fields[name] = el(doc, "dd", {}, MISSING);
      row.appendChild(fields[name]);
      values.appendChild(row);
    });
    section.appendChild(values);

    var bar = el(doc, "div", {
      class: "faceplate-bar",
      role: "meter",
      "aria-label": tag + " output",
      "aria-valuemin": "0",
      "aria-valuemax": "100",
      "data-live-value": "",
    });
    var fill = el(doc, "div", { class: "faceplate-bar-fill" });
    bar.appendChild(fill);
    section.appendChild(bar);

    var modes = el(doc, "div", { class: "faceplate-modes", role: "group", "aria-label": tag + " mode" });
    var buttons = {
      manual: el(doc, "button", { type: "button", "data-action": "manual", "aria-pressed": "false" }, "MAN"),
      auto: el(doc, "button", { type: "button", "data-action": "auto", "aria-pressed": "false" }, "AUTO"),
    };
    modes.appendChild(buttons.manual);
    modes.appendChild(buttons.auto);
    section.appendChild(modes);

    var forms = {
      sp: entryForm(doc, tag, "sp", "SP"),
      out: entryForm(doc, tag, "out", "OUT %"),
    };
    section.appendChild(forms.sp);
    section.appendChild(forms.out);

    var tuning = el(doc, "details", { class: "faceplate-tuning" });
    var summary = el(doc, "summary", {}, "Tuning");
    tuning.appendChild(summary);
    GAINS.forEach(function (name) {
      forms[name] = entryForm(doc, tag, name, name.charAt(0).toUpperCase() + name.slice(1));
      tuning.appendChild(forms[name]);
    });
    section.appendChild(tuning);

    var message = el(doc, "p", { class: "faceplate-message", role: "status" });
    section.appendChild(message);

    var row = null;

    function say(text, kind) {
      message.textContent = text || "";
      if (kind) message.setAttribute("data-kind", kind);
      else message.removeAttribute("data-kind");
    }

    function send(action, value, done) {
      say("");
      post(tag, action, value).then(function (error) {
        if (error) say(error, "refused");
        else if (done) done();
      });
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
          say(parsed.error, "refused");
          return;
        }
        send(parsed.action, parsed.value, function () {
          // Only what was sent is cleared: text typed while the request was in
          // flight is the operator's next entry and stays.
          if (input.value === sent) input.value = "";
        });
      });
    });

    function setEnabled(form, enabled) {
      form.querySelectorAll("input, button").forEach(function (node) {
        node.disabled = !enabled;
      });
    }

    function show(next) {
      row = next;
      var m = model(tag, next);

      section.setAttribute("data-mode", m.mode || "unknown");
      mode.textContent = m.modeLabel;
      fields.PV.textContent = m.pv;
      fields.SP.textContent = m.sp;
      fields.OUT.textContent = m.out;

      fill.style.width = (m.outPercent === null ? 0 : m.outPercent) + "%";
      // A meter must carry a value; with none to show, the bar is hidden from
      // assistive tech (the OUT reading beside it already says "--").
      if (m.outPercent === null) {
        bar.removeAttribute("aria-valuenow");
        bar.setAttribute("aria-hidden", "true");
      } else {
        bar.setAttribute("aria-valuenow", m.outPercent.toFixed(1));
        bar.removeAttribute("aria-hidden");
      }

      buttons.manual.setAttribute("aria-pressed", String(m.mode === "MANUAL"));
      buttons.auto.setAttribute("aria-pressed", String(m.mode === "AUTO"));

      forms.sp.querySelector("input").placeholder = m.sp === MISSING ? "" : m.sp.split(" ")[0];
      forms.out.querySelector("input").placeholder = m.out === MISSING ? "" : m.out.split(" ")[0];
      setEnabled(forms.out, m.manual);

      summary.textContent = m.tunable ? "Tuning" : "Tuning (locked)";
      GAINS.forEach(function (name) {
        forms[name].querySelector("input").placeholder = m.gains[name];
        setEnabled(forms[name], m.tunable);
      });
    }

    return { element: section, show: show };
  }

  /* Mount faceplates into `container`, one per loop the snapshots publish.
   * `options.fetch` exists so a page or test can supply its own transport.
   * Returns {update}: call it with every snapshot. */
  function mount(container, options) {
    var settings = options || {};
    var doFetch = settings.fetch || root.fetch.bind(root);
    var doc = container.ownerDocument;
    var plates = {};
    var order = null; // never equal to a snapshot's key, so the first one always builds

    // Resolves to the server's refusal text, or null when the action was taken.
    function post(tag, action, value) {
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
    }

    function update(snapshot) {
      // A snapshot with no controllers section at all is not "no loops" (that
      // is an empty section): leave the faceplates, and whatever the operator
      // has typed into them, as they are.
      if (!isObject(snapshot) || !isObject(snapshot.controllers)) return;

      var tags = controllerTags(snapshot);
      var key = tags.join("\n");

      // Rebuilt only when the set of loops changes (a scenario's plant).
      if (key !== order) {
        order = key;
        plates = {};
        container.textContent = "";
        if (!tags.length) container.appendChild(el(doc, "p", { class: "faceplate-empty" }, "No controllers in this plant."));
        tags.forEach(function (tag) {
          plates[tag] = buildFaceplate(doc, tag, post);
          container.appendChild(plates[tag].element);
        });
      }

      tags.forEach(function (tag) {
        plates[tag].show(snapshot.controllers[tag]);
      });
    }

    return { update: update };
  }

  api.mount = mount;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Faceplates = api;
})(typeof window !== "undefined" ? window : globalThis);
