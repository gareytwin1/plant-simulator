/* Stale connection handling (T16-5).
 *
 * The console reads the plant over the SSE stream in app/api/stream.py. That
 * stream pushes a full snapshot every interval even while the plant is paused,
 * so silence is never normal: a number on screen that the stream has stopped
 * refreshing must say so, or the operator keeps trusting it.
 *
 * Four states, derived only from what the stream delivers:
 *   connecting  nothing received yet
 *   live        a snapshot arrived within the stale bound
 *   stale       no snapshot within the bound, or the stream errored
 *   closed      the browser gave up on the stream (a 204 from the server, or an
 *               HTTP error or wrong content type); it will not reconnect
 * `stale` and `closed` both de-emphasise the console; the next snapshot clears
 * `stale`, never `closed`: only a page reload recovers from it, and the label
 * says so, since a clean session end and a broken endpoint look alike here.
 *
 * Everything above the "browser glue" marker is pure (a clock value in, a state
 * out) so it runs under Node without a DOM; tests/test_stale_connection.py
 * drives it that way. De-emphasis is static/css/connection.css, driven by the
 * data-connection attribute this file sets; colour comes from tokens.css.
 */
(function (root) {
  "use strict";

  var STREAM_URL = "/api/stream";

  // A stale verdict waits this many push intervals, floored so one slow tick
  // or network hiccup does not flicker the console. Deliberately tighter than
  // the server's own dropout budget (app.api.stream.DROPOUT_INTERVALS is 5,
  // same 2s floor): the operator should see a freeze before the server gives up
  // on a wedged client. tests/test_stale_connection.py pins the ordering.
  var STALE_INTERVALS = 3;
  var MIN_STALE_MS = 2000;

  // EventSource.readyState value for a connection that will not reconnect.
  var READY_CLOSED = 2;

  var LABEL = {
    connecting: "CONNECTING",
    live: "LIVE",
    stale: "STALE - DATA NOT UPDATING",
    closed: "DISCONNECTED - RELOAD TO RECONNECT",
  };

  function staleAfterMs(intervalSeconds) {
    return Math.max(intervalSeconds * 1000 * STALE_INTERVALS, MIN_STALE_MS);
  }

  /* The connection's state machine. `now` is a millisecond clock reading, passed
   * in on every call so the machine never reads a clock itself. `status(now)`
   * is the only way to ask: staleness is a function of time, so it is computed
   * on query, not stored, and a throttled timer cannot leave it behind. */
  function createMonitor(staleMs) {
    var lastMessageAt = null;
    var errored = false;
    var closed = false;

    return {
      message: function (now) {
        if (closed) return;
        lastMessageAt = now;
        errored = false;
      },
      error: function (permanent) {
        if (permanent) closed = true;
        errored = true;
      },
      status: function (now) {
        if (closed) return "closed";
        if (lastMessageAt === null && !errored) return "connecting";
        if (errored) return "stale";
        return now - lastMessageAt > staleMs ? "stale" : "live";
      },
    };
  }

  var api = {
    STREAM_URL: STREAM_URL,
    LABEL: LABEL,
    staleAfterMs: staleAfterMs,
    createMonitor: createMonitor,
  };

  /* ---- browser glue ---------------------------------------------------- */

  /* Watch the stream and mirror its state onto the page. `consoleEl` gets a
   * data-connection attribute (CSS de-emphasises values under stale and
   * closed); `indicatorEl`, when given, gets the label as text. Options, all
   * for a page or test supplying its own: EventSource, now, setInterval,
   * clearInterval, onSnapshot(snapshot), onStatus(status). `intervalSeconds` is
   * required: it is the server's push interval, and a guessed default would
   * read a healthy slower stream as stale. Returns {status, stop}. */
  function mount(consoleEl, indicatorEl, options) {
    var settings = options || {};
    var EventSourceImpl = settings.EventSource || root.EventSource;
    var now = settings.now || Date.now;
    var setTimer = settings.setInterval || root.setInterval.bind(root);
    var clearTimer = settings.clearInterval || root.clearInterval.bind(root);
    if (!(settings.intervalSeconds > 0)) {
      throw new Error("connection.mount needs intervalSeconds, the server's push interval");
    }
    var staleMs = staleAfterMs(settings.intervalSeconds);
    var monitor = createMonitor(staleMs);
    var shown = null;

    function render() {
      var current = monitor.status(now());
      if (current === shown) return;
      shown = current;
      consoleEl.setAttribute("data-connection", current);
      if (indicatorEl) indicatorEl.textContent = LABEL[current];
      if (settings.onStatus) settings.onStatus(current);
    }

    var source = new EventSourceImpl(settings.url || STREAM_URL);

    source.onmessage = function (event) {
      var snapshot;
      try {
        snapshot = JSON.parse(event.data);
      } catch (failure) {
        // An unparseable event is not a live reading; leave the clock alone.
        return;
      }
      monitor.message(now());
      render();
      if (settings.onSnapshot) settings.onSnapshot(snapshot);
    };

    source.onerror = function () {
      // CLOSED means the browser will not reconnect: a 204, but also an HTTP
      // error or wrong content type. Any other error is a reconnect in progress.
      monitor.error(source.readyState === READY_CLOSED);
      render();
    };

    // Re-evaluate on a timer: a stream that goes quiet raises no event at all.
    var timer = setTimer(render, Math.max(250, Math.floor(staleMs / 4)));
    render();

    return {
      status: function () {
        return monitor.status(now());
      },
      stop: function () {
        clearTimer(timer);
        source.close();
      },
    };
  }

  api.mount = mount;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ConnectionMonitor = api;
})(typeof window !== "undefined" ? window : globalThis);
