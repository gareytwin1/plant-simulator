/* Scenario run controls (T16-15): the console's Scenario bar.
 *
 * Start run and Abort run for the scenario this session has loaded, and the
 * phase it is in, following the server without a reload:
 *   POST /api/scenario/start
 *   POST /api/scenario/abort
 *   GET  /api/scenario/result   (409 = nothing loaded = free play)
 *
 * Only `phase` and `title` are ever read from a response body, so a live run's
 * key and a finished run's debrief are never displayed here (T16-12). A title
 * is the scenario's public name, the one the landing page lists. A refusal is
 * shown in this file's own plain words, chosen by status code, never the
 * server's text. The server renders the title with the page; a poll replaces
 * it when another tab loads a different scenario.
 *
 * Abort takes two clicks (arm, then confirm) with no window.confirm, so it
 * runs under Node. Aborting reveals the debrief, so it is offered only while a
 * run is running; a loaded trainee leaves through the landing page.
 *
 * Everything above the "browser glue" marker is pure so it runs under Node
 * without a DOM; tests/test_scenario_controls.py drives it that way.
 */
(function (root) {
  "use strict";

  var START_URL = "/api/scenario/start";
  var ABORT_URL = "/api/scenario/abort";
  var RESULT_URL = "/api/scenario/result";
  var PHASES = ["idle", "loaded", "running", "complete", "aborted"];
  var FETCH_TIMEOUT_MS = 5000;

  var api = {};

  api.refusalText = function (action, status) {
    if (status === 409) {
      if (action === "start") {
        return "That scenario is no longer ready to start. It may have been started, finished or unloaded from another tab.";
      }
      return "That scenario is no longer running, so there is nothing to abort.";
    }
    if (status === 429) return "Too many requests. Wait a moment and try again.";
    // The request may have reached the server before it failed.
    if (status === 0 || status >= 500) return "The request did not finish. The status shown may be out of date, so check it before trying again.";
    return "Something went wrong, so nothing was changed. Try again.";
  };

  api.buildStartRequest = function () {
    return { url: START_URL, init: { method: "POST" } };
  };

  api.buildAbortRequest = function () {
    return { url: ABORT_URL, init: { method: "POST" } };
  };

  api.buildResultRequest = function () {
    return { url: RESULT_URL, init: undefined };
  };

  /* The phase a response puts the run in, or null when it says nothing (a
   * failure, or a body with no known phase). A 409 on the result is free play. */
  api.phaseOf = function (status, body) {
    if (status === 409) return "idle";
    if (status !== 200 || body === null || typeof body !== "object") return null;
    return PHASES.indexOf(body.phase) === -1 ? null : body.phase;
  };

  /* The scenario title a response carries, or null when it carries none. */
  api.titleOf = function (status, body) {
    if (status !== 200 || body === null || typeof body !== "object") return null;
    return typeof body.title === "string" ? body.title : null;
  };

  /* What the bar offers in a phase. `armed` is an abort waiting on its second
   * click and only means something while running. */
  api.controlsFor = function (phase, armed) {
    var running = phase === "running";
    return {
      start: phase === "loaded",
      abort: running && !armed,
      confirm: running && armed,
      keep: running && armed,
      choose: running ? "" : phase === "loaded" ? "Choose another scenario" : "Choose a scenario",
    };
  };

  /* `labels` is the server's phase-to-text map, sent with the page so the text
   * has one source. */
  api.phaseLabel = function (labels, phase) {
    return Object.prototype.hasOwnProperty.call(labels, phase) ? labels[phase] : "";
  };

  api.modeText = function (phase, title) {
    if (phase === "idle") return "Free play";
    return title ? "Scenario: " + title : "Scenario";
  };

  /* browser glue */

  /* Mount the bar. `options.fetch` and `options.pollMs` exist so a page or
   * test can supply its own transport and timer. `options.onChange`, when
   * given, is called after a response moves the phase or the title, which is
   * when the plant the session shows may have changed (T20-2). Returns
   * {poll, stop, state}. */
  function mount(doc, options) {
    var settings = options || {};
    var doFetch = settings.fetch || root.fetch.bind(root);
    var bar = doc.getElementById("scenario-bar");
    var pollMs = settings.pollMs === undefined ? Number(bar.getAttribute("data-interval-seconds")) * 1000 : settings.pollMs;
    var labels = JSON.parse(bar.getAttribute("data-phase-labels"));
    var els = {
      mode: doc.getElementById("scenario-mode"),
      phase: doc.getElementById("scenario-phase"),
      notice: doc.getElementById("scenario-notice"),
      start: doc.getElementById("scenario-start"),
      abort: doc.getElementById("scenario-abort"),
      confirm: doc.getElementById("scenario-abort-confirm"),
      keep: doc.getElementById("scenario-abort-keep"),
      choose: doc.getElementById("scenario-choose"),
    };
    var actionButtons = [els.start, els.abort, els.confirm, els.keep];
    var state = {
      phase: api.phaseOf(200, { phase: bar.getAttribute("data-phase") }) || "idle",
      title: bar.getAttribute("data-title") || "",
      armed: false,
      busy: false,
    };
    var polling = false;
    var pollPromise = null;
    // Bumped by every click that sends, so a poll that began earlier is stale.
    var epoch = 0;
    var timer = null;

    function render() {
      var controls = api.controlsFor(state.phase, state.armed);

      els.mode.textContent = api.modeText(state.phase, state.title);
      els.phase.textContent = api.phaseLabel(labels, state.phase);
      els.start.hidden = !controls.start;
      els.abort.hidden = !controls.abort;
      els.confirm.hidden = !controls.confirm;
      els.keep.hidden = !controls.keep;
      els.choose.hidden = controls.choose === "";
      els.choose.textContent = controls.choose;
      actionButtons.forEach(function (button) {
        button.disabled = state.busy;
      });
      bar.setAttribute("data-phase", state.phase);
    }

    function notice(text) {
      els.notice.textContent = text;
      els.notice.hidden = text === "";
    }

    function apply(status, body) {
      var phase = api.phaseOf(status, body);
      var title = api.titleOf(status, body);
      var before = state.phase + "\n" + state.title;

      if (title !== null) state.title = title;
      if (phase !== null && phase !== state.phase) {
        state.phase = phase;
        if (phase === "idle") state.title = "";
        if (phase !== "running") state.armed = false;
      }
      if (settings.onChange && state.phase + "\n" + state.title !== before) settings.onChange();
    }

    /* A request that never settles would hold a flag for good, so each one is
     * given a deadline and aborted where the transport allows. */
    async function get(request) {
      var controller = typeof AbortController === "function" ? new AbortController() : null;
      var timerId;
      var deadline = new Promise(function (resolve, reject) {
        timerId = root.setTimeout(function () {
          if (controller) controller.abort();
          reject(new Error("timed out"));
        }, FETCH_TIMEOUT_MS);
      });
      var init = Object.assign({}, request.init || {});
      if (controller) init.signal = controller.signal;

      try {
        var response = await Promise.race([doFetch(request.url, init), deadline]);
        var body = null;
        if (response.ok) body = await Promise.race([response.json(), deadline]);
        return { status: response.status, body: body };
      } finally {
        root.clearTimeout(timerId);
      }
    }

    async function pollOnce() {
      var started = epoch;
      try {
        var result = await get(api.buildResultRequest());
        // A click that sent while this was in flight knows better.
        if (started === epoch) apply(result.status, result.body);
      } catch (error) {
        // A failed poll leaves the bar as it was; the next tick tries again.
      } finally {
        polling = false;
        pollPromise = null;
        render();
      }
    }

    /* The unguarded read: the timer's guard is about not wasting a request on
     * a hidden tab, and a click that needs the truth is not that. */
    function readNow() {
      if (polling || state.busy) return Promise.resolve();
      polling = true;
      pollPromise = pollOnce();
      return pollPromise;
    }

    function poll() {
      if (root.document && root.document.hidden) return Promise.resolve();
      return readNow();
    }

    async function send(action, request) {
      state.busy = true;
      state.armed = false;
      epoch += 1;
      render();
      var resync = false;
      try {
        var result = await get(request);
        if (result.status === 200) {
          notice("");
          apply(200, result.body);
          resync = api.phaseOf(200, result.body) === null;
        } else {
          notice(api.refusalText(action, result.status));
          resync = result.status !== 429;
        }
      } catch (error) {
        notice(api.refusalText(action, 0));
        resync = true;
      } finally {
        state.busy = false;
        render();
      }
      if (resync) {
        // A poll already in flight began before this refusal; wait it out and
        // ask again, or the bar stays stale for a whole interval.
        if (pollPromise) await pollPromise;
        await readNow();
      }
    }

    els.start.addEventListener("click", function () {
      return send("start", api.buildStartRequest());
    });
    els.abort.addEventListener("click", function () {
      state.armed = true;
      notice("");
      render();
    });
    els.keep.addEventListener("click", function () {
      state.armed = false;
      render();
    });
    els.confirm.addEventListener("click", function () {
      return send("abort", api.buildAbortRequest());
    });

    render();
    if (pollMs) timer = root.setInterval(poll, pollMs);

    // A tab that comes back to the front catches up at once, not on the next tick.
    var listening = pollMs && root.document && root.document.addEventListener;
    if (listening) root.document.addEventListener("visibilitychange", poll);

    return {
      poll: poll,
      state: state,
      stop: function () {
        if (timer !== null) root.clearInterval(timer);
        if (listening) root.document.removeEventListener("visibilitychange", poll);
      },
    };
  }

  api.mount = mount;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ScenarioBar = api;
})(typeof window !== "undefined" ? window : globalThis);
