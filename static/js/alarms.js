/* Alarm banner and summary (T10-5).
 *
 * The console surface for the alarm API in app/api/alarms.py:
 *   GET  /api/alarms/history      -> the C6 event record, oldest first
 *   POST /api/alarms/acknowledge  -> {"alarm_id": ...}
 *
 * The history is the only source: an alarm's lifecycle (app.alarms.state) is
 * replayed from it, not asked of the server. Everything above the "browser
 * glue" marker is pure (data in, data or an HTML string out) so it runs under
 * Node without a DOM; tests/test_alarm_console.py drives it that way.
 * Colour comes from static/css/tokens.css through alarms.css, never from here.
 */
(function (root) {
  "use strict";

  var HISTORY_URL = "/api/alarms/history";
  var ACKNOWLEDGE_URL = "/api/alarms/acknowledge";

  // Lower rank sorts first. Priority is app.alarms.manager.Priority.
  var PRIORITY_RANK = { critical: 0, high: 1, low: 2 };
  var PRIORITY_LABEL = { critical: "CRIT", high: "HIGH", low: "LOW" };

  // A state that still wants the operator: unack first, then returned-unack.
  var STATE_RANK = { unack: 0, rtn_unack: 1, acked: 2 };
  var STATE_LABEL = { unack: "UNACK", acked: "ACK", rtn_unack: "RTN" };

  var SORT_KEYS = ["priority", "state", "tag", "message", "time"];

  /* Replay the history into the alarms still on the console, one per id.
   *
   * Mirrors app.alarms.state.Alarm: a raising event makes the alarm UNACK, an
   * acknowledgement moves UNACK to ACKED (or RTN_UNACK to NORMAL), a clear
   * moves UNACK to RTN_UNACK (or ACKED to NORMAL). An id names one monitored
   * point for its whole life, so a later event for it escalates or re-raises
   * the same alarm rather than adding a second row. An acknowledge or clear
   * for an id whose raising event was evicted from the history has nothing to
   * apply to and is ignored.
   */
  function deriveAlarms(entries) {
    var byId = new Map();

    entries.forEach(function (entry) {
      var alarm = byId.get(entry.id);

      if (entry.type === "alarm") {
        var raised = alarm && alarm.state !== "normal" ? alarm.raisedAt : entry.sim_time;
        byId.set(entry.id, {
          id: entry.id,
          tag: entry.tag,
          priority: entry.priority,
          message: entry.message,
          state: "unack",
          raisedAt: raised,
          updatedAt: entry.sim_time,
        });
        return;
      }

      if (!alarm) return;

      if (entry.type === "acknowledge") {
        if (alarm.state === "unack") alarm.state = "acked";
        else if (alarm.state === "rtn_unack") alarm.state = "normal";
      } else if (entry.type === "clear") {
        if (alarm.state === "unack") alarm.state = "rtn_unack";
        else if (alarm.state === "acked") alarm.state = "normal";
      }
      alarm.updatedAt = entry.sim_time;
    });

    return Array.from(byId.values()).filter(function (alarm) {
      return alarm.state !== "normal";
    });
  }

  function compareBy(key) {
    return function (a, b) {
      switch (key) {
        case "priority":
          return PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority];
        case "state":
          return STATE_RANK[a.state] - STATE_RANK[b.state];
        case "time":
          return a.raisedAt - b.raisedAt;
        default:
          return a[key] < b[key] ? -1 : a[key] > b[key] ? 1 : 0;
      }
    };
  }

  /* A copy sorted by `key` ("priority", "state", "tag", "message", "time"),
   * ascending unless `descending`. Ties fall back to most urgent first, then
   * raise time, then id, so the order is the same for the same data. */
  function sortAlarms(alarms, key, descending) {
    var primary = compareBy(key);
    var tiebreak = [compareBy("priority"), compareBy("state"), compareBy("time"), compareBy("id")];
    var sign = descending ? -1 : 1;

    return alarms.slice().sort(function (a, b) {
      var result = primary(a, b) * sign;
      for (var i = 0; result === 0 && i < tiebreak.length; i++) result = tiebreak[i](a, b);
      return result;
    });
  }

  function mostUrgent(alarms) {
    return sortAlarms(alarms, "priority", false)[0] || null;
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatSimTime(seconds) {
    var whole = Math.max(0, Math.floor(seconds));
    var pad = function (n) {
      return String(n).padStart(2, "0");
    };
    return pad(Math.floor(whole / 3600)) + ":" + pad(Math.floor(whole / 60) % 60) + ":" + pad(whole % 60);
  }

  /* The glyph comes from --symbol-alarm-* in CSS, so a badge is its label plus
   * a [data-priority] hook; colour and glyph are never the only channel. */
  function badge(priority, suffix) {
    return (
      '<span class="alarm-badge" data-priority="' + escapeHtml(priority) + '">' +
      escapeHtml(PRIORITY_LABEL[priority] || priority.toUpperCase()) +
      (suffix === undefined ? "" : " " + escapeHtml(suffix)) +
      "</span>"
    );
  }

  function countsByPriority(alarms) {
    var counts = { critical: 0, high: 0, low: 0 };
    alarms.forEach(function (alarm) {
      counts[alarm.priority] += 1;
    });
    return counts;
  }

  function renderBanner(alarms) {
    if (alarms.length === 0) {
      return '<div class="alarm-banner" data-empty="true">No active alarms</div>';
    }

    var top = mostUrgent(alarms);
    var counts = countsByPriority(alarms);
    var tallies = ["critical", "high", "low"]
      .filter(function (priority) {
        return counts[priority] > 0;
      })
      .map(function (priority) {
        return badge(priority, counts[priority]);
      })
      .join("");

    return (
      '<div class="alarm-banner" data-priority="' + escapeHtml(top.priority) +
      '" data-state="' + escapeHtml(top.state) + '">' +
      '<span class="alarm-banner-headline">' + badge(top.priority) +
      '<span class="alarm-banner-message">' + escapeHtml(top.message) + "</span></span>" +
      '<span class="alarm-banner-tallies">' + tallies + "</span>" +
      "</div>"
    );
  }

  var COLUMNS = [
    { key: "priority", label: "Priority" },
    { key: "state", label: "State" },
    { key: "tag", label: "Tag" },
    { key: "message", label: "Message" },
    { key: "time", label: "Raised" },
  ];

  function renderSummary(alarms, sort) {
    var active = sort || { key: "priority", descending: false };
    var rows = sortAlarms(alarms, active.key, active.descending);

    var head = COLUMNS.map(function (column) {
      var current = column.key === active.key;
      var direction = current ? (active.descending ? "descending" : "ascending") : "none";
      return (
        '<th scope="col" aria-sort="' + direction + '">' +
        '<button type="button" class="alarm-sort" data-sort="' + column.key + '">' +
        column.label + "</button></th>"
      );
    }).join("") + '<th scope="col">Action</th>';

    var body = rows.length === 0
      ? '<tr><td colspan="6" class="alarm-empty">No active alarms</td></tr>'
      : rows.map(function (alarm) {
          var needsAck = alarm.state === "unack" || alarm.state === "rtn_unack";
          var action = needsAck
            ? '<button type="button" class="alarm-ack" data-alarm-id="' + escapeHtml(alarm.id) +
              '">Acknowledge</button>'
            : "";
          return (
            '<tr class="alarm-row" data-priority="' + escapeHtml(alarm.priority) +
            '" data-state="' + escapeHtml(alarm.state) + '">' +
            "<td>" + badge(alarm.priority) + "</td>" +
            '<td class="alarm-state">' + STATE_LABEL[alarm.state] + "</td>" +
            "<td>" + escapeHtml(alarm.tag) + "</td>" +
            "<td>" + escapeHtml(alarm.message) + "</td>" +
            "<td>" + formatSimTime(alarm.raisedAt) + "</td>" +
            "<td>" + action + "</td></tr>"
          );
        }).join("");

    return (
      '<table class="alarm-summary"><thead><tr>' + head + "</tr></thead><tbody>" + body +
      "</tbody></table>"
    );
  }

  function buildAcknowledgeRequest(alarmId) {
    return {
      url: ACKNOWLEDGE_URL,
      init: {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alarm_id: alarmId }),
      },
    };
  }

  var api = {
    SORT_KEYS: SORT_KEYS,
    HISTORY_URL: HISTORY_URL,
    deriveAlarms: deriveAlarms,
    sortAlarms: sortAlarms,
    countsByPriority: countsByPriority,
    renderBanner: renderBanner,
    renderSummary: renderSummary,
    buildAcknowledgeRequest: buildAcknowledgeRequest,
  };

  /* ---- browser glue ---------------------------------------------------- */

  /* Mount the console into `bannerEl` and `summaryEl`. `options.fetch` and
   * `options.pollMs` exist so a page or test can supply its own transport;
   * returns {ready, refresh, stop}, `ready` resolving once the first draw is done. */
  function mount(bannerEl, summaryEl, options) {
    var settings = options || {};
    var doFetch = settings.fetch || root.fetch.bind(root);
    var sort = { key: "priority", descending: false };
    var alarms = [];
    var timer = null;
    var latest = 0;

    function draw() {
      bannerEl.innerHTML = renderBanner(alarms);
      summaryEl.innerHTML = renderSummary(alarms, sort);
    }

    async function refresh() {
      var ticket = ++latest;
      var response = await doFetch(HISTORY_URL);
      if (!response.ok) return;
      var entries = await response.json();
      // A newer refresh started while this one was in flight; its answer wins.
      if (ticket !== latest) return;
      alarms = deriveAlarms(entries);
      draw();
    }

    summaryEl.addEventListener("click", async function (event) {
      var target = event.target;
      if (!target || !target.closest) return;

      var sortButton = target.closest("[data-sort]");
      if (sortButton) {
        var key = sortButton.getAttribute("data-sort");
        sort = { key: key, descending: sort.key === key && !sort.descending };
        draw();
        return;
      }

      var ackButton = target.closest("[data-alarm-id]");
      if (ackButton) {
        var request = buildAcknowledgeRequest(ackButton.getAttribute("data-alarm-id"));
        var response = await doFetch(request.url, request.init);
        if (response.ok) await refresh();
      }
    });

    var ready = refresh();
    if (settings.pollMs) timer = root.setInterval(refresh, settings.pollMs);

    return {
      ready: ready,
      refresh: refresh,
      stop: function () {
        if (timer !== null) root.clearInterval(timer);
      },
    };
  }

  api.mount = mount;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AlarmConsole = api;
})(typeof window !== "undefined" ? window : globalThis);
