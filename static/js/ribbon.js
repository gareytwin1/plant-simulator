/* The ribbon (T20-8): one header on every page, carrying the run and the
 * alarms, after prototype/console-redesign.
 *
 * Composes what other modules already own rather than copying it:
 * static/js/scenario.js runs the run controls and the run's name and state,
 * and static/js/alarms.js replays the alarm history. This file adds the run
 * clock and the alarm summary.
 *
 * The run clock is scenario time, never a wall clock: GET /api/scenario/result
 * gives the run's elapsed time and limit at each poll, and between polls the
 * console's snapshots carry it forward by how far the plant's own sim_time has
 * moved. While running it never steps backwards, so a poll that lands a moment
 * behind the last snapshot holds the clock rather than rewinding it.
 *
 * Pause and Resume freeze and unfreeze the plant the session shows
 * (POST /api/simulation/pause and /resume, T20-14). They follow the snapshot's
 * `running` field, offered in free play and while a run is running, and only
 * on the console where the run controls are. A refusal is worded here by
 * status code, never from the server's text.
 *
 * The alarm summary counts the alarms still on the console by priority, with
 * how many wait on an acknowledgement, and takes the worst priority's colour.
 * On the console it opens the alarm overlay (alarms.js); elsewhere it links there.
 *
 * Everything above the "browser glue" marker is pure so it runs under Node
 * without a DOM; tests/test_ribbon.py drives it that way.
 */
(function (root) {
  "use strict";

  var node = typeof module !== "undefined" && module.exports;
  var Alarms = node ? require("./alarms.js") : root.AlarmConsole;
  var Scenario = node ? require("./scenario.js") : root.ScenarioBar;

  var PRIORITIES = ["critical", "high", "low"];
  var PRIORITY_LABEL = { critical: "CRIT", high: "HIGH", low: "LOW" };

  var PAUSE_URL = "/api/simulation/pause";
  var RESUME_URL = "/api/simulation/resume";

  var api = {};

  /* Counts by priority, how many are not acknowledged (raised, or returned
   * to normal unseen), and the worst priority present or "none". */
  api.summarize = function (alarms) {
    var counts = Alarms.countsByPriority(alarms);
    var unacknowledged = alarms.filter(function (alarm) {
      return alarm.state !== "acked";
    }).length;
    var worst = PRIORITIES.filter(function (priority) {
      return counts[priority] > 0;
    })[0] || "none";

    return { counts: counts, unacknowledged: unacknowledged, worst: worst, total: alarms.length };
  };

  /* The summary's markup and its spoken label. Numbers and fixed words only,
   * so nothing here needs escaping. The glyph is drawn by CSS from tokens.css.
   * `opensList` is true on the console, where pressing it opens the alarm list;
   * from any other page it goes to the console, where the list is closed. */
  api.summaryView = function (summary, opensList) {
    var action = opensList ? "Open the alarm list." : "Go to the console.";

    if (summary.total === 0) {
      return { html: "No active alarms", label: "No active alarms. " + action };
    }

    var shown = PRIORITIES.filter(function (priority) {
      return summary.counts[priority] > 0;
    });
    var badges = shown.map(function (priority) {
      return '<span class="sev-badge" data-priority="' + priority + '">' +
        summary.counts[priority] + " " + PRIORITY_LABEL[priority] + "</span>";
    });
    var unack = summary.unacknowledged ? summary.unacknowledged + " new" : "All acknowledged";
    var spoken = shown.map(function (priority) {
      return summary.counts[priority] + " " + PRIORITY_LABEL[priority];
    }).join(", ");

    return {
      html: badges.join("") + '<span class="alarm-unack">' + unack + "</span>",
      label: spoken + " alarms, " +
        (summary.unacknowledged ? summary.unacknowledged + " not acknowledged" : "all acknowledged") +
        ". " + action,
    };
  };

  /* Which of Pause and Resume to offer. `running` is the snapshot's field, or
   * null before the first snapshot, when neither is offered. Only free play
   * and a running scenario have a clock to stop. */
  api.pauseControls = function (phase, running) {
    var live = (phase === "idle" || phase === "running") && typeof running === "boolean";
    return { pause: live && running, resume: live && !running };
  };

  api.buildPauseRequest = function (pause) {
    return { url: pause ? PAUSE_URL : RESUME_URL, init: { method: "POST" } };
  };

  /* The running state a pause or resume response reports, or null. */
  api.runningOf = function (status, body) {
    if (status !== 200 || body === null || typeof body !== "object") return null;
    return typeof body.running === "boolean" ? body.running : null;
  };

  api.pauseRefusalText = function (pause, status) {
    var verb = pause ? "pause" : "resume";
    if (status === 409) return "There is nothing to " + verb + " now. The run may have ended or been unloaded from another tab.";
    if (status === 429) return "Too many requests. Wait a moment and try again.";
    if (status === 0 || status >= 500) return "The request did not finish. Check whether the plant is running before trying again.";
    return "Something went wrong, so nothing was changed. Try again.";
  };

  api.formatClock = function (seconds) {
    var whole = Math.max(0, Math.floor(seconds));
    return String(Math.floor(whole / 60)).padStart(2, "0") + ":" + String(whole % 60).padStart(2, "0");
  };

  /* The run's elapsed time and limit from a result response, or null when it
   * carries none (free play, a failure). */
  api.runTimes = function (status, body) {
    if (status !== 200 || body === null || typeof body !== "object") return null;
    if (typeof body.elapsed_s !== "number" || typeof body.time_limit_s !== "number") return null;
    return { elapsed: body.elapsed_s, limit: body.time_limit_s };
  };

  api.clockText = function (elapsed, times) {
    if (elapsed === null || times === null) return "";
    return api.formatClock(elapsed) + " / " + api.formatClock(times.limit);
  };

  /* The run clock as a state machine fed by results and snapshots; it never
   * reads a clock of its own. `read()` is the elapsed time to show, or null. */
  api.createRunClock = function () {
    var phase = "idle";
    var times = null;
    var anchor = null;
    var latest = null;
    var shown = null;

    return {
      result: function (nextPhase, nextTimes) {
        // A new phase, or a run that went back in time or changed its limit,
        // is a different run: what the old one showed means nothing to it.
        var restarted = times !== null && nextTimes !== null &&
          (nextTimes.elapsed < times.elapsed || nextTimes.limit !== times.limit);
        if (nextPhase !== phase || restarted) shown = null;
        phase = nextPhase;
        times = nextTimes;
        anchor = latest;
      },
      snapshot: function (simTime) {
        latest = simTime;
        if (anchor === null) anchor = simTime;
      },
      read: function () {
        if (times === null) return null;
        var elapsed = times.elapsed;
        if (phase === "running") {
          if (anchor !== null && latest !== null) elapsed += Math.max(0, latest - anchor);
          elapsed = Math.min(elapsed, times.limit);
          if (shown !== null) elapsed = Math.max(elapsed, shown);
          shown = elapsed;
        }
        return elapsed;
      },
      times: function () {
        return times;
      },
    };
  };

  /* browser glue */

  /* Mount the ribbon. `options.fetch` and `options.pollMs` exist so a page or
   * test can supply its own transport and timer; `options.onChange` is handed
   * to the run controls, and `options.onResult(status, body)` sees every
   * result they apply, after the ribbon has (the console's debrief panel,
   * T20-22). Returns {update, refresh, poll, adopt, stop, ready}: `update`
   * takes each snapshot the page receives, and `adopt` is the run controls'. */
  function mount(doc, options) {
    var settings = options || {};
    var doFetch = settings.fetch || root.fetch.bind(root);
    var ribbon = doc.getElementById("ribbon");
    var pollMs = settings.pollMs === undefined ? Number(ribbon.getAttribute("data-interval-seconds")) * 1000 : settings.pollMs;
    var alarmLink = doc.getElementById("ribbon-alarms");
    var alarmText = doc.getElementById("ribbon-alarm-summary");
    var clockEls = [doc.getElementById("run-clock"), doc.getElementById("run-clock-value"), doc.getElementById("run-clock-inline")];
    var clock = api.createRunClock();
    var pauseButton = doc.getElementById("ribbon-pause");
    var resumeButton = doc.getElementById("ribbon-resume");
    var notice = doc.getElementById("scenario-notice");
    var timer = null;
    var latest = 0;
    var phase = Scenario.phaseOf(200, { phase: doc.getElementById("scenario-bar").getAttribute("data-phase") }) || "idle";
    var plantRunning = null;
    var pausing = false;
    // The notice is shared with the run controls, so only a message this
    // code wrote is this code's to clear.
    var pauseNotice = "";
    var run = null;

    function drawPause() {
      if (run) run.render();
      var offered = api.pauseControls(phase, plantRunning);
      if (pauseButton) {
        pauseButton.hidden = !offered.pause;
        pauseButton.disabled = pausing;
      }
      if (resumeButton) {
        resumeButton.hidden = !offered.resume;
        resumeButton.disabled = pausing;
      }
    }

    function say(text) {
      pauseNotice = text;
      notice.textContent = text;
      notice.hidden = text === "";
    }

    async function sendPause(pause) {
      pausing = true;
      drawPause();
      try {
        var request = api.buildPauseRequest(pause);
        var response = await doFetch(request.url, request.init);
        var body = response.ok ? await response.json() : null;
        var running = api.runningOf(response.status, body);
        if (running !== null) {
          plantRunning = running;
          if (pauseNotice !== "" && notice.textContent === pauseNotice) say("");
        } else {
          say(api.pauseRefusalText(pause, response.status));
        }
      } catch (error) {
        say(api.pauseRefusalText(pause, 0));
      } finally {
        pausing = false;
        drawPause();
      }
    }

    if (pauseButton) pauseButton.addEventListener("click", function () { return sendPause(true); });
    if (resumeButton) resumeButton.addEventListener("click", function () { return sendPause(false); });

    function drawClock() {
      var text = api.clockText(clock.read(), clock.times());
      clockEls[0].hidden = text === "";
      clockEls[1].textContent = text;
      clockEls[2].textContent = text === "" ? "" : " · " + text;
    }

    function drawAlarms(alarms) {
      var summary = api.summarize(alarms);
      var view = api.summaryView(summary, alarmLink.tagName === "BUTTON");
      ribbon.setAttribute("data-severity", summary.worst);
      ribbon.setAttribute("data-unack", summary.unacknowledged > 0 ? "true" : "false");
      alarmText.innerHTML = view.html;
      alarmLink.setAttribute("aria-label", view.label);
    }

    /* Also the way to catch up at once after something on the page changed the
     * history, such as an acknowledgement. */
    async function refresh() {
      var ticket = ++latest;
      try {
        var response = await doFetch(Alarms.HISTORY_URL);
        if (!response.ok) return;
        var entries = await response.json();
        // A newer refresh started while this one was in flight; its answer wins.
        if (ticket === latest) drawAlarms(Alarms.deriveAlarms(entries));
      } catch (error) {
        // The summary keeps its last answer; the next tick tries again.
      }
    }

    function tick() {
      if (root.document && root.document.hidden) return;
      refresh();
    }

    run = Scenario.mount(doc, {
      fetch: doFetch,
      isPaused: function () {
        return plantRunning === false && (phase === "idle" || phase === "running");
      },
      pollMs: pollMs,
      onChange: settings.onChange,
      onResult: function (status, body) {
        var next = Scenario.phaseOf(status, body);
        if (next === null) return;
        phase = next;
        clock.result(phase, api.runTimes(status, body));
        drawClock();
        drawPause();
        if (settings.onResult) settings.onResult(status, body);
      },
    });

    drawClock();
    var ready = Promise.all([run.poll(), refresh()]);
    if (pollMs) timer = root.setInterval(tick, pollMs);

    return {
      ready: ready,
      refresh: refresh,
      poll: run.poll,
      adopt: run.adopt,
      update: function (snapshot) {
        if (!snapshot || typeof snapshot.sim_time !== "number") return;
        clock.snapshot(snapshot.sim_time);
        drawClock();
        if (typeof snapshot.running === "boolean" && !pausing) {
          plantRunning = snapshot.running;
          drawPause();
        }
      },
      stop: function () {
        if (timer !== null) root.clearInterval(timer);
        run.stop();
      },
    };
  }

  api.mount = mount;

  if (node) module.exports = api;
  else root.Ribbon = api;
})(typeof window !== "undefined" ? window : globalThis);
