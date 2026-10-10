/* Process graphic (T16-3).
 *
 * Binds the live snapshot to the shown plant's graphic by tag, so a new piece
 * of equipment is new SVG markup and no JavaScript. This file knows no tag and
 * no device type: it only follows the data-* attributes an element carries.
 *
 * Which graphic is the plant's to say (T20-2): `follow` reads GET /api/plant,
 * mounts the `graphic` it names (static/graphics/<plant id>.svg) and mounts
 * again only when a refresh finds a different plant id - a scenario load or
 * unload. A plant with no graphic says so and still lists its equipment.
 *
 *   data-tag="P-101"                 a device's symbol group: sets data-state
 *                                    (running | stopped | none | unknown) and
 *                                    data-band (none | warning | alarm | trip)
 *   data-state-of="P-101"            text: the device's state, glyph and label
 *   data-band-of="P-101"             text: its worst envelope band, glyph and label
 *   data-bind="streams.B-P-101.flow" text: the value at section.key.field, with
 *     data-format="fixed:N | percent"   (default fixed:1) and data-unit="GPM"
 *   data-flow="streams.B-P-101.flow" a pipe: sets data-flow-dir (forward |
 *                                    reverse | none | unknown); the path is
 *                                    drawn in the direction of positive flow
 *   data-fill="equipment.V-101.level" a rect with data-y0 and data-h0: its
 *                                    height follows the 0..1 value
 *   data-unplaced                    text: equipment in the snapshot that no
 *                                    data-tag names, so nothing goes unseen
 *
 * Anything the snapshot does not hold degrades to "--" with data-missing set,
 * or data-state="unknown", and never throws: a plant can publish a device the
 * graphic does not draw yet, or the other way round.
 *
 * What it reads is the operator view (app/api/visibility.py), the only shape a
 * browser receives. State comes from `running` alone. The snapshot does not
 * say an interlock has tripped, so the graphic does not claim one: a trip shows
 * as the envelope's trip band on the point that crossed it, and a machine the
 * trip stopped shows as STOP.
 *
 * Everything above the "browser glue" marker takes element-likes (getAttribute,
 * setAttribute, removeAttribute, textContent) so it runs under Node without a
 * DOM; tests/test_process_graphic.py drives it over the real SVG that way.
 * Colour comes from static/css/tokens.css through the SVG's own <style>.
 */
(function (root) {
  "use strict";

  var PLANT_URL = "/api/plant";
  var NO_GRAPHIC = "No schematic for this plant";

  var MISSING = "--";

  // Glyphs repeat the --symbol-* tokens in tokens.css: SVG text cannot read a
  // CSS content value, and colour never carries a state alone.
  var STATE_TEXT = {
    running: "▶ RUN",
    stopped: "■ STOP",
    none: "",
    unknown: MISSING,
  };

  // Envelope band severity, from the band label's repeated side: "lo" and "hi"
  // are WARNING, "lolo" ALARM, "lololo" TRIP (app.envelope.evaluator.isa_band).
  var SEVERITY = { 1: "warning", 2: "alarm", 3: "trip" };
  var BAND_GLYPH = { warning: "●", alarm: "◆", trip: "▲" };

  // A stopped machine settles just off zero (0.055 GPM on the reference pump),
  // so only a flow clear of that noise is drawn as moving.
  var FLOW_EPSILON = 0.1;

  function isObject(value) {
    return value !== null && typeof value === "object";
  }

  function numeric(value) {
    return typeof value === "number" && isFinite(value) ? value : null;
  }

  /* The value at "section.key.field", or undefined at the first thing missing.
   * Tags and fields hold no dot, so a split is exact. */
  function resolvePath(snapshot, path) {
    var node = snapshot;
    var parts = String(path).split(".");
    for (var i = 0; i < parts.length; i++) {
      if (!isObject(node) || !Object.prototype.hasOwnProperty.call(node, parts[i])) {
        return undefined;
      }
      node = node[parts[i]];
    }
    return node;
  }

  function formatValue(value, format, unit) {
    var n = numeric(value);
    if (n === null) return MISSING;

    var spec = format || "fixed:1";
    var text;
    if (spec === "percent") {
      text = String(Math.round(n * 100)) + "%";
    } else {
      var digits = spec.indexOf("fixed:") === 0 ? parseInt(spec.slice(6), 10) : 1;
      if (!(digits >= 0 && digits <= 6)) digits = 1;
      // A value that rounds to zero reads 0, never -0.0.
      text = (Math.abs(n) < 0.5 * Math.pow(10, -digits) ? 0 : n).toFixed(digits);
    }
    return unit ? text + " " + unit : text;
  }

  function flowDirection(value) {
    var n = numeric(value);
    if (n === null) return "unknown";
    if (n > FLOW_EPSILON) return "forward";
    if (n < -FLOW_EPSILON) return "reverse";
    return "none";
  }

  function bandSeverity(band) {
    if (typeof band !== "string" || band.length === 0 || band.length % 2 !== 0) return null;
    return SEVERITY[band.length / 2] || null;
  }

  /* The worst envelope band on any point of `tag`, as {severity, band} or null.
   * An envelope key is "TAG.variable"; the first of equal severity wins. */
  function worstBand(snapshot, tag) {
    var envelope = isObject(snapshot) ? snapshot.envelope : null;
    var worst = null;
    if (!isObject(envelope)) return null;

    Object.keys(envelope).forEach(function (key) {
      if (key.indexOf(tag + ".") !== 0 || !isObject(envelope[key])) return;
      var band = envelope[key].band;
      var severity = bandSeverity(band);
      if (severity === null) return;
      if (worst === null || band.length > worst.band.length) {
        worst = { severity: severity, band: band };
      }
    });
    return worst;
  }

  function deviceState(snapshot, tag) {
    var row = resolvePath(snapshot, "equipment." + tag);
    if (!isObject(row)) return "unknown";
    if (row.running === true) return "running";
    if (row.running === false) return "stopped";
    return "none";
  }

  function bandText(band) {
    return band ? BAND_GLYPH[band.severity] + " " + band.band.toUpperCase() : "";
  }

  /* ---- element updates ------------------------------------------------- */

  function attr(el, name) {
    var value = el.getAttribute(name);
    return value === null || value === undefined ? null : value;
  }

  function setMissing(el, missing) {
    if (missing) el.setAttribute("data-missing", "true");
    else el.removeAttribute("data-missing");
  }

  function applyElement(el, snapshot, placed) {
    var tag = attr(el, "data-tag");
    if (tag !== null) {
      placed[tag] = true;
      var state = deviceState(snapshot, tag);
      var band = worstBand(snapshot, tag);
      el.setAttribute("data-state", state);
      el.setAttribute("data-band", band ? band.severity : "none");
    }

    var stateOf = attr(el, "data-state-of");
    if (stateOf !== null) el.textContent = STATE_TEXT[deviceState(snapshot, stateOf)];

    var bandOf = attr(el, "data-band-of");
    if (bandOf !== null) el.textContent = bandText(worstBand(snapshot, bandOf));

    var bind = attr(el, "data-bind");
    if (bind !== null) {
      var value = resolvePath(snapshot, bind);
      el.textContent = formatValue(value, attr(el, "data-format"), attr(el, "data-unit"));
      setMissing(el, numeric(value) === null);

      // equipment.TAG.field is one envelope point, keyed "TAG.field".
      var parts = bind.split(".");
      var envelope = isObject(snapshot) ? snapshot.envelope : null;
      var own =
        parts[0] === "equipment" && parts.length === 3 && isObject(envelope)
          ? envelope[parts[1] + "." + parts[2]]
          : null;
      el.setAttribute("data-band", (isObject(own) && bandSeverity(own.band)) || "none");
    }

    var flow = attr(el, "data-flow");
    if (flow !== null) el.setAttribute("data-flow-dir", flowDirection(resolvePath(snapshot, flow)));

    var fill = attr(el, "data-fill");
    if (fill !== null) {
      var level = numeric(resolvePath(snapshot, fill));
      var y0 = parseFloat(attr(el, "data-y0"));
      var h0 = parseFloat(attr(el, "data-h0"));
      var fraction = level === null ? 0 : Math.min(1, Math.max(0, level));
      el.setAttribute("height", String(h0 * fraction));
      el.setAttribute("y", String(y0 + h0 * (1 - fraction)));
      setMissing(el, level === null);
    }
  }

  /* Apply `snapshot` to every element, and return {unplaced}: the tags the
   * snapshot publishes that no element's data-tag names. */
  function update(elements, snapshot) {
    var placed = {};
    var notes = [];

    elements.forEach(function (el) {
      applyElement(el, snapshot, placed);
      if (attr(el, "data-unplaced") !== null) notes.push(el);
    });

    var equipment = isObject(snapshot) && isObject(snapshot.equipment) ? snapshot.equipment : {};
    var unplaced = Object.keys(equipment).filter(function (tag) {
      return !placed[tag];
    });
    notes.forEach(function (el) {
      el.textContent = unplaced.length ? "Not on graphic: " + unplaced.join(", ") : "";
    });
    return { unplaced: unplaced };
  }

  /* ---- browser glue ---------------------------------------------------- */

  var BOUND =
    "[data-tag],[data-state-of],[data-band-of],[data-bind],[data-flow],[data-fill],[data-unplaced]";

  function collect(svg) {
    return Array.prototype.slice.call(svg.querySelectorAll(BOUND));
  }

  /* Load the SVG at `options.url` into `container` and keep it bound.
   * `options.fetch` exists so a page or test can supply its own transport, and
   * `options.stale`, when it returns true, stops a load that finishes after
   * this graphic was replaced from writing over its successor; returns {ready, update}, where update(snapshot) may be called before
   * `ready` resolves (the latest snapshot is applied once the SVG is in). */
  function mount(container, options) {
    var settings = options || {};
    var doFetch = settings.fetch || root.fetch.bind(root);
    var elements = [];
    var latest = null;
    var loaded = false;

    function draw() {
      if (loaded && latest !== null) update(elements, latest);
    }

    // Only a failed load says so; a binding error must not wipe a graphic that
    // is already on screen.
    function unavailable() {
      container.textContent = "Process graphic unavailable";
      container.setAttribute("role", "status");
    }

    var ready = doFetch(settings.url)
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.text();
      })
      .then(
        function (markup) {
          if (settings.stale && settings.stale()) return;
          container.innerHTML = markup;
          elements = collect(container);
          loaded = true;
          draw();
        },
        function () {
          if (!(settings.stale && settings.stale())) unavailable();
        }
      );

    return {
      ready: ready,
      update: function (snapshot) {
        latest = snapshot;
        draw();
      },
    };
  }

  /* A plant with no graphic: the notice, and the unplaced list, which then
   * names every device the snapshot publishes. */
  function mountNone(container) {
    var doc = container.ownerDocument;
    var notice = doc.createElement("p");
    var list = doc.createElement("p");
    notice.textContent = NO_GRAPHIC;
    notice.setAttribute("role", "status");
    list.setAttribute("data-unplaced", "");
    container.textContent = "";
    container.appendChild(notice);
    container.appendChild(list);
    var elements = [list];
    var latest = null;

    return {
      ready: Promise.resolve(),
      update: function (snapshot) {
        latest = snapshot;
        if (latest !== null) update(elements, latest);
      },
    };
  }

  /* The plant id and graphic a GET /api/plant body names, or null when it
   * names no plant. */
  function plantOf(body) {
    if (!isObject(body) || typeof body.plant !== "string") return null;
    return { plant: body.plant, graphic: typeof body.graphic === "string" ? body.graphic : null };
  }

  /* Mount the graphic of the plant the session shows, and follow it. Returns
   * {ready, update, refresh}: update(snapshot) as `mount`'s, and refresh(),
   * which rereads GET /api/plant and re-mounts only when the plant id has
   * changed. A snapshot of the new plant that arrives before the swap degrades
   * on the old graphic as any other mismatch does. A failed read leaves what
   * is shown in place. `options.fetch` and `options.mount` (the two mount
   * functions) exist so a test can supply its own. */
  function follow(container, options) {
    var settings = options || {};
    var doFetch = settings.fetch || root.fetch.bind(root);
    var mounts = settings.mount || { graphic: mount, none: mountNone };
    var shown = null;
    var current = null;
    var latest = null;
    var ticket = 0;
    var generation = 0;

    function refresh() {
      var mine = ++ticket;

      return doFetch(PLANT_URL)
        .then(function (response) {
          if (!response.ok) throw new Error("HTTP " + response.status);
          return response.json();
        })
        .then(function (body) {
          var next = plantOf(body);
          // A newer refresh knows better, and the same plant keeps its graphic.
          if (mine !== ticket || next === null || (shown !== null && next.plant === shown)) return;
          shown = next.plant;
          var own = ++generation;
          var stale = function () {
            return own !== generation;
          };
          // An earlier "unavailable" no longer speaks for what is shown.
          container.removeAttribute("role");
          current = next.graphic === null
            ? mounts.none(container)
            : mounts.graphic(container, { fetch: settings.fetch, url: next.graphic, stale: stale });
          if (latest !== null) current.update(latest);
          return current.ready;
        })
        .catch(function () {
          if (current === null) {
            container.textContent = "Process graphic unavailable";
            container.setAttribute("role", "status");
          }
        });
    }

    return {
      ready: refresh(),
      refresh: refresh,
      update: function (snapshot) {
        latest = snapshot;
        if (current !== null) current.update(snapshot);
      },
    };
  }

  var api = {
    resolvePath: resolvePath,
    formatValue: formatValue,
    flowDirection: flowDirection,
    worstBand: worstBand,
    deviceState: deviceState,
    update: update,
    collect: collect,
    mount: mount,
    mountNone: mountNone,
    plantOf: plantOf,
    follow: follow,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ProcessGraphic = api;
})(typeof window !== "undefined" ? window : globalThis);
