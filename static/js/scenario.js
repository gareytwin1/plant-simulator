/* Scenario run controls (T16-15), shown in the ribbon (T20-8).
 *
 * Start run and Abort run for the scenario this session has loaded, and the
 * phase it is in, following the server without a reload:
 *   POST /api/scenario/start
 *   POST /api/scenario/abort
 *   GET  /api/scenario/result   (409 = nothing loaded = free play)
 *
 * Only `phase` and `title` are ever read from a response body here, so a live
 * run's key and a finished run's debrief are never displayed (T16-12); a
 * caller's `onResult` sees the body, and static/js/ribbon.js reads only the
 * run's times from it. A title is the scenario's public name, the one the
 * landing page lists. A refusal is shown in this file's own plain words,
 * chosen by status code, never the server's text. The server renders the
 * title with the page; a poll replaces it when another tab loads a different
 * scenario.
 *
 * Abort takes two clicks (arm, then confirm) with no window.confirm, so it
 * runs under Node; Escape disarms it. Aborting reveals the debrief, so it is
 * offered only while a run is running; a loaded trainee leaves through the
 * landing page. Start and Abort are on the console only: the landing page
 * shows the run without them, since it never starts the plant's clock.
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

  /* The ribbon's two lines: the run's name, then what it is doing. */
  api.runTitle = function (phase, title) {
    if (phase === "idle") return "Free play";
    return title || "Scenario";
  };

  /* `running` is whether the plant's clock advances: the landing page never
   * starts it, so a session that has not opened the console has not. */
  api.runState = function (labels, phase, running) {
    if (phase === "idle") return running ? "Plant running" : "Plant not started";
    return "Scenario \u00B7 " + api.phaseLabel(labels, phase);
  };

  /* browser glue */

  /* Mount the run controls. `options.fetch` and `options.pollMs` exist so a
   * page or test can supply its own transport and timer. `options.onChange`,
   * when given, is called after a response moves the phase or the title, which
   * is when the plant the session shows may have changed (T20-2).
   * `options.onResult(status, body)` sees every response the bar applies. The
   * buttons and the choose link are optional, so a page can show the run
   * without offering them. Returns {poll, stop, state}. */
  function mount(doc, options) {
    var settings = options || {};
    var doFetch = settings.fetch || root.fetch.bind(root);
    var bar = doc.getElementById("scenario-bar");
    var pollMs = settings.pollMs === undefined ? Number(bar.getAttribute("data-interval-seconds")) * 1000 : settings.pollMs;
    var labels = JSON.parse(bar.getAttribute("data-phase-labels"));
    var running = bar.getAttribute("data-plant-running") !== "false";
    var els = {
      title: doc.getElementById("scenario-title"),
      state: doc.getElementById("scenario-state"),
      notice: doc.getElementById("scenario-notice"),
      start: doc.getElementById("scenario-start"),
      abort: doc.getElementById("scenario-abort"),
      confirmGroup: doc.getElementById("scenario-confirm"),
      confirm: doc.getElementById("scenario-abort-confirm"),
      keep: doc.getElementById("scenario-abort-keep"),
      choose: doc.getElementById("scenario-choose"),
      chooseLabel: doc.getElementById("scenario-choose-label"),
    };
    var actionButtons = [els.start, els.abort, els.confirm, els.keep].filter(Boolean);
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

    function show(el, visible) {
      if (el) el.hidden = !visible;
    }

    function render() {
      var controls = api.controlsFor(state.phase, state.armed);

      els.title.textContent = api.runTitle(state.phase, state.title);
      els.title.setAttribute("title", api.modeText(state.phase, state.title));
      els.state.textContent = api.runState(labels, state.phase, running);
      show(els.start, controls.start);
      show(els.abort, controls.abort);
      show(els.confirmGroup, controls.confirm);
      show(els.confirm, controls.confirm);
      show(els.keep, controls.keep);
      if (els.choose) {
        els.choose.hidden = controls.choose === "";
        els.choose.setAttribute("aria-label", controls.choose);
        (els.chooseLabel || els.choose).textContent = controls.choose;
      }
      actionButtons.forEach(function (button) {
        button.disabled = state.busy;
      });
      bar.setAttribute("data-phase", state.phase);
    }

    /* Keyboard focus follows the controls, so it is never left on one that
     * has just been hidden or disabled. It moves only when it was already in
     * the bar, so a background poll never takes it from elsewhere. */
    function focus(el) {
      if (el && typeof el.focus === "function") el.focus();
    }

    var controls = [els.start, els.abort, els.confirm, els.keep, els.choose].filter(Boolean);

    function holdsFocus(el) {
      return controls.indexOf(el) !== -1;
    }

    function refocus(owned) {
      var active = doc.activeElement;
      if (!owned || (holdsFocus(active) && !active.hidden)) return;
      focus([els.abort, els.keep, els.start, els.choose].filter(function (el) {
        return el && !el.hidden;
      })[0]);
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
      if (settings.onResult) settings.onResult(status, body);
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
      var owned;
      try {
        var result = await get(api.buildResultRequest());
        // A click that sent while this was in flight knows better.
        if (started === epoch) apply(result.status, result.body);
      } catch (error) {
        // A failed poll leaves the bar as it was; the next tick tries again.
      } finally {
        polling = false;
        pollPromise = null;
        owned = holdsFocus(doc.activeElement);
        render();
        refocus(owned);
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
      // Disabling the clicked button can drop focus to the body, so note now.
      var owned = holdsFocus(doc.activeElement);
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
        refocus(owned);
      }
      if (resync) {
        // A poll already in flight began before this refusal; wait it out and
        // ask again, or the bar stays stale for a whole interval.
        if (pollPromise) await pollPromise;
        await readNow();
      }
    }

    function disarm() {
      state.armed = false;
      render();
      focus(els.abort);
    }

    if (els.start) {
      els.start.addEventListener("click", function () {
        return send("start", api.buildStartRequest());
      });
    }
    if (els.abort) {
      els.abort.addEventListener("click", function () {
        state.armed = true;
        notice("");
        render();
        focus(els.keep);
      });
      els.keep.addEventListener("click", disarm);
      els.confirm.addEventListener("click", function () {
        return send("abort", api.buildAbortRequest());
      });
    }

    /* Escape belongs to whatever has focus: it disarms only from inside the
     * run controls, or from nowhere in particular. */
    function onKey(event) {
      if (event.key !== "Escape" || !state.armed) return;
      var active = doc.activeElement;
      if (active && active !== doc.body && bar.contains && !bar.contains(active)) return;
      disarm();
    }

    if (doc.addEventListener) doc.addEventListener("keydown", onKey);

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
        if (doc.removeEventListener) doc.removeEventListener("keydown", onKey);
      },
    };
  }

  api.mount = mount;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ScenarioBar = api;
})(typeof window !== "undefined" ? window : globalThis);
