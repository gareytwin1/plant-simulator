/* Landing page (T16-11): free play and scenario choice.
 *
 * Talks to the scenario API (app/api/scenario.py) and reads /api/snapshot for
 * the plant time. A refusal is shown in this file's own plain words, chosen by
 * status code, and never the server's text: a scenario's id and its fault must
 * not reach the page through an error. Everything above the "browser glue"
 * marker is pure so it runs under Node without a DOM; tests/test_landing.py
 * drives it that way.
 */
(function (root) {
  "use strict";

  var LOAD_URL = "/api/scenario/load";
  var UNLOAD_URL = "/api/scenario/unload";
  var SNAPSHOT_URL = "/api/snapshot";

  var PHASE_LABEL = {
    idle: "",
    loaded: "Loaded, not started",
    running: "Running",
    complete: "Finished",
    aborted: "Aborted",
  };

  var api = {};

  api.refusalText = function (status) {
    if (status === 409) {
      return "A scenario is running. Abort it before choosing another, or before returning to free play.";
    }
    if (status === 404) return "That scenario is not available any more. Reload the page to see the current list.";
    if (status === 400) return "That scenario could not be set up, so nothing was changed.";
    if (status === 429) return "Too many requests. Wait a moment and try again.";
    return "Something went wrong, so nothing was changed. Try again.";
  };

  api.buildLoadRequest = function (scenarioId) {
    return {
      url: LOAD_URL,
      init: {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario: scenarioId }),
      },
    };
  };

  api.buildUnloadRequest = function () {
    return { url: UNLOAD_URL, init: { method: "POST" } };
  };

  api.phaseLabel = function (phase) {
    return PHASE_LABEL[phase] || "";
  };

  api.formatTime = function (seconds) {
    return String(Math.round(seconds));
  };

  /* browser glue */

  function mount(doc, fetchImpl) {
    var doFetch = fetchImpl || root.fetch.bind(root);
    var modeEl = doc.getElementById("standing-mode");
    var phaseEl = doc.getElementById("standing-phase");
    var timeEl = doc.getElementById("standing-time");
    var noticeEl = doc.getElementById("notice");
    var buttons = Array.prototype.slice.call(doc.querySelectorAll("main button"));

    function notice(text, kind) {
      noticeEl.textContent = text;
      noticeEl.setAttribute("data-kind", kind);
      noticeEl.hidden = false;
    }

    function setBusy(busy) {
      buttons.forEach(function (button) {
        button.disabled = busy;
      });
    }

    async function refreshTime() {
      try {
        var response = await doFetch(SNAPSHOT_URL);
        if (!response.ok) return;
        var snapshot = await response.json();
        timeEl.textContent = api.formatTime(snapshot.sim_time);
      } catch (error) {
        // The time is a convenience; the action already succeeded.
      }
    }

    async function send(request, onSuccess) {
      setBusy(true);
      try {
        var response = await doFetch(request.url, request.init);
        if (!response.ok) {
          notice(api.refusalText(response.status), "refused");
          return;
        }
        var body = await response.json();
        onSuccess(body);
        await refreshTime();
      } catch (error) {
        notice(api.refusalText(0), "refused");
      } finally {
        setBusy(false);
      }
    }

    doc.getElementById("free-play").addEventListener("click", function () {
      send(api.buildUnloadRequest(), function () {
        modeEl.textContent = "Free play";
        phaseEl.textContent = api.phaseLabel("idle");
        notice("Free play is ready.", "ok");
      });
    });

    doc.querySelectorAll(".scenario").forEach(function (card) {
      card.querySelector(".load-scenario").addEventListener("click", function () {
        var title = card.getAttribute("data-title");
        send(api.buildLoadRequest(card.getAttribute("data-scenario")), function (result) {
          modeEl.textContent = "Scenario: " + title;
          phaseEl.textContent = api.phaseLabel(result.phase);
          notice(title + " is loaded.", "ok");
        });
      });
    });
  }

  api.mount = mount;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Landing = api;
})(typeof window !== "undefined" ? window : globalThis);
