/* Alarm overlay (T10-5, T20-12).
 *
 * The console surface for the alarm API in app/api/alarms.py:
 *   GET  /api/alarms/history      -> the C6 event record, oldest first
 *   POST /api/alarms/acknowledge  -> {"alarm_id": ...}
 *
 * The history is the only source: an alarm's lifecycle (app.alarms.state) is
 * replayed from it, not asked of the server. The ribbon's alarm summary opens
 * a modal overlay over the workspace: a filtered list, Acknowledge per row and
 * for all shown, Show trend and Locate. Everything above the "browser glue"
 * marker is pure (data in, data or an HTML string out) so it runs under Node
 * without a DOM; tests/test_alarm_console.py drives it that way.
 * Colour comes from static/css/tokens.css through alarms.css, never from here.
 */
(function (root) {
  "use strict";

  var HISTORY_URL = "/api/alarms/history";
  var ACKNOWLEDGE_URL = "/api/alarms/acknowledge";

  // Lower rank sorts first. Priority is app.alarms.manager.Priority.
  var PRIORITIES = ["critical", "high", "low"];
  var PRIORITY_RANK = { critical: 0, high: 1, low: 2 };
  var PRIORITY_LABEL = { critical: "CRIT", high: "HIGH", low: "LOW" };

  // A state that still wants the operator: unack first, then returned-unack.
  var STATE_RANK = { unack: 0, rtn_unack: 1, acked: 2 };

  var DEFAULT_FILTERS = { priorities: PRIORITIES, acknowledgement: "all", tag: "all", text: "" };

  /* The monitored point an alarm event names, '<tag>.<variable>', or null for
   * an event that carries none (C6's data.point is additive). */
  function pointOf(entry) {
    var data = entry.data;
    return data && typeof data.point === "string" && data.point !== "" ? data.point : null;
  }

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
          point: pointOf(entry),
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

  function countsByPriority(alarms) {
    var counts = { critical: 0, high: 0, low: 0 };
    alarms.forEach(function (alarm) {
      counts[alarm.priority] += 1;
    });
    return counts;
  }

  function needsAcknowledge(alarm) {
    return alarm.state !== "acked";
  }

  function unacknowledged(alarms) {
    return alarms.filter(needsAcknowledge);
  }

  function conditionOf(alarm) {
    return alarm.state === "rtn_unack" ? "Returned to normal" : "Active";
  }

  /* The alarms `filters` lets through, in the order they were given. Filters
   * combine: an alarm is shown only when it passes every one. `filters` may
   * name any of priorities (a list), acknowledgement ("all", "unacked" or
   * "acked"), tag ("all" or one tag) and text (a case-insensitive piece of the
   * tag or message); one left out lets everything through. */
  function filterAlarms(alarms, filters) {
    var wanted = Object.assign({}, DEFAULT_FILTERS, filters || {});
    var text = String(wanted.text).trim().toLowerCase();

    return alarms.filter(function (alarm) {
      if (wanted.priorities.indexOf(alarm.priority) < 0) return false;
      if (wanted.acknowledgement === "unacked" && !needsAcknowledge(alarm)) return false;
      if (wanted.acknowledgement === "acked" && needsAcknowledge(alarm)) return false;
      if (wanted.tag !== "all" && alarm.tag !== wanted.tag) return false;
      if (text && (alarm.tag + " " + alarm.message).toLowerCase().indexOf(text) < 0) return false;
      return true;
    });
  }

  /* The rows the overlay lists: what `filters` lets through, most urgent first. */
  function shownAlarms(alarms, filters) {
    return sortAlarms(filterAlarms(alarms, filters), "priority", false);
  }

  /* The tags to offer in the equipment filter: those with an active alarm, plus
   * `keep` (the one chosen), so a filter never changes because its alarm cleared. */
  function equipmentTags(alarms, keep) {
    var tags = alarms.map(function (alarm) {
      return alarm.tag;
    });
    if (keep && keep !== "all") tags.push(keep);

    return tags
      .filter(function (tag, at) {
        return tags.indexOf(tag) === at;
      })
      .sort();
  }

  function renderTagOptions(tags, selected) {
    return (
      '<option value="all">All equipment</option>' +
      tags.map(function (tag) {
        return (
          '<option value="' + escapeHtml(tag) + '"' + (tag === selected ? " selected" : "") + ">" +
          escapeHtml(tag) + "</option>"
        );
      }).join("")
    );
  }

  function summaryLine(alarms, shown) {
    return (
      alarms.length + " active \u00b7 " + unacknowledged(alarms).length + " not acknowledged \u00b7 showing " +
      shown.length
    );
  }

  function emptyText(alarms) {
    return alarms.length === 0 ? "No active alarms." : "No alarms match these filters.";
  }

  /* The glyph comes from --symbol-alarm-* in CSS, so a badge is its label plus
   * a [data-priority] hook; colour and glyph are never the only channel. */
  function badge(priority) {
    return (
      '<span class="alarm-badge" data-priority="' + escapeHtml(priority) + '">' +
      escapeHtml(PRIORITY_LABEL[priority] || priority.toUpperCase()) + "</span>"
    );
  }

  function rowButton(action, alarm, label, text, quiet) {
    return (
      '<button type="button" class="btn' + (quiet ? " btn-quiet" : "") + '" data-row-action="' + action +
      '" data-alarm-id="' + escapeHtml(alarm.id) + '" aria-label="' + escapeHtml(label) + '">' + text + "</button>"
    );
  }

  /* The table body for `shown`. Show trend is offered only for an alarm that
   * names its point; Acknowledge only where it would change something. */
  function renderRows(shown) {
    return shown.map(function (alarm) {
      var actions = "";
      if (needsAcknowledge(alarm)) {
        actions += rowButton("ack", alarm, "Acknowledge " + alarm.message, "Acknowledge", false);
      }
      if (alarm.point) {
        actions += rowButton("trend", alarm, "Show trend for " + alarm.message, "Show trend", true);
      }
      actions += rowButton("locate", alarm, "Locate " + alarm.tag, "Locate", true);

      var returned = alarm.state === "rtn_unack";
      return (
        '<tr class="alarm-row" data-priority="' + escapeHtml(alarm.priority) +
        '" data-state="' + escapeHtml(alarm.state) + '">' +
        "<td>" + badge(alarm.priority) + "</td>" +
        '<td class="num">' + formatSimTime(alarm.raisedAt) + "</td>" +
        '<td class="num">' + escapeHtml(alarm.tag) + "</td>" +
        "<td>" + escapeHtml(alarm.message) + "</td>" +
        '<td><span class="alarm-condition" data-condition="' + (returned ? "returned" : "active") + '">' +
        conditionOf(alarm) + "</span></td>" +
        '<td><span class="alarm-acked" data-acknowledged="' + (needsAcknowledge(alarm) ? "no" : "yes") + '">' +
        (needsAcknowledge(alarm) ? "No" : "Yes") + "</span></td>" +
        '<td><div class="alarm-actions">' + actions + "</div></td></tr>"
      );
    }).join("");
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
    PRIORITIES: PRIORITIES,
    HISTORY_URL: HISTORY_URL,
    DEFAULT_FILTERS: DEFAULT_FILTERS,
    deriveAlarms: deriveAlarms,
    sortAlarms: sortAlarms,
    countsByPriority: countsByPriority,
    filterAlarms: filterAlarms,
    shownAlarms: shownAlarms,
    unacknowledged: unacknowledged,
    conditionOf: conditionOf,
    equipmentTags: equipmentTags,
    renderTagOptions: renderTagOptions,
    summaryLine: summaryLine,
    emptyText: emptyText,
    renderRows: renderRows,
    buildAcknowledgeRequest: buildAcknowledgeRequest,
  };

  /* ---- browser glue ---------------------------------------------------- */

  var FOCUSABLE = "button, input, select, [href]";

  /* Mount the overlay onto the page that `doc` is: the elements are in
   * templates/console.html by id. `options`:
   *   opener         the ribbon's alarm summary button; it opens and closes the
   *                  overlay, and focus returns to it
   *   inert          the element made inert behind the overlay (the workspace)
   *   fetch, pollMs  a transport and the refresh period while open, for a page or test
   *   onAcknowledge  called after an acknowledgement is recorded
   *   onShowTrend    called with the alarm (tag, point) after the overlay closes;
   *   onLocate       likewise. Each returns false when it had nothing to show,
   *                  and the overlay then opens again saying so.
   * Returns {ready, refresh, open, close, isOpen, stop}. The history is fetched
   * on opening and then every `pollMs` while open; nothing polls while closed. */
  function mount(doc, options) {
    var settings = options || {};
    var doFetch = settings.fetch || root.fetch.bind(root);
    var $ = function (id) {
      return doc.getElementById(id);
    };
    var overlay = $("alarm-overlay");
    var opener = settings.opener;
    var rows = $("alarm-rows");
    var tagSelect = $("filter-tag");
    var ackSelect = $("filter-state");
    var search = $("filter-search");
    var ackAll = $("ack-visible");
    var feedback = $("alarm-feedback");

    var alarms = [];
    var shown = [];
    var drawnRows = ""; // the table starts empty
    var timer = null;
    var latest = 0;
    var open = false;
    var ready = Promise.resolve();

    function filters() {
      return {
        priorities: PRIORITIES.filter(function (priority) {
          return $("filter-" + priority).checked;
        }),
        acknowledgement: ackSelect.value,
        tag: tagSelect.value,
        text: search.value,
      };
    }

    function say(text) {
      feedback.textContent = text;
    }

    function draw() {
      var counts = countsByPriority(alarms);
      PRIORITIES.forEach(function (priority) {
        $("count-" + priority).textContent = counts[priority];
      });

      var chosen = tagSelect.value;
      var options_ = renderTagOptions(equipmentTags(alarms, chosen), chosen);
      if (tagSelect.getAttribute("data-options") !== options_) {
        tagSelect.innerHTML = options_;
        tagSelect.setAttribute("data-options", options_);
        tagSelect.value = chosen;
      }

      shown = shownAlarms(alarms, filters());
      $("alarm-overlay-sub").textContent = summaryLine(alarms, shown);
      var empty = $("alarm-empty");
      empty.hidden = shown.length > 0;
      empty.textContent = emptyText(alarms);
      ackAll.disabled = unacknowledged(shown).length === 0;

      // A redraw that changes nothing is skipped, so a click is never lost to a
      // button replaced between its press and its release.
      var html = renderRows(shown);
      if (html === drawnRows) return;
      drawnRows = html;

      var active = doc.activeElement;
      var kept = active && rows.contains(active) && active.getAttribute
        ? [active.getAttribute("data-row-action"), active.getAttribute("data-alarm-id")]
        : null;
      rows.innerHTML = html;
      if (kept) focusAfterRedraw(kept[0], kept[1]);
    }

    /* Put focus back where it was in the redrawn table: on the same button, or
     * on another action of the same alarm's row, or on the close button. Never
     * on another alarm's button: an acknowledgement would then land on a row
     * the operator has not looked at. */
    function focusAfterRedraw(action, id) {
      var buttons = Array.prototype.slice.call(rows.querySelectorAll("[data-row-action]")).filter(function (button) {
        return button.getAttribute("data-alarm-id") === id;
      });
      var same = buttons.filter(function (button) {
        return button.getAttribute("data-row-action") === action;
      })[0];
      (same || buttons[0] || $("alarm-overlay-close")).focus();
    }

    async function refresh() {
      var ticket = ++latest;
      try {
        var response = await doFetch(HISTORY_URL);
        if (!response.ok) return;
        var entries = await response.json();
        // A newer refresh started while this one was in flight; its answer wins.
        if (ticket !== latest) return;
        alarms = deriveAlarms(entries);
        draw();
      } catch (error) {
        // The list keeps its last answer; the next tick tries again.
      }
    }

    function openOverlay() {
      if (open) return;
      open = true;
      say("");
      overlay.hidden = false;
      opener.setAttribute("aria-expanded", "true");
      if (settings.inert) settings.inert.setAttribute("inert", "");
      draw();
      $("alarm-overlay-close").focus();
      ready = refresh();
      if (settings.pollMs) timer = root.setInterval(refresh, settings.pollMs);
    }

    function closeOverlay(returnFocus) {
      if (!open) return;
      open = false;
      overlay.hidden = true;
      opener.setAttribute("aria-expanded", "false");
      if (settings.inert) settings.inert.removeAttribute("inert");
      if (timer !== null) root.clearInterval(timer);
      timer = null;
      latest += 1;
      if (returnFocus) opener.focus();
    }

    async function acknowledge(ids) {
      var failed = 0;
      await Promise.all(ids.map(async function (id) {
        var request = buildAcknowledgeRequest(id);
        try {
          var response = await doFetch(request.url, request.init);
          if (!response.ok) failed += 1;
        } catch (error) {
          failed += 1;
        }
      }));
      say(failed === 0 ? "" : failed + (failed === 1 ? " acknowledgement" : " acknowledgements") + " could not be recorded.");
      await refresh();
      try {
        if (settings.onAcknowledge) settings.onAcknowledge();
      } catch (error) {
        console.error(error);
      }
    }

    function rowAlarm(id) {
      return alarms.filter(function (alarm) {
        return alarm.id === id;
      })[0] || null;
    }

    rows.addEventListener("click", function (event) {
      var target = event.target;
      var button = target && target.closest ? target.closest("[data-row-action]") : null;
      if (!button) return;
      var id = button.getAttribute("data-alarm-id");
      var action = button.getAttribute("data-row-action");
      var alarm = rowAlarm(id);
      if (!alarm) return;

      if (action === "ack") {
        return acknowledge([id]);
      }
      // Show trend and Locate hand focus to the canvas, so the overlay lets go of it.
      closeOverlay(false);
      var handler = action === "trend" ? settings.onShowTrend : settings.onLocate;
      var shownIt = false;
      try {
        shownIt = !!handler && handler(alarm) !== false;
      } catch (error) {
        console.error(error);
      }
      if (shownIt) return;
      // Nothing to show: the overlay comes back with the reason, focus on the
      // button that was pressed, rather than vanishing without a word.
      openOverlay();
      say(action === "trend" ? "Could not show a trend for this alarm." : "This alarm is not drawn on the canvas.");
      focusAfterRedraw(action, id);
    });

    // Exactly the unacknowledged rows on screen, not whatever the history says now.
    ackAll.addEventListener("click", function () {
      return acknowledge(unacknowledged(shown).map(function (alarm) {
        return alarm.id;
      }));
    });

    [ackSelect, tagSelect].forEach(function (control) {
      control.addEventListener("change", draw);
    });
    search.addEventListener("input", draw);
    PRIORITIES.forEach(function (priority) {
      $("filter-" + priority).addEventListener("change", draw);
    });

    $("alarm-overlay-close").addEventListener("click", function () {
      closeOverlay(true);
    });
    // A click on the dimmed area around the panel, not on the panel itself.
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) closeOverlay(true);
    });
    opener.addEventListener("click", function (event) {
      if (event.preventDefault) event.preventDefault();
      if (open) closeOverlay(true);
      else openOverlay();
    });

    // Tab stays inside the dialog: the workspace is inert, so from its last
    // control it would otherwise run on into the ribbon.
    overlay.addEventListener("keydown", function (event) {
      if (event.key !== "Tab") return;
      var controls = Array.prototype.slice.call(overlay.querySelectorAll(FOCUSABLE)).filter(function (node) {
        return !node.disabled;
      });
      if (controls.length === 0) return;
      var first = controls[0];
      var last = controls[controls.length - 1];
      if (event.shiftKey && doc.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && doc.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    function onEscape(event) {
      if (event.key === "Escape" && open) closeOverlay(true);
    }
    doc.addEventListener("keydown", onEscape);

    return {
      get ready() {
        return ready;
      },
      refresh: refresh,
      open: openOverlay,
      close: function () {
        closeOverlay(true);
      },
      isOpen: function () {
        return open;
      },
      stop: function () {
        closeOverlay(false);
        doc.removeEventListener("keydown", onEscape);
      },
    };
  }

  api.mount = mount;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AlarmConsole = api;
})(typeof window !== "undefined" ? window : globalThis);
