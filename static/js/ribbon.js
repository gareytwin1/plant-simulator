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
 * The alarm summary counts the alarms still on the console by priority, with
 * how many wait on an acknowledgement, and takes the worst priority's colour.
 * It links to the console's alarm section until the alarm overlay (T20-12).
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
   * so nothing here needs escaping. The glyph is drawn by CSS from tokens.css. */
  api.summaryView = function (summary) {
    if (summary.total === 0) {
      return { html: "No active alarms", label: "No active alarms. Go to the alarm list." };
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
        ". Go to the alarm list.",
    };
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
        if (nextPhase !== phase) shown = null;
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
   * to the run controls. Returns {update, refresh, stop, ready}: `update`
   * takes each snapshot the page receives. */
  function mount(doc, options) {
    var settings = options || {};
    var doFetch = settings.fetch || root.fetch.bind(root);
    var ribbon = doc.getElementById("ribbon");
    var pollMs = settings.pollMs === undefined ? Number(ribbon.getAttribute("data-interval-seconds")) * 1000 : settings.pollMs;
    var alarmLink = doc.getElementById("ribbon-alarms");
    var alarmText = doc.getElementById("ribbon-alarm-summary");
    var clockEls = [doc.getElementById("run-clock"), doc.getElementById("run-clock-inline")];
    var clock = api.createRunClock();
    var timer = null;
    var latest = 0;

    function drawClock() {
      var text = api.clockText(clock.read(), clock.times());
      clockEls[0].textContent = text;
      clockEls[0].hidden = text === "";
      clockEls[1].textContent = text === "" ? "" : " · " + text;
    }

    function drawAlarms(alarms) {
      var summary = api.summarize(alarms);
      var view = api.summaryView(summary);
      ribbon.setAttribute("data-severity", summary.worst);
      ribbon.setAttribute("data-unack", summary.unacknowledged > 0 ? "true" : "false");
      alarmText.innerHTML = view.html;
      alarmLink.setAttribute("aria-label", view.label);
    }

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

    var run = Scenario.mount(doc, {
      fetch: doFetch,
      pollMs: pollMs,
      onChange: settings.onChange,
      onResult: function (status, body) {
        var phase = Scenario.phaseOf(status, body);
        if (phase === null) return;
        clock.result(phase, api.runTimes(status, body));
        drawClock();
      },
    });

    drawClock();
    var ready = Promise.all([run.poll(), refresh()]);
    if (pollMs) timer = root.setInterval(tick, pollMs);

    return {
      ready: ready,
      refresh: refresh,
      update: function (snapshot) {
        if (!snapshot || typeof snapshot.sim_time !== "number") return;
        clock.snapshot(snapshot.sim_time);
        drawClock();
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
