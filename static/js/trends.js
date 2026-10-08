/* Trend display (T17-4).
 *
 * The console surface for the trend API (app/api/trend.py) and the alarm
 * record (app/api/alarms.py):
 *   GET /api/trend/points  -> {points, limits, max_tags}
 *   GET /api/trend         -> {point: [[t, v], ...]}
 *   GET /api/alarms/history
 *
 * A pen is a trend point (`<id>.<field>`); the server publishes every point,
 * the evaluated envelope bounds of the ones that have them, and the pen bound,
 * so none of that is copied here. Time is simulated seconds, the same clock as
 * the stream's sim_time. The display keeps no history of its own: each fetch
 * replaces what is drawn, so a scenario load, unload or abort (which swaps the
 * history, and can send simulated time backwards) needs no special case.
 *
 * Every pen is scaled to its own range, so a pressure and a level share one
 * plot. The focused pen gives the y-axis and the envelope bands behind it; a
 * band is a labelled rule as well as a tint, and a pen is a dash pattern and a
 * number as well as a stroke, so nothing rests on colour alone.
 *
 * Everything above the "browser glue" marker is pure (data in, numbers or an
 * HTML/SVG string out) so it runs under Node without a DOM;
 * tests/test_trend_display.py drives it that way. Colour comes from
 * static/css/tokens.css through trends.css, never from here.
 */
(function (root) {
  "use strict";

  var POINTS_URL = "/api/trend/points";
  var TREND_URL = "/api/trend";
  var ALARMS_URL = "/api/alarms/history";

  // The buffer holds 30 simulated minutes (config.TREND_CAPACITY).
  var SPANS = [1, 5, 10, 30];
  var DEFAULT_SPAN = 10;
  var MIN_POINTS = 2;
  var PAD = 0.05;

  // Outermost last. A bound fills from its value outward to the next one.
  var HIGH_BOUNDS = [
    { key: "warning_hi", severity: "warning" },
    { key: "alarm_hi", severity: "alarm" },
    { key: "trip_hi", severity: "trip" },
  ];
  var LOW_BOUNDS = [
    { key: "warning_lo", severity: "warning" },
    { key: "alarm_lo", severity: "alarm" },
    { key: "trip_lo", severity: "trip" },
  ];
  // app.envelope.evaluator.isa_band, upper-cased as the alarm messages spell
  // it: the side repeated once per severity step (HI, HIHI, HIHIHI).
  var SEVERITY_STEPS = { warning: 1, alarm: 2, trip: 3 };

  // Mirrors --symbol-alarm-* in tokens.css (tests/test_trend_display.py).
  var PRIORITY_GLYPH = { critical: "▲", high: "◆", low: "●" };

  // Eight pens, eight dash patterns: the second channel after the number.
  var DASHES = ["", "7 3", "2 3", "9 3 2 3", "4 4", "1 4", "12 4", "6 2 1 2"];

  // The right margin holds the pen numbers; a tick label needs half its width
  // (TICK_HALF) clear on both sides, so a tick nearer an edge is not drawn.
  var MARGIN = { top: 16, right: 22, bottom: 26, left: 52 };
  var TICK_HALF = 26;
  var NUMBER_GAP = 11;
  // A spread below this fraction of the magnitude is float noise, not signal.
  var FLAT = 1e-6;

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function isNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function round(value) {
    return Math.round(value * 100) / 100;
  }

  function formatValue(value) {
    if (!isNumber(value)) return "--";
    return String(Number(value.toPrecision(4)));
  }

  function formatClock(seconds) {
    var whole = Math.max(0, Math.floor(seconds));
    var pad = function (n) {
      return String(n).padStart(2, "0");
    };
    return pad(Math.floor(whole / 3600)) + ":" + pad(Math.floor(whole / 60) % 60) + ":" + pad(whole % 60);
  }

  /* ---- selection --------------------------------------------------------- */

  /* The pens shown until the operator picks their own: every point with
   * evaluated limits, then each loop's PV and SP, up to the server's bound. */
  function defaultSelection(points, limits, controllers, maxTags) {
    var known = new Set(points);
    var wanted = Object.keys(isObject(limits) ? limits : {});

    Object.keys(isObject(controllers) ? controllers : {}).forEach(function (loop) {
      wanted.push(loop + ".pv", loop + ".sp");
    });

    var chosen = [];
    wanted.forEach(function (point) {
      if (known.has(point) && chosen.indexOf(point) < 0) chosen.push(point);
    });
    return chosen.slice(0, maxTags);
  }

  /* A stored or edited selection reduced to what the plant publishes now:
   * unknown points dropped, duplicates dropped, capped at the pen bound. */
  function sanitizeSelection(selection, points, maxTags) {
    var known = new Set(points);
    var kept = [];
    (Array.isArray(selection) ? selection : []).forEach(function (point) {
      if (known.has(point) && kept.indexOf(point) < 0) kept.push(point);
    });
    return kept.slice(0, maxTags);
  }

  function sanitizeSpan(span) {
    return SPANS.indexOf(span) >= 0 ? span : DEFAULT_SPAN;
  }

  function sanitizeFocus(focus, selection) {
    return selection.indexOf(focus) >= 0 ? focus : selection.length ? selection[0] : null;
  }

  /* ---- requests ---------------------------------------------------------- */

  /* The window ends at the latest simulated time and reaches `span` minutes
   * back, but never before simulated time zero. */
  function windowFor(simTime, spanMinutes) {
    return { from: Math.max(0, simTime - spanMinutes * 60), to: simTime };
  }

  /* The plot's pixel width, kept within 2 and the server's `max_points`. */
  function pointsFor(plotWidth, maxPoints) {
    var wanted = Math.max(MIN_POINTS, Math.round(plotWidth));
    return isNumber(maxPoints) ? Math.min(maxPoints, wanted) : wanted;
  }

  function buildTrendUrl(tags, win, maxPoints) {
    return (
      TREND_URL +
      "?tags=" + tags.map(encodeURIComponent).join(",") +
      "&from=" + win.from +
      "&to=" + win.to +
      "&max_points=" + maxPoints
    );
  }

  /* ---- scaling ----------------------------------------------------------- */

  /* A pen's own y range: its window's samples and its limits, padded 5%. A
   * flat pen has no spread to pad, so it gets +/-max(5% of |v|, 1). */
  function scaleFor(samples, limits) {
    var values = [];
    (samples || []).forEach(function (pair) {
      if (isNumber(pair[1])) values.push(pair[1]);
    });
    Object.keys(limits || {}).forEach(function (key) {
      if (isNumber(limits[key])) values.push(limits[key]);
    });

    if (!values.length) return { lo: 0, hi: 1 };

    var lo = Math.min.apply(null, values);
    var hi = Math.max.apply(null, values);

    if (hi - lo <= FLAT * Math.max(Math.abs(lo), Math.abs(hi), 1)) {
      lo = (lo + hi) / 2;
      hi = lo;
      var half = Math.max(PAD * Math.abs(lo), 1);
      return { lo: lo - half, hi: hi + half };
    }

    var pad = (hi - lo) * PAD;
    return { lo: lo - pad, hi: hi + pad };
  }

  /* Roughly `count` round-numbered ticks inside [lo, hi]. */
  function ticksFor(lo, hi, count) {
    var rough = (hi - lo) / Math.max(1, count);
    var magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
    var step = [1, 2, 5, 10].map(function (m) {
      return m * magnitude;
    }).reduce(function (best, candidate) {
      return Math.abs(Math.log(candidate / rough)) < Math.abs(Math.log(best / rough)) ? candidate : best;
    });
    var ticks = [];
    for (var tick = Math.ceil(lo / step) * step; tick <= hi + step * 1e-9; tick += step) {
      ticks.push(Number(tick.toPrecision(12)));
    }
    return ticks;
  }

  /* ---- bands and markers ------------------------------------------------- */

  /* Rules and tinted bands for one pen's limits over [scale.lo, scale.hi].
   *
   * Each bound fills outward to the next outer bound or the edge of the plot
   * (warning to alarm to trip), and every bound is also a rule labelled with
   * the ISA ladder as the alarm messages spell it: HI, HIHI, HIHIHI on the high
   * side, LO, LOLO, LOLOLO on the low side.
   * Bounds outside the visible range draw no rule, but the band they begin
   * still reaches into view if its tint does. */
  function bandsFor(limits, scale) {
    var bands = [];
    var rules = [];

    function side(bounds, outwardIsUp, word) {
      var present = bounds.filter(function (bound) {
        return limits && isNumber(limits[bound.key]);
      });
      present.forEach(function (bound, index) {
        var value = limits[bound.key];
        var next = present[index + 1];
        var edge = outwardIsUp ? scale.hi : scale.lo;
        var far = next ? limits[next.key] : edge;
        var from = outwardIsUp ? value : far;
        var to = outwardIsUp ? far : value;
        var lo = Math.max(from, scale.lo);
        var hi = Math.min(to, scale.hi);

        if (hi > lo) bands.push({ severity: bound.severity, lo: lo, hi: hi });

        if (value >= scale.lo && value <= scale.hi) {
          rules.push({
            severity: bound.severity,
            value: value,
            label: new Array(SEVERITY_STEPS[bound.severity] + 1).join(word),
          });
        }
      });
    }

    side(HIGH_BOUNDS, true, "HI");
    side(LOW_BOUNDS, false, "LO");

    return { bands: bands, rules: rules };
  }

  /* The alarm events inside the window, oldest first: raising events only
   * (an acknowledge or a clear is not drawn). */
  function markersFor(entries, win) {
    return (Array.isArray(entries) ? entries : [])
      .filter(function (entry) {
        return (
          isObject(entry) && entry.type === "alarm" && isNumber(entry.sim_time) &&
          entry.sim_time >= win.from && entry.sim_time <= win.to
        );
      })
      .map(function (entry) {
        return {
          time: entry.sim_time,
          priority: PRIORITY_GLYPH[entry.priority] ? entry.priority : "low",
          tag: entry.tag,
          message: entry.message,
        };
      })
      .sort(function (a, b) {
        return a.time - b.time;
      });
  }

  /* ---- drawing ----------------------------------------------------------- */

  /* An SVG path through `samples`, scaled into the plot. A null reading
   * breaks the line rather than being bridged. */
  function pathFor(samples, toX, toY) {
    var parts = [];
    var pen = false;

    (samples || []).forEach(function (pair) {
      if (!isNumber(pair[1]) || !isNumber(pair[0])) {
        pen = false;
        return;
      }
      parts.push((pen ? "L" : "M") + round(toX(pair[0])) + " " + round(toY(pair[1])));
      pen = true;
    });

    return parts.join(" ");
  }

  function latest(samples) {
    for (var i = (samples || []).length - 1; i >= 0; i--) {
      if (isNumber(samples[i][1])) return samples[i][1];
    }
    return null;
  }

  /* One plot as an SVG string.
   *   input.width, input.height  pixels of the whole drawing
   *   input.window               {from, to} simulated seconds
   *   input.pens                 [{point, samples}], in selection order
   *   input.limits               point -> bounds
   *   input.focus                the pen whose scale and bands are drawn
   *   input.markers              markersFor(...)
   */
  function renderChart(input) {
    var width = input.width;
    var height = input.height;
    var win = input.window;
    var left = MARGIN.left;
    var top = MARGIN.top;
    var plotW = Math.max(1, width - MARGIN.left - MARGIN.right);
    var plotH = Math.max(1, height - MARGIN.top - MARGIN.bottom);
    var span = win.to - win.from || 1;
    var toX = function (t) {
      return left + ((t - win.from) / span) * plotW;
    };

    var scales = input.pens.map(function (pen) {
      return scaleFor(pen.samples, (input.limits || {})[pen.point]);
    });
    var focusIndex = input.pens.findIndex(function (pen) {
      return pen.point === input.focus;
    });
    if (focusIndex < 0) focusIndex = 0;
    var focusScale = scales[focusIndex] || { lo: 0, hi: 1 };

    var yFor = function (scale) {
      return function (v) {
        return top + (1 - (v - scale.lo) / (scale.hi - scale.lo)) * plotH;
      };
    };
    var focusY = yFor(focusScale);
    var focusPen = input.pens[focusIndex];
    var shading = bandsFor(focusPen ? (input.limits || {})[focusPen.point] : null, focusScale);

    var svg = [];
    svg.push(
      '<svg class="trend-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + width + " " + height +
      '" width="' + width + '" height="' + height + '" role="img" aria-label="' +
      escapeHtml(chartLabel(input.pens, win)) + '">'
    );

    svg.push(
      '<rect class="trend-plot" x="' + left + '" y="' + top + '" width="' + plotW + '" height="' + plotH + '"/>'
    );

    shading.bands.forEach(function (band) {
      var yTop = focusY(band.hi);
      svg.push(
        '<rect class="trend-band" data-severity="' + band.severity + '" x="' + left + '" y="' + round(yTop) +
        '" width="' + plotW + '" height="' + round(focusY(band.lo) - yTop) + '"/>'
      );
    });

    var yTicks = ticksFor(focusScale.lo, focusScale.hi, Math.max(2, Math.round(plotH / 44)));
    yTicks.forEach(function (tick) {
      var y = round(focusY(tick));
      svg.push('<line class="trend-grid" x1="' + left + '" x2="' + (left + plotW) + '" y1="' + y + '" y2="' + y + '"/>');
      svg.push('<text class="trend-tick" x="' + (left - 6) + '" y="' + y + '" text-anchor="end" dominant-baseline="middle">' +
        escapeHtml(formatValue(tick)) + "</text>");
    });

    var xTicks = ticksFor(win.from, win.to, Math.max(2, Math.round(plotW / 90)));
    xTicks.forEach(function (tick) {
      var x = round(toX(tick));
      if (x < TICK_HALF || x > width - TICK_HALF) return;
      svg.push('<line class="trend-grid" x1="' + x + '" x2="' + x + '" y1="' + top + '" y2="' + (top + plotH) + '"/>');
      svg.push('<text class="trend-tick" x="' + x + '" y="' + (top + plotH + 16) + '" text-anchor="middle">' +
        formatClock(tick) + "</text>");
    });

    shading.rules.forEach(function (rule) {
      var y = round(focusY(rule.value));
      svg.push(
        '<line class="trend-rule" data-severity="' + rule.severity + '" x1="' + left + '" x2="' + (left + plotW) +
        '" y1="' + y + '" y2="' + y + '"/>'
      );
      svg.push(
        '<text class="trend-rule-label" data-severity="' + rule.severity + '" x="' + (left + plotW - 4) +
        '" y="' + (y - 3) + '" text-anchor="end">' + rule.label + " " + escapeHtml(formatValue(rule.value)) + "</text>"
      );
    });

    // The focused pen is drawn last, on top.
    var order = input.pens.map(function (pen, index) {
      return index;
    }).filter(function (index) {
      return index !== focusIndex;
    });
    if (input.pens.length) order.push(focusIndex);

    order.forEach(function (index) {
      var pen = input.pens[index];
      var d = pathFor(pen.samples, toX, yFor(scales[index]));
      var dash = DASHES[index % DASHES.length];
      svg.push(
        '<path class="trend-pen" data-pen="' + (index + 1) + '"' + (index === focusIndex ? ' data-focus="true"' : "") +
        ' data-point="' + escapeHtml(pen.point) + '"' + (dash ? ' stroke-dasharray="' + dash + '"' : "") +
        ' d="' + d + '"/>'
      );
    });

    // Each pen's number sits in the right margin at the height of its last
    // reading, nudged apart where pens end together.
    var numbers = [];
    input.pens.forEach(function (pen, index) {
      var value = latest(pen.samples);
      if (value !== null) numbers.push({ index: index, y: yFor(scales[index])(value) });
    });
    numbers.sort(function (a, b) {
      return a.y - b.y;
    });
    numbers.forEach(function (number, i) {
      if (i > 0) number.y = Math.max(number.y, numbers[i - 1].y + NUMBER_GAP);
    });
    // The push down can run past the plot: clamp, then pack back upward.
    for (var n = numbers.length - 1; n >= 0; n--) {
      var limit = n === numbers.length - 1 ? top + plotH : numbers[n + 1].y - NUMBER_GAP;
      numbers[n].y = Math.min(numbers[n].y, limit);
    }
    numbers.forEach(function (number) {
      svg.push(
        '<text class="trend-pen-number" data-pen="' + (number.index + 1) + '" x="' + (left + plotW + 4) +
        '" y="' + round(number.y) + '" dominant-baseline="middle">' + (number.index + 1) + "</text>"
      );
    });

    (input.markers || []).forEach(function (marker) {
      var x = round(toX(marker.time));
      var label = marker.priority + " alarm at " + formatClock(marker.time) + ": " + marker.message;
      svg.push(
        '<g class="trend-marker" data-priority="' + escapeHtml(marker.priority) + '" role="img" aria-label="' +
        escapeHtml(label) + '"><title>' + escapeHtml(label) + "</title>" +
        '<line x1="' + x + '" x2="' + x + '" y1="' + top + '" y2="' + (top + plotH) + '"/>' +
        '<text x="' + x + '" y="' + (top - 3) + '" text-anchor="middle">' +
        (PRIORITY_GLYPH[marker.priority] || "●") + "</text></g>"
      );
    });

    svg.push("</svg>");
    return svg.join("");
  }

  function chartLabel(pens, win) {
    if (!pens.length) return "Trend: no pens selected";
    return (
      "Trend of " + pens.map(function (pen) {
        return pen.point;
      }).join(", ") + " from " + formatClock(win.from) + " to " + formatClock(win.to)
    );
  }

  /* The span and add-a-pen selectors. Kept apart from the legend so a tick
   * that only moves a value never rebuilds a select the operator has open. */
  function renderSelectors(view) {
    var spans = SPANS.map(function (minutes) {
      return (
        '<option value="' + minutes + '"' + (minutes === view.span ? " selected" : "") + ">" +
        minutes + " min</option>"
      );
    }).join("");

    var selected = new Set(view.selection);
    var addable = view.points.filter(function (point) {
      return !selected.has(point);
    });
    var full = view.selection.length >= view.maxTags;
    var add =
      '<option value="">' + (full ? "Pen limit reached (" + view.maxTags + ")" : "Add a pen") + "</option>" +
      addable.map(function (point) {
        return '<option value="' + escapeHtml(point) + '">' + escapeHtml(point) + "</option>";
      }).join("");

    return (
      '<div class="trend-selectors">' +
      '<label>Span <select class="trend-span" data-span>' + spans + "</select></label>" +
      '<label>Pen <select class="trend-add" data-add' + (full || !addable.length ? " disabled" : "") + ">" + add +
      "</select></label></div>"
    );
  }

  /* One row per pen: its number, dash, latest value, a focus button and a
   * remove button. */
  function renderLegend(view) {
    return '<ol class="trend-legend">' + view.selection.map(function (point, index) {
      var dash = DASHES[index % DASHES.length];
      var focused = point === view.focus;
      return (
        '<li class="trend-pen-row" data-pen="' + (index + 1) + '"' + (focused ? ' data-focus="true"' : "") + ">" +
        '<button type="button" class="trend-focus" data-focus-point="' + escapeHtml(point) +
        '" aria-pressed="' + (focused ? "true" : "false") + '">' +
        '<svg class="trend-swatch" viewBox="0 0 28 10" width="28" height="10" aria-hidden="true"><line x1="0" x2="28" y1="5" y2="5"' +
        (dash ? ' stroke-dasharray="' + dash + '"' : "") + "/></svg>" +
        '<span class="trend-pen-index">' + (index + 1) + "</span>" +
        '<span class="trend-pen-point">' + escapeHtml(point) + "</span>" +
        '<span class="trend-pen-value" data-live-value data-value-point="' + escapeHtml(point) + '">' + formatValue((view.latest || {})[point]) + "</span></button>" +
        '<button type="button" class="trend-remove" data-remove-point="' + escapeHtml(point) +
        '" aria-label="Remove ' + escapeHtml(point) + '" title="Remove">\u00D7</button></li>'
      );
    }).join("") + "</ol>";
  }

  var api = {
    SPANS: SPANS,
    DEFAULT_SPAN: DEFAULT_SPAN,
    PRIORITY_GLYPH: PRIORITY_GLYPH,
    DASHES: DASHES,
    POINTS_URL: POINTS_URL,
    ALARMS_URL: ALARMS_URL,
    defaultSelection: defaultSelection,
    sanitizeSelection: sanitizeSelection,
    sanitizeSpan: sanitizeSpan,
    sanitizeFocus: sanitizeFocus,
    windowFor: windowFor,
    pointsFor: pointsFor,
    buildTrendUrl: buildTrendUrl,
    scaleFor: scaleFor,
    ticksFor: ticksFor,
    bandsFor: bandsFor,
    markersFor: markersFor,
    pathFor: pathFor,
    renderChart: renderChart,
    renderSelectors: renderSelectors,
    renderLegend: renderLegend,
    formatValue: formatValue,
    formatClock: formatClock,
  };

  /* ---- browser glue ---------------------------------------------------- */

  var STORAGE_KEY = "plant-simulator.trends";
  var TICK_MS = 2000;
  var FETCH_TIMEOUT_MS = 4 * TICK_MS;
  var FALLBACK_WIDTH = 640;

  function readStored(storage) {
    try {
      var parsed = JSON.parse(storage.getItem(STORAGE_KEY));
      return isObject(parsed) ? parsed : {};
    } catch (error) {
      return {};
    }
  }

  function writeStored(storage, value) {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(value));
    } catch (error) {
      // Storage can be absent or full; the display works without it.
    }
  }

  function defaultStorage() {
    try {
      return root.localStorage;
    } catch (error) {
      return null;
    }
  }

  /* Mount the trend into `container`. `options.fetch`, `options.pollMs`,
   * `options.storage` and `options.width` exist so a page or test can supply
   * its own transport, timer, storage and size. Returns {ready, update,
   * refresh, poll, stop}: feed it every snapshot with `update`; `ready` resolves
   * once the first fetch has been drawn. */
  function mount(container, options) {
    var settings = options || {};
    var doFetch = settings.fetch || root.fetch.bind(root);
    var storage = settings.storage === undefined ? defaultStorage() : settings.storage;
    var pollMs = settings.pollMs === undefined ? TICK_MS : settings.pollMs;
    var timeoutMs = settings.timeoutMs || FETCH_TIMEOUT_MS;
    var doc = container.ownerDocument || root.document;
    var stored = storage ? readStored(storage) : {};

    var state = {
      span: sanitizeSpan(stored.span),
      selection: Array.isArray(stored.selection) ? stored.selection : null,
      focus: typeof stored.focus === "string" ? stored.focus : null,
      points: [],
      limits: {},
      maxTags: 8,
      maxPoints: null,
      data: {},
      markers: [],
      win: null,
      simTime: null,
      controllers: {},
      problem: "",
    };
    var ticket = 0;
    var drawnSelectors = null;
    var drawnLegend = null;
    var polling = false;
    var timer = null;

    var controlsEl = doc.createElement("div");
    var legendEl = doc.createElement("div");
    var chartEl = doc.createElement("div");
    var statusEl = doc.createElement("p");
    controlsEl.setAttribute("class", "trend-controls");
    legendEl.setAttribute("class", "trend-legend-box");
    chartEl.setAttribute("class", "trend-chart");
    statusEl.setAttribute("class", "trend-status");
    statusEl.setAttribute("role", "status");
    container.appendChild(controlsEl);
    container.appendChild(legendEl);
    container.appendChild(chartEl);
    container.appendChild(statusEl);

    function persist() {
      if (storage) writeStored(storage, { span: state.span, selection: state.selection, focus: state.focus });
    }

    function selection() {
      var chosen = state.selection === null
        ? defaultSelection(state.points, state.limits, state.controllers, state.maxTags)
        : sanitizeSelection(state.selection, state.points, state.maxTags);
      return chosen;
    }

    function width() {
      return Math.max(280, Math.round(settings.width || container.clientWidth || FALLBACK_WIDTH));
    }

    function draw() {
      var chosen = selection();
      var focus = sanitizeFocus(state.focus, chosen);
      var drawnWidth = width();
      var latestValues = {};

      var pens = chosen.map(function (point) {
        var samples = state.data[point] || [];
        latestValues[point] = latest(samples);
        return { point: point, samples: samples };
      });

      var view = {
        span: state.span,
        points: state.points,
        selection: chosen,
        maxTags: state.maxTags,
        focus: focus,
        latest: latestValues,
      };
      var selectors = renderSelectors(view);
      if (selectors !== drawnSelectors) {
        drawnSelectors = selectors;
        controlsEl.innerHTML = selectors;
      }
      // The legend is rebuilt only when its rows change, so keyboard focus
      // survives a tick; values are rewritten in place.
      var legend = renderLegend({ span: view.span, selection: chosen, focus: focus, latest: {} });
      if (legend !== drawnLegend) {
        drawnLegend = legend;
        legendEl.innerHTML = legend;
      }
      legendEl.querySelectorAll("[data-live-value]").forEach(function (node) {
        node.textContent = formatValue(latestValues[node.getAttribute("data-value-point")]);
      });

      if (state.simTime === null) {
        chartEl.innerHTML = '<p class="trend-empty">Waiting for the plant.</p>';
      } else if (!chosen.length) {
        chartEl.innerHTML = '<p class="trend-empty">No pens selected. Add one to draw it.</p>';
      } else {
        chartEl.innerHTML = renderChart({
          width: drawnWidth,
          height: drawnWidth < 480 ? 220 : 280,
          window: state.win || windowFor(state.simTime, state.span),
          pens: pens,
          limits: state.limits,
          focus: focus,
          markers: state.markers,
        });
      }

      statusEl.textContent = state.problem;
    }

    /* A request that never settles would hold the poll flag for good, so each
     * one is given a deadline and aborted where the transport allows. */
    async function get(url) {
      var controller = typeof AbortController === "function" ? new AbortController() : null;
      var timer;
      var deadline = new Promise(function (resolve, reject) {
        timer = root.setTimeout(function () {
          if (controller) controller.abort();
          reject(new Error("timed out"));
        }, timeoutMs);
      });

      try {
        var response = await Promise.race([doFetch(url, controller ? { signal: controller.signal } : undefined), deadline]);
        if (!response.ok) throw new Error("HTTP " + response.status);
        return await Promise.race([response.json(), deadline]);
      } finally {
        root.clearTimeout(timer);
      }
    }

    /* Refetch the point list, the window of the selected pens and the alarm
     * record, and replace what is drawn with them. Nothing is skipped for a
     * paused plant: a history swap can leave simulated time where it was, so
     * only a fetch can tell. A newer refresh supersedes an older one still in
     * flight. */
    async function refresh() {
      var mine = ++ticket;

      try {
        var listing = await get(POINTS_URL);
        if (mine !== ticket) return;

        state.points = Array.isArray(listing.points) ? listing.points : [];
        state.limits = isObject(listing.limits) ? listing.limits : {};
        state.maxTags = isNumber(listing.max_tags) ? listing.max_tags : state.maxTags;
        state.maxPoints = isNumber(listing.max_points) ? listing.max_points : state.maxPoints;

        var chosen = selection();

        if (state.simTime === null || !chosen.length) {
          state.data = {};
          state.markers = [];
          state.win = null;
        } else {
          // The window the data is fetched for is the window it is drawn in,
          // however far a later snapshot has moved the clock meanwhile.
          var win = windowFor(state.simTime, state.span);
          var results = await Promise.all([
            get(buildTrendUrl(chosen, win, pointsFor(width() - MARGIN.left - MARGIN.right, state.maxPoints))),
            get(ALARMS_URL),
          ]);
          if (mine !== ticket) return;

          state.data = isObject(results[0]) ? results[0] : {};
          state.markers = markersFor(results[1], win);
          state.win = win;
        }

        state.problem = "";
      } catch (error) {
        if (mine !== ticket) return;
        state.problem = "Trend unavailable: " + error.message;
      }

      draw();
    }

    /* Apply an edit, persist it and refetch. A change to the selection waits
     * for the point list: before it arrives every selection reads as empty,
     * and would overwrite the operator's stored one. An edit turned away is
     * drawn over, so a select snaps back to the real state. */
    function edit(change, selects) {
      if (selects && !state.points.length) {
        drawnSelectors = null;
        draw();
        return;
      }
      change();
      persist();
      draw();
      return refresh();
    }

    /* A rebuilt legend loses the focused button; give focus back to the focus
     * button of `point`, so a keyboard user can press on. */
    function refocus(point) {
      if (!point) return;
      Array.prototype.forEach.call(legendEl.querySelectorAll("[data-focus-point]"), function (button) {
        if (button.getAttribute("data-focus-point") === point && button.focus) button.focus();
      });
    }

    legendEl.addEventListener("click", function (event) {
      var target = event.target;
      if (!target || !target.closest) return;

      var focusButton = target.closest("[data-focus-point]");
      if (focusButton) {
        state.focus = focusButton.getAttribute("data-focus-point");
        persist();
        draw();
        refocus(state.focus);
        return;
      }

      var removeButton = target.closest("[data-remove-point]");
      if (removeButton) {
        var gone = removeButton.getAttribute("data-remove-point");
        var before = selection();
        var result = edit(function () {
          state.selection = before.filter(function (point) {
            return point !== gone;
          });
        }, true);
        // The row that held focus is gone: hand it to the pen that took its place.
        var rest = state.selection === null ? [] : state.selection;
        refocus(rest[Math.min(before.indexOf(gone), rest.length - 1)]);
        return result;
      }
    });

    controlsEl.addEventListener("change", function (event) {
      var target = event.target;
      if (!target || !target.getAttribute) return;

      if (target.getAttribute("data-span") !== null) {
        return edit(function () {
          state.span = sanitizeSpan(Number(target.value));
        });
      }

      if (target.getAttribute("data-add") !== null && target.value) {
        return edit(function () {
          state.selection = selection().concat([target.value]).slice(0, state.maxTags);
          state.focus = target.value;
        }, true);
      }
    });

    /* One timer tick: a refresh, unless the page is in a hidden tab or the
     * last tick is still waiting on a slow server (a user's edit may still
     * supersede it, a later tick never does). */
    async function poll() {
      if ((root.document && root.document.hidden) || polling) return;
      polling = true;
      try {
        await refresh();
      } finally {
        polling = false;
      }
    }

    /* Called with every snapshot: remembers the clock and the loops. The first
     * snapshot draws at once instead of waiting for the next tick. */
    function update(snapshot) {
      if (!isObject(snapshot) || !isNumber(snapshot.sim_time)) return;

      var first = state.simTime === null;
      state.simTime = snapshot.sim_time;
      if (isObject(snapshot.controllers)) state.controllers = snapshot.controllers;
      if (first) return refresh();
    }

    draw();
    var ready = refresh();
    if (pollMs) timer = root.setInterval(poll, pollMs);

    return {
      ready: ready,
      update: update,
      refresh: refresh,
      poll: poll,
      stop: function () {
        if (timer !== null) root.clearInterval(timer);
      },
    };
  }

  api.mount = mount;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Trends = api;
})(typeof window !== "undefined" ? window : globalThis);
