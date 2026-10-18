/* Trend rendering (T17-4, reshaped by T20-11).
 *
 * The pure half of the console's trends, for the trend API (app/api/trend.py)
 * and the alarm record (app/api/alarms.py):
 *   GET /api/trend/points  -> {points, limits, descriptors, units, max_tags, max_points}
 *   GET /api/trend         -> {point: [[t, v], ...]}
 *   GET /api/alarms/history
 *
 * A pen is a trend point (`<id>.<field>`); the server publishes every point,
 * the evaluated envelope bounds of the ones that have them, and the pen bound,
 * so none of that is copied here. Time is simulated seconds, the same clock as
 * the stream's sim_time; it is shown through static/js/clock.js, as run time
 * when the caller passes an `origin` (a scenario's run time zero) and as the
 * plant clock when it passes none.
 *
 * Every pen is scaled to its own range, so a pressure and a level share one
 * plot. The focused pen gives the y-axis and the envelope bands behind it; a
 * band is a labelled rule as well as a tint, and a pen is a dash pattern and a
 * number as well as a stroke, so nothing rests on colour alone. A trend window
 * draws one pen on a held scale (`holdScale`), so a change can be judged by eye.
 *
 * Everything here is pure (data in, numbers or an SVG string out) so it runs
 * under Node without a DOM; tests/test_trend_display.py drives it that way.
 * The windows that show it are trendwindows.js. Colour comes from
 * static/css/tokens.css through trends.css, never from here.
 */
(function (root) {
  "use strict";

  var Clock = typeof module !== "undefined" && module.exports ? require("./clock.js") : root.SimClock;

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
  // it: the side repeated by position, at most twice (HI, HIHI).
  var SEVERITY_STEPS = { warning: 1, alarm: 2, trip: 2 }; // as manager.py _MAX_STEPS, graphic.js

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

  function formatClock(seconds, origin) {
    return Clock.format(seconds, origin);
  }

  /* ---- spans and requests ---------------------------------------------- */

  function sanitizeSpan(span) {
    return SPANS.indexOf(span) >= 0 ? span : DEFAULT_SPAN;
  }

  /* The window ends at the latest simulated time and reaches `span` minutes
   * back, but never before time zero of the clock shown: simulated time zero,
   * or a run's start when `origin` is one. */
  function windowFor(simTime, spanMinutes, origin) {
    var zero = isNumber(origin) ? Math.min(origin, simTime) : 0;
    return { from: Math.max(zero, simTime - spanMinutes * 60), to: simTime };
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

  /* A held scale: `scaleFor` widened to take in `held`, the scale drawn last
   * time. It widens when a reading leaves it and never narrows; the caller
   * drops what it holds when the span changes. With nothing to scale, what is
   * held is kept. */
  function holdScale(held, samples, limits) {
    var next = scaleFor(samples, limits);
    var empty = !(samples || []).some(function (pair) {
      return isNumber(pair[1]);
    }) && !Object.keys(limits || {}).some(function (key) {
      return isNumber(limits[key]);
    });
    if (!held) return next;
    if (empty) return held;
    return { lo: Math.min(held.lo, next.lo), hi: Math.max(held.hi, next.hi) };
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
   * the ISA ladder as the alarm messages spell it: HI, HIHI on the high side,
   * LO, LOLO on the low side, whether the outer limit alarms or trips.
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
   *   input.scales               optional point -> {lo, hi}, a held scale
   *   input.label                optional accessible name of the drawing
   *   input.origin               optional run time zero; times then read as run time
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
      var held = (input.scales || {})[pen.point];
      return held || scaleFor(pen.samples, (input.limits || {})[pen.point]);
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
      '" width="' + width + '" height="' + height + '" role="group" aria-label="' +
      escapeHtml(input.label || chartLabel(input.pens, win, input.origin)) + '">'
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

    // Ticks fall on round numbers of the clock shown, not of the plant's.
    var shift = isNumber(input.origin) ? input.origin : 0;
    var xTicks = ticksFor(win.from - shift, win.to - shift, Math.max(2, Math.round(plotW / 90)));
    xTicks.forEach(function (shown) {
      var tick = shown + shift;
      var x = round(toX(tick));
      if (x < TICK_HALF || x > width - TICK_HALF) return;
      svg.push('<line class="trend-grid" x1="' + x + '" x2="' + x + '" y1="' + top + '" y2="' + (top + plotH) + '"/>');
      svg.push('<text class="trend-tick" x="' + x + '" y="' + (top + plotH + 16) + '" text-anchor="middle">' +
        formatClock(tick, input.origin) + "</text>");
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
      var label = marker.priority + " alarm at " + formatClock(marker.time, input.origin) + ": " + marker.message;
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

  function chartLabel(pens, win, origin) {
    if (!pens.length) return "Trend: no pens selected";
    return (
      "Trend of " + pens.map(function (pen) {
        return pen.point;
      }).join(", ") + " from " + formatClock(win.from, origin) + " to " + formatClock(win.to, origin)
    );
  }

  /* A pen's name for the operator: its equipment or loop id and the
   * descriptor the server publishes ("K-101 discharge pressure"). The point id
   * stays the key everywhere; a point with no descriptor shows as the id. */
  function penName(point, descriptors) {
    var words = descriptors && descriptors[point];
    if (typeof words !== "string" || !words) return point;
    var dot = point.lastIndexOf(".");
    return (dot < 0 ? point : point.slice(0, dot)) + " " + words;
  }

  var api = {
    SPANS: SPANS,
    DEFAULT_SPAN: DEFAULT_SPAN,
    PRIORITY_GLYPH: PRIORITY_GLYPH,
    DASHES: DASHES,
    POINTS_URL: POINTS_URL,
    ALARMS_URL: ALARMS_URL,
    sanitizeSpan: sanitizeSpan,
    windowFor: windowFor,
    pointsFor: pointsFor,
    buildTrendUrl: buildTrendUrl,
    scaleFor: scaleFor,
    holdScale: holdScale,
    ticksFor: ticksFor,
    bandsFor: bandsFor,
    markersFor: markersFor,
    pathFor: pathFor,
    renderChart: renderChart,
    penName: penName,
    formatValue: formatValue,
    formatClock: formatClock,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Trends = api;
})(typeof window !== "undefined" ? window : globalThis);
