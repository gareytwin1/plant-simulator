/* Floating trend windows (T20-11).
 *
 * A trend opens in its own window over the canvas, from a reading on the
 * canvas or a faceplate's Trend button (and, from T20-12, an alarm). Each
 * window shows one point: its live value and unit, spans of 1, 5, 10 and 30
 * minutes, its limit bands and alarm markers, on a vertical scale that widens
 * but never narrows while the span is unchanged. A window moves and resizes by
 * pointer and by keyboard, minimises to a tray that shows its value, and
 * closes. Opening a point that is already open raises its window.
 *
 * One timer tick serves every open window. It asks for the trend listing, the
 * history of all their points at the widest open span (one /api/trend
 * request, sliced per window) and the alarm record: three requests, however
 * many windows are open, and none while no window is. A tick is skipped in a
 * hidden tab and while the last one is still waiting. The windows keep no
 * history of their own: each fetch replaces what is drawn, so a scenario load,
 * unload or abort redraws every window from the new history.
 *
 * Words and units come from the listing (T20-1); a fraction shows as a
 * percent, on the value line and the axis alike. Each point's geometry and
 * span are kept in sessionStorage, so a window reopened later in the session
 * comes back where it was; storage that is missing or throws only loses that.
 *
 * Drawing is trends.js's and placement canvas.js's `place`; the value and its
 * band come from the snapshot through faceplate.js and graphic.js, as on a
 * faceplate. Everything above the "browser glue" marker is pure, so it runs
 * under Node; tests/test_trend_windows.py drives it that way and runs the glue
 * over tests/fake_dom.js. Colour comes from tokens.css through trends.css.
 */
(function (root) {
  "use strict";

  function lib(name, file) {
    if (root[name]) return root[name];
    return typeof require === "function" ? require(file) : null;
  }

  var T = lib("Trends", "./trends.js");

  var STORAGE_KEY = "plant-simulator.trend-windows";
  var TICK_MS = 2000;
  var FETCH_TIMEOUT_MS = 4 * TICK_MS;
  var MISSING = "--";

  var DEFAULT_SIZE = { w: 420, h: 290 };
  var MIN_SIZE = { w: 260, h: 180 };
  var EDGE = 8; // between a window and the workspace edge
  var BAR_REACH = 48; // how much of the top of a window stays in view
  var KEY_STEP = 16;
  var FINE_STEP = 2; // with Alt
  var CASCADE = 28;
  var AVOID_PANEL = 4; // a faceplate weighs more than another window
  var AVOID_WINDOW = 3;
  // The chart's chrome: title bar, value line and padding, for a first draw
  // before the layout has measured it.
  var CHROME = { w: 14, h: 92 };
  var PLOT_MARGIN = 74; // trends.js's left and right margins together

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function isNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function clamp(value, lo, hi) {
    return Math.min(hi, Math.max(lo, value));
  }

  /* ---- geometry ---------------------------------------------------------- */

  /* A window's {x, y, w, h} kept usable inside a workspace of `bounds`
   * {width, height}: no smaller than MIN_SIZE, no wider or taller than the
   * workspace, never off its left or top edge, and never so far right or down
   * that its title bar leaves it. */
  function clampGeometry(g, bounds) {
    var w = clamp(g.w, MIN_SIZE.w, Math.max(MIN_SIZE.w, bounds.width - 2 * EDGE));
    var h = clamp(g.h, MIN_SIZE.h, Math.max(MIN_SIZE.h, bounds.height - 2 * EDGE));
    return {
      x: clamp(g.x, EDGE, Math.max(EDGE, bounds.width - w - EDGE)),
      y: clamp(g.y, EDGE, Math.max(EDGE, bounds.height - BAR_REACH)),
      w: w,
      h: h,
    };
  }

  /* An arrow key on a title bar: moves the window, or with Shift resizes it;
   * Alt takes a fine step. Returns the new geometry (unclamped), or null for a
   * key that does neither. */
  function keyGeometry(g, key, shift, alt) {
    var step = alt ? FINE_STEP : KEY_STEP;
    var dx = { ArrowLeft: -step, ArrowRight: step }[key] || 0;
    var dy = { ArrowUp: -step, ArrowDown: step }[key] || 0;
    if (!dx && !dy) return null;
    if (shift) return { x: g.x, y: g.y, w: g.w + dx, h: g.h + dy };
    return { x: g.x + dx, y: g.y + dy, w: g.w, h: g.h };
  }

  /* Step `pos` down and right while another window's corner sits on it. */
  function cascade(pos, taken) {
    var p = { x: pos.x, y: pos.y };
    var guard = taken.length + 1;
    while (guard-- > 0 && taken.some(function (o) {
      return Math.abs(o.x - p.x) < 12 && Math.abs(o.y - p.y) < 12;
    })) {
      p = { x: p.x + CASCADE, y: p.y + CASCADE };
    }
    return p;
  }

  /* ---- values and units -------------------------------------------------- */

  /* A fraction (unit "fraction", 0..1) shows as a percent. */
  function displayUnit(unit) {
    return unit === "fraction" ? "%" : typeof unit === "string" ? unit : "";
  }

  function toDisplay(value, unit) {
    return isNumber(value) && unit === "fraction" ? value * 100 : value;
  }

  function displaySamples(samples, unit) {
    return (Array.isArray(samples) ? samples : []).map(function (pair) {
      return [pair[0], toDisplay(pair[1], unit)];
    });
  }

  function displayLimits(limits, unit) {
    if (!isObject(limits)) return null;
    var out = {};
    Object.keys(limits).forEach(function (key) {
      out[key] = toDisplay(limits[key], unit);
    });
    return out;
  }

  /* The value line: the number to one decimal and its unit apart. */
  function valueParts(value, unit) {
    var n = toDisplay(value, unit);
    if (!isNumber(n)) return { number: MISSING, unit: "" };
    return { number: (Math.abs(n) < 0.05 ? 0 : n).toFixed(1), unit: displayUnit(unit) };
  }

  /* ---- requests ---------------------------------------------------------- */

  /* One request for every shown window: `shown` is [{point, span, width}]
   * (width the chart's in pixels). It reaches back the widest span, and asks
   * for enough points that the narrowest span, sliced out of it, still has a
   * point per pixel - within the server's bound. `origin` is
   * static/js/clock.js's, so the request starts no earlier than the chart. */
  function fetchPlan(shown, simTime, maxPoints, origin) {
    var widest = Math.max.apply(null, shown.map(function (w) {
      return w.span;
    }));
    var wanted = Math.max.apply(null, shown.map(function (w) {
      return Math.max(1, w.width - PLOT_MARGIN) * (widest / w.span);
    }));
    var win = T.windowFor(simTime, widest, origin);
    var tags = shown.map(function (w) {
      return w.point;
    });
    return { tags: tags, window: win, url: T.buildTrendUrl(tags, win, T.pointsFor(wanted, maxPoints)) };
  }

  function sliceTo(samples, win) {
    return (Array.isArray(samples) ? samples : []).filter(function (pair) {
      return pair[0] >= win.from && pair[0] <= win.to;
    });
  }

  /* The alarms of one point, as trends.js's markers. An envelope alarm names
   * its tag and its descriptor (C6's data.pv); one that names its point
   * (data.point, T20-12) is matched by that. */
  function pointMarkers(entries, point, descriptor, win) {
    var cut = point.lastIndexOf(".");
    var tag = point.slice(0, cut);
    var mine = (Array.isArray(entries) ? entries : []).filter(function (entry) {
      var data = isObject(entry) && isObject(entry.data) ? entry.data : {};
      if (typeof data.point === "string") return data.point === point;
      return isObject(entry) && entry.tag === tag && typeof descriptor === "string" && data.pv === descriptor;
    });
    return T.markersFor(mine, win);
  }

  var api = {
    STORAGE_KEY: STORAGE_KEY,
    TICK_MS: TICK_MS,
    DEFAULT_SIZE: DEFAULT_SIZE,
    MIN_SIZE: MIN_SIZE,
    clampGeometry: clampGeometry,
    keyGeometry: keyGeometry,
    cascade: cascade,
    displayUnit: displayUnit,
    displaySamples: displaySamples,
    displayLimits: displayLimits,
    valueParts: valueParts,
    fetchPlan: fetchPlan,
    sliceTo: sliceTo,
    pointMarkers: pointMarkers,
  };

  /* ---- browser glue ---------------------------------------------------- */

  var TREND_ICON = '<svg viewBox="0 0 14 10" aria-hidden="true"><path d="M1 8 L5 3 L8 6 L13 1"/></svg>';

  function el(doc, name, attrs, text) {
    var node = doc.createElement(name);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function connected(node) {
    if (!node) return false;
    if (typeof node.isConnected === "boolean") return node.isConnected;
    return true;
  }

  function readStored(storage) {
    try {
      var parsed = JSON.parse(storage.getItem(STORAGE_KEY));
      return isObject(parsed) ? parsed : {};
    } catch (error) {
      return {};
    }
  }

  function defaultStorage() {
    try {
      return root.sessionStorage;
    } catch (error) {
      return null;
    }
  }

  /* Operate the trend windows. `options`:
   *   workspace  the element the windows float over (sizes and clamps them)
   *   layer      the element windows are appended to, inside the workspace
   *   tray       the element minimised windows sit in, inside the workspace
   *   obstacles  function returning [{rect, weight}] a new window stays clear
   *              of, in workspace coordinates (the open faceplate)
   *   onChange   called when a window opens, closes, minimises or restores
   *   origin     called at each draw for static/js/clock.js's origin, so the
   *              axis and markers read run time during a scenario
   *   fetch, storage, pollMs, timeoutMs   for a page or a test
   * Returns {open, close, minimise, restore, isOpen, obstacles, update,
   * refresh, reset, redraw, poll, stop}: call update with every snapshot, and
   * redraw when the origin changes. */
  function mount(options) {
    var settings = options || {};
    var workspace = settings.workspace;
    var layer = settings.layer;
    var tray = settings.tray;
    var doc = layer.ownerDocument || root.document;
    var doFetch = settings.fetch || root.fetch.bind(root);
    var storage = settings.storage === undefined ? defaultStorage() : settings.storage;
    var pollMs = settings.pollMs === undefined ? TICK_MS : settings.pollMs;
    var timeoutMs = settings.timeoutMs || FETCH_TIMEOUT_MS;
    var F = lib("Faceplates", "./faceplate.js");
    var G = lib("ProcessGraphic", "./graphic.js");
    var C = lib("Canvas", "./canvas.js");

    function origin() {
      return settings.origin ? settings.origin() : null;
    }

    var state = {
      listing: null, // {points, limits, descriptors, units, maxTags, maxPoints}
      data: {}, // point -> samples, as last fetched
      alarms: [],
      fetched: null, // the window the data was fetched for
      simTime: null,
      snapshot: null,
      problem: "",
    };
    var windows = {}; // point -> win
    var order = []; // points, in the order opened
    var z = 10;
    var ticket = 0;
    var polling = false;
    var timer = null;

    var trayStatus = el(doc, "p", { class: "tray-status", role: "status" });
    var trayChips = el(doc, "div", { class: "tray-chips" });
    tray.appendChild(trayChips);
    tray.appendChild(trayStatus);

    function bounds() {
      var r = workspace.getBoundingClientRect();
      return { width: r.width, height: r.height };
    }

    function changed() {
      if (settings.onChange) settings.onChange();
    }

    function nameOf(point) {
      return T.penName(point, state.listing ? state.listing.descriptors : null);
    }

    function unitOf(point) {
      return state.listing && isObject(state.listing.units) ? state.listing.units[point] : undefined;
    }

    function persist() {
      if (!storage) return;
      var stored = readStored(storage);
      Object.keys(windows).forEach(function (point) {
        var w = windows[point];
        stored[point] = { x: w.x, y: w.y, w: w.w, h: w.h, span: w.span };
      });
      try {
        storage.setItem(STORAGE_KEY, JSON.stringify(stored));
      } catch (error) {
        // Storage can be absent or full; the windows work without it.
      }
    }

    function shownPoints() {
      return order.filter(function (point) {
        return !windows[point].minimised;
      });
    }

    /* ---- one window ---- */

    function applyGeometry(win) {
      var g = clampGeometry(win, bounds());
      win.x = g.x;
      win.y = g.y;
      win.w = g.w;
      win.h = g.h;
      win.node.style.left = g.x + "px";
      win.node.style.top = g.y + "px";
      win.node.style.width = g.w + "px";
      win.node.style.height = g.h + "px";
    }

    function raise(win) {
      z += 1;
      win.node.style.zIndex = String(z);
      Object.keys(windows).forEach(function (point) {
        windows[point].node.setAttribute("data-active", windows[point] === win ? "true" : "false");
      });
    }

    function chartSize(win) {
      var chart = win.parts.chart;
      var w = chart.clientWidth || win.w - CHROME.w;
      var h = chart.clientHeight || win.h - CHROME.h;
      return { w: Math.max(160, Math.floor(w - 12)), h: Math.max(80, Math.floor(h - 12)) };
    }

    function liveValue(point) {
      var value = F ? F.pointValue(state.snapshot, point) : undefined;
      if (isNumber(value)) return value;
      var samples = state.data[point] || [];
      for (var i = samples.length - 1; i >= 0; i--) {
        if (isNumber(samples[i][1])) return samples[i][1];
      }
      return null;
    }

    function drawValue(win) {
      var parts = valueParts(liveValue(win.point), unitOf(win.point));
      var band = G ? G.pointBand(state.snapshot, win.point) : null;
      var value = win.parts.value;
      value.textContent = parts.number;
      if (parts.unit) value.appendChild(el(doc, "small", {}, parts.unit));
      value.setAttribute("data-band", band ? band.severity : "none");
      win.parts.band.textContent = band ? G.bandText(band) : "";
      if (band) win.parts.band.setAttribute("data-severity", band.severity);
      else win.parts.band.removeAttribute("data-severity");
    }

    function drawChart(win) {
      var chart = win.parts.chart;
      if (state.simTime === null || state.fetched === null || !(win.point in state.data)) {
        win.parts.status.textContent = state.problem || (state.simTime === null ? "Waiting for the plant." : "Loading the trend.");
        chart.textContent = "";
        return;
      }
      win.parts.status.textContent = state.problem;

      var point = win.point;
      var unit = unitOf(point);
      var range = T.windowFor(state.fetched.to, win.span, origin());
      var samples = displaySamples(sliceTo(state.data[point], range), unit);
      var limits = displayLimits(state.listing && state.listing.limits[point], unit);
      win.hold = T.holdScale(win.hold, samples, limits);
      var descriptor = state.listing ? state.listing.descriptors[point] : undefined;
      var size = chartSize(win);
      var shownUnit = displayUnit(unit);
      var held = {};
      var bands = {};
      held[point] = win.hold;
      bands[point] = limits;

      chart.innerHTML = T.renderChart({
        width: size.w,
        height: size.h,
        window: range,
        pens: [{ point: point, samples: samples }],
        limits: bands,
        focus: point,
        markers: pointMarkers(state.alarms, point, descriptor, range),
        scales: held,
        origin: origin(),
        label: "Trend of " + nameOf(point) + (shownUnit ? " in " + shownUnit : "") + ", the last " + win.span +
          " minute" + (win.span > 1 ? "s" : ""),
      });
    }

    /* A window opened before the listing arrived is named by its point id
     * until it does. */
    function drawName(win) {
      var name = nameOf(win.point);
      if (win.parts.name.textContent === name) return;
      win.parts.name.textContent = name;
      win.node.setAttribute("aria-label", "Trend of " + name);
      win.parts.bar.setAttribute("aria-label", "Trend of " + name + ". Drag, or use the arrow keys to move and Shift with the arrow keys to resize.");
      win.parts.minimise.setAttribute("aria-label", "Minimise trend of " + name);
      win.parts.close.setAttribute("aria-label", "Close trend of " + name);
    }

    function draw(win) {
      drawName(win);
      if (win.minimised) return;
      drawValue(win);
      drawChart(win);
    }

    function setSpan(win, span) {
      if (span === win.span) return;
      win.span = span;
      win.hold = null;
      win.parts.spans.forEach(function (button) {
        button.setAttribute("aria-pressed", String(Number(button.getAttribute("data-span")) === span));
      });
      persist();
      draw(win);
      // A wider span than any fetched needs history not yet here.
      if (state.fetched === null || state.fetched.to - state.fetched.from < span * 60) refresh();
    }

    function drag(handle, win, mode) {
      handle.addEventListener("pointerdown", function (event) {
        if (event.button !== 0 || (event.target && event.target.closest && event.target.closest("button"))) return;
        event.preventDefault();
        if (handle.setPointerCapture && event.pointerId !== undefined) handle.setPointerCapture(event.pointerId);
        var start = { px: event.clientX, py: event.clientY, x: win.x, y: win.y, w: win.w, h: win.h };
        function move(ev) {
          var dx = ev.clientX - start.px;
          var dy = ev.clientY - start.py;
          if (mode === "move") {
            win.x = start.x + dx;
            win.y = start.y + dy;
          } else {
            win.w = start.w + dx;
            win.h = start.h + dy;
          }
          applyGeometry(win);
        }
        function up() {
          handle.removeEventListener("pointermove", move);
          handle.removeEventListener("pointerup", up);
          handle.removeEventListener("pointercancel", up);
          draw(win);
          persist();
        }
        handle.addEventListener("pointermove", move);
        handle.addEventListener("pointerup", up);
        handle.addEventListener("pointercancel", up);
      });
    }

    function build(win) {
      var node = el(doc, "section", { class: "trend-window", role: "dialog", "data-point": win.point });
      var bar = el(doc, "div", { class: "tw-bar", tabindex: "0" });
      var title = el(doc, "span", { class: "tw-name" });
      var minimise = el(doc, "button", { type: "button", class: "tw-ctl", "data-tw": "minimise", title: "Minimise" }, "\u2212");
      var close_ = el(doc, "button", { type: "button", class: "tw-ctl", "data-tw": "close", title: "Close" }, "✕");
      bar.appendChild(title);
      bar.appendChild(minimise);
      bar.appendChild(close_);
      node.appendChild(bar);

      var meta = el(doc, "div", { class: "tw-meta" });
      var reading = el(doc, "p", { class: "tw-reading", "data-live-value": "" });
      var value = el(doc, "span", { class: "tw-value" }, MISSING);
      var band = el(doc, "span", { class: "tw-band" });
      reading.appendChild(value);
      reading.appendChild(band);
      meta.appendChild(reading);
      var group = el(doc, "div", { class: "tw-spans", role: "group", "aria-label": "Time range" });
      var spans = T.SPANS.map(function (minutes) {
        var button = el(doc, "button", {
          type: "button",
          "data-span": String(minutes),
          "aria-pressed": String(minutes === win.span),
          "aria-label": minutes + " minute" + (minutes > 1 ? "s" : ""),
        }, minutes + "m");
        button.addEventListener("click", function () {
          setSpan(win, minutes);
        });
        group.appendChild(button);
        return button;
      });
      meta.appendChild(group);
      node.appendChild(meta);

      var chart = el(doc, "div", { class: "tw-chart", "data-live-value": "" });
      node.appendChild(chart);
      var status = el(doc, "p", { class: "tw-status", role: "status" });
      node.appendChild(status);
      var grip = el(doc, "div", { class: "tw-resize", "aria-hidden": "true" });
      node.appendChild(grip);

      minimise.addEventListener("click", function () {
        minimiseWindow(win.point);
      });
      close_.addEventListener("click", function () {
        closeWindow(win.point, true);
      });
      node.addEventListener("pointerdown", function () {
        raise(win);
      });
      bar.addEventListener("keydown", function (event) {
        // An arrow on the Minimise or Close button is not a move.
        if (event.target !== bar) return;
        var next = keyGeometry(win, event.key, event.shiftKey, event.altKey);
        if (!next) return;
        event.preventDefault();
        win.x = next.x;
        win.y = next.y;
        win.w = next.w;
        win.h = next.h;
        applyGeometry(win);
        draw(win);
        persist();
      });
      drag(bar, win, "move");
      drag(grip, win, "resize");
      if (typeof root.ResizeObserver === "function") {
        win.observer = new root.ResizeObserver(function () {
          draw(win);
        });
        win.observer.observe(chart);
      }

      win.node = node;
      win.parts = {
        bar: bar,
        name: title,
        minimise: minimise,
        close: close_,
        value: value,
        band: band,
        chart: chart,
        status: status,
        spans: spans,
      };
      drawName(win);
    }

    /* ---- the tray ---- */

    var trayKey = null;

    function chipText(point) {
      var parts = valueParts(liveValue(point), unitOf(point));
      return nameOf(point) + " " + parts.number + (parts.unit === "%" ? "%" : parts.unit ? " " + parts.unit : "");
    }

    function drawTray() {
      var minimised = order.filter(function (point) {
        return windows[point].minimised;
      });
      var key = minimised.join("\n");
      if (key !== trayKey) {
        trayKey = key;
        trayChips.textContent = "";
        minimised.forEach(function (point) {
          var chip = el(doc, "button", { type: "button", class: "tray-chip", "data-point": point });
          chip.innerHTML = TREND_ICON;
          chip.appendChild(el(doc, "span", { class: "tray-text", "data-live-value": "" }));
          chip.addEventListener("click", function () {
            restoreWindow(point);
          });
          trayChips.appendChild(chip);
        });
      }
      trayChips.querySelectorAll(".tray-chip").forEach(function (chip) {
        var point = chip.getAttribute("data-point");
        chip.setAttribute("aria-label", "Restore trend of " + nameOf(point));
        chip.querySelector(".tray-text").textContent = chipText(point);
      });
      if (minimised.length) tray.removeAttribute("hidden");
      else if (!trayStatus.textContent) tray.setAttribute("hidden", "");
    }

    function say(text) {
      trayStatus.textContent = text;
      if (text) tray.removeAttribute("hidden");
      drawTray();
    }

    /* ---- opening and closing ---- */

    function avoidFor(except) {
      var avoid = (settings.obstacles ? settings.obstacles() : []).map(function (item) {
        return { rect: item.rect, weight: item.weight || AVOID_PANEL };
      });
      return avoid.concat(windowRects(except));
    }

    function windowRects(except) {
      return shownPoints()
        .filter(function (point) {
          return point !== except;
        })
        .map(function (point) {
          var w = windows[point];
          return { rect: { left: w.x, top: w.y, right: w.x + w.w, bottom: w.y + w.h }, weight: AVOID_WINDOW };
        });
    }

    /* Open the trend of `point`, or raise its window when it is open.
     * `opener` is where focus goes back to on close, and `anchor` (a rect in
     * workspace coordinates) what a new window sits beside. Returns false when
     * the pen bound is reached and nothing opened. */
    function open(point, opener, anchor) {
      var existing = windows[point];
      if (existing) {
        if (opener) existing.opener = opener;
        if (existing.minimised) restoreWindow(point);
        else {
          raise(existing);
          existing.parts.bar.focus();
        }
        return true;
      }
      var maxTags = state.listing ? state.listing.maxTags : 8;
      if (order.length >= maxTags) {
        say("At most " + maxTags + " trends can be open. Close one to open another.");
        return false;
      }
      say("");

      var stored = storage ? readStored(storage)[point] : null;
      var room = bounds();
      var win = { point: point, span: T.DEFAULT_SPAN, minimised: false, hold: null, opener: opener || null };
      if (isObject(stored) && isNumber(stored.x) && isNumber(stored.y) && isNumber(stored.w) && isNumber(stored.h)) {
        win.x = stored.x;
        win.y = stored.y;
        win.w = stored.w;
        win.h = stored.h;
        win.span = T.sanitizeSpan(stored.span);
      } else {
        win.w = Math.min(DEFAULT_SIZE.w, room.width - 2 * EDGE);
        win.h = Math.min(DEFAULT_SIZE.h, room.height - 2 * EDGE);
        var pos = C.place({
          size: { w: win.w, h: win.h },
          bounds: room,
          anchor: anchor || null,
          avoid: avoidFor(null),
          others: [],
        });
        pos = cascade(pos, order.map(function (p) {
          return windows[p];
        }));
        win.x = pos.x;
        win.y = pos.y;
      }

      build(win);
      windows[point] = win;
      order.push(point);
      layer.appendChild(win.node);
      applyGeometry(win);
      raise(win);
      draw(win);
      persist();
      win.parts.bar.focus();
      changed();
      refresh();
      return true;
    }

    function closeWindow(point, restoreFocus) {
      var win = windows[point];
      if (!win) return;
      persist();
      if (win.observer) win.observer.disconnect();
      if (win.node.parentNode) win.node.parentNode.removeChild(win.node);
      delete windows[point];
      order = order.filter(function (p) {
        return p !== point;
      });
      say("");
      if (restoreFocus && connected(win.opener) && typeof win.opener.focus === "function") win.opener.focus();
      changed();
    }

    function minimiseWindow(point) {
      var win = windows[point];
      if (!win || win.minimised) return;
      win.minimised = true;
      win.node.setAttribute("hidden", "");
      drawTray();
      var chip = trayChips.querySelector('[data-point="' + point + '"]');
      if (chip) chip.focus();
      changed();
    }

    function restoreWindow(point) {
      var win = windows[point];
      if (!win) return;
      if (win.minimised) {
        win.minimised = false;
        win.node.removeAttribute("hidden");
        applyGeometry(win);
        drawTray();
        draw(win);
        changed();
        // Its history was not fetched while it sat in the tray.
        refresh();
      }
      raise(win);
      win.parts.bar.focus();
    }

    /* ---- fetching ---- */

    async function get(url) {
      var controller = typeof AbortController === "function" ? new AbortController() : null;
      var deadlineTimer;
      var deadline = new Promise(function (resolve, reject) {
        deadlineTimer = root.setTimeout(function () {
          if (controller) controller.abort();
          reject(new Error("timed out"));
        }, timeoutMs);
      });

      try {
        var response = await Promise.race([doFetch(url, controller ? { signal: controller.signal } : undefined), deadline]);
        if (!response.ok) throw new Error("HTTP " + response.status);
        return await Promise.race([response.json(), deadline]);
      } finally {
        root.clearTimeout(deadlineTimer);
      }
    }

    function listingOf(body) {
      var l = isObject(body) ? body : {};
      return {
        points: Array.isArray(l.points) ? l.points : [],
        limits: isObject(l.limits) ? l.limits : {},
        descriptors: isObject(l.descriptors) ? l.descriptors : {},
        units: isObject(l.units) ? l.units : {},
        maxTags: isNumber(l.max_tags) ? l.max_tags : 8,
        maxPoints: isNumber(l.max_points) ? l.max_points : null,
      };
    }

    /* Refetch the listing, the shown windows' history and the alarm record,
     * and redraw every shown window from them. A window whose point the plant
     * no longer publishes (another plant was loaded) closes. A newer refresh
     * supersedes an older one still in flight. */
    async function refresh() {
      var mine = ++ticket;
      try {
        var listing = listingOf(await get(T.POINTS_URL));
        if (mine !== ticket) return;
        state.listing = listing;
        order.slice().forEach(function (point) {
          if (listing.points.indexOf(point) < 0) closeWindow(point, false);
        });

        var shown = shownPoints();
        if (state.simTime !== null && shown.length) {
          var plan = fetchPlan(shown.map(function (point) {
            return { point: point, span: windows[point].span, width: chartSize(windows[point]).w };
          }), state.simTime, listing.maxPoints, origin());
          var results = await Promise.all([get(plan.url), get(T.ALARMS_URL)]);
          if (mine !== ticket) return;
          // Simulated time that went back is another history: hold nothing over.
          if (state.fetched !== null && plan.window.to < state.fetched.to) {
            order.forEach(function (point) {
              windows[point].hold = null;
            });
          }
          state.data = isObject(results[0]) ? results[0] : {};
          state.alarms = Array.isArray(results[1]) ? results[1] : [];
          state.fetched = plan.window;
        }
        state.problem = "";
      } catch (error) {
        if (mine !== ticket) return;
        state.problem = "Trend unavailable: " + error.message;
      }
      order.forEach(function (point) {
        draw(windows[point]);
      });
      drawTray();
    }

    /* One timer tick: a refresh, unless no window is shown, the page is in a
     * hidden tab, or the last tick is still waiting on a slow server. */
    async function poll() {
      if (!shownPoints().length || (root.document && root.document.hidden) || polling) return;
      polling = true;
      try {
        await refresh();
      } finally {
        polling = false;
      }
    }

    /* Called with every snapshot: the live values follow it at once, the
     * charts on the next tick. */
    function update(snapshot) {
      if (!isObject(snapshot) || !isNumber(snapshot.sim_time)) return;
      var first = state.simTime === null;
      state.simTime = snapshot.sim_time;
      state.snapshot = snapshot;
      shownPoints().forEach(function (point) {
        drawValue(windows[point]);
      });
      drawTray();
      if (first && order.length) return refresh();
    }

    // A smaller browser window pulls every shown window back inside it.
    function relayout() {
      shownPoints().forEach(function (point) {
        applyGeometry(windows[point]);
        draw(windows[point]);
      });
    }

    if (pollMs) timer = root.setInterval(poll, pollMs);
    var listening = pollMs && root.document && root.document.addEventListener;
    if (listening) root.document.addEventListener("visibilitychange", poll);
    if (root.addEventListener) root.addEventListener("resize", relayout);
    tray.setAttribute("hidden", "");

    return {
      open: open,
      close: function (point) {
        closeWindow(point, true);
      },
      minimise: minimiseWindow,
      restore: restoreWindow,
      isOpen: function (point) {
        return Object.prototype.hasOwnProperty.call(windows, point);
      },
      obstacles: function () {
        return windowRects(null);
      },
      update: update,
      refresh: refresh,
      // The shown plant or its history may have changed (a scenario load,
      // unload or abort): hold no scale over from the old one.
      reset: function () {
        order.forEach(function (point) {
          windows[point].hold = null;
        });
        return refresh();
      },
      redraw: function () {
        order.forEach(function (point) {
          draw(windows[point]);
        });
      },
      poll: poll,
      stop: function () {
        if (timer !== null) root.clearInterval(timer);
        if (listening) root.document.removeEventListener("visibilitychange", poll);
        if (root.removeEventListener) root.removeEventListener("resize", relayout);
      },
    };
  }

  api.mount = mount;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.TrendWindows = api;
})(typeof window !== "undefined" ? window : globalThis);
