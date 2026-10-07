/* Landing page (T16-11): free play and scenario choice, then on to the console.
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
  var CONSOLE_URL = "/console";

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

  api.CONSOLE_URL = CONSOLE_URL;

  api.buildLoadRequest = function (scenarioKey) {
    return {
      url: LOAD_URL,
      init: {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario: scenarioKey }),
      },
    };
  };

  api.buildUnloadRequest = function () {
    return { url: UNLOAD_URL, init: { method: "POST" } };
  };

  /* `labels` is the server's phase-to-text map, sent with the page so the text
   * has one source. */
  api.phaseLabel = function (labels, phase) {
    return Object.prototype.hasOwnProperty.call(labels, phase) ? labels[phase] : "";
  };

  api.formatTime = function (seconds) {
    return String(Math.round(seconds));
  };

  /* browser glue */

  /* `navigate` exists so a test can see where the page goes; the page itself
   * leaves for the console once its load or free-play call succeeds. */
  function mount(doc, fetchImpl, navigate) {
    var doFetch = fetchImpl || root.fetch.bind(root);
    var goTo = navigate || function (url) {
      if (root.location) root.location.assign(url);
    };
    var labels = JSON.parse(doc.getElementById("standing").getAttribute("data-phase-labels"));
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
        goTo(CONSOLE_URL);
      } catch (error) {
        notice(api.refusalText(0), "refused");
      } finally {
        setBusy(false);
      }
    }

    doc.getElementById("free-play").addEventListener("click", function () {
      send(api.buildUnloadRequest(), function () {
        modeEl.textContent = "Free play";
        phaseEl.textContent = api.phaseLabel(labels, "idle");
        notice("Free play is ready.", "ok");
      });
    });

    doc.querySelectorAll(".scenario").forEach(function (card) {
      card.querySelector(".load-scenario").addEventListener("click", function () {
        var title = card.getAttribute("data-title");
        send(api.buildLoadRequest(card.getAttribute("data-scenario")), function (result) {
          modeEl.textContent = "Scenario: " + title;
          phaseEl.textContent = api.phaseLabel(labels, result.phase);
          notice(title + " is loaded.", "ok");
        });
      });
    });
  }

  api.mount = mount;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Landing = api;
})(typeof window !== "undefined" ? window : globalThis);
