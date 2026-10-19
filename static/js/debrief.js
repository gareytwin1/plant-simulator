/* The result and debrief panel (T20-22): what a finished run came to, on the
 * console, without a reload.
 *
 * Reads only the body the ribbon's scenario poll already fetches
 * (GET /api/scenario/result, through ribbon.js's `onResult`), never a poll of
 * its own, and shows something only once the phase is complete or aborted:
 * while a run is loaded or running nothing of its outcome, score or debrief is
 * rendered, whatever the body carries. What it shows comes from that body
 * alone: outcome, elapsed_s, time_limit_s, score.total, score.penalties and
 * debrief.
 *
 * A criterion is worded from CRITERIA, keyed by the penalty names in
 * config/scoring.yaml; a name the table lacks shows as itself, and
 * tests/test_debrief.py fails when the yaml gains one the table does not word.
 * Only criteria that cost points are listed, each with how much of its own
 * weight it cost (the penalty, 0 to 1), since a criterion can cost too little
 * to move the rounded total.
 *
 * The panel opens itself once per finished run and is non-modal, so the final
 * plant stays readable and operable beside it; the ribbon's Result button
 * reopens it while the run stays finished. Try again is the landing page's
 * load-then-start with the body's key, and a refusal is worded here by status
 * code, never from the server's text.
 *
 * Everything above the "browser glue" marker is pure so it runs under Node
 * without a DOM; tests/test_debrief.py drives it that way.
 */
(function (root) {
  "use strict";

  var node = typeof module !== "undefined" && module.exports;
  var Ribbon = node ? require("./ribbon.js") : root.Ribbon;
  var Scenario = node ? require("./scenario.js") : root.ScenarioBar;

  var LOAD_URL = "/api/scenario/load";
  var START_URL = "/api/scenario/start";
  var FETCH_TIMEOUT_MS = 5000;

  var OUTCOMES = {
    succeeded: { text: "Succeeded", glyph: "\u2713" },
    failed: { text: "Failed", glyph: "\u2717" },
    timed_out: { text: "Timed out", glyph: "\u2717" },
    aborted: { text: "Aborted", glyph: "\u25A0" },
  };
  var UNKNOWN_OUTCOME = { text: "Outcome not recorded", glyph: "?" };

  /* In config/scoring.yaml's weight order, heaviest first, which is the order
   * they are listed in. */
  var CRITERIA = {
    outcome: "The run did not succeed",
    trip_count: "Equipment tripped",
    peak_severity: "A reading went past its limits",
    unnecessary_actions: "Actions that were not needed",
    time_outside_envelope: "Time with readings outside their limits",
    time_to_stabilise: "Time taken to steady the plant",
    alarm_count: "Alarms raised",
    time_to_recognise: "Time taken to act on the first alarm",
    production_lost: "Production lost",
  };

  var api = {};

  api.CRITERIA = CRITERIA;

  function own(table, key) {
    return Object.prototype.hasOwnProperty.call(table, key);
  }

  function isObject(value) {
    return value !== null && typeof value === "object";
  }

  function isNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  api.isFinished = function (status, body) {
    return status === 200 && isObject(body) && (body.phase === "complete" || body.phase === "aborted");
  };

  api.criterionText = function (name) {
    return own(CRITERIA, name) ? CRITERIA[name] : name;
  };

  /* How much of a criterion's own weight a penalty cost. */
  api.extent = function (penalty) {
    if (penalty >= 1) return "in full";
    if (penalty >= 0.25) return "partly";
    return "a little";
  };

  /* The criteria that cost points, worded, the known ones in table order and
   * any others after them in the order the body gave. */
  api.costs = function (penalties) {
    if (!isObject(penalties)) return [];
    var names = Object.keys(penalties).filter(function (name) {
      return isNumber(penalties[name]) && penalties[name] > 0;
    });
    var known = Object.keys(CRITERIA).filter(function (name) {
      return names.indexOf(name) !== -1;
    });
    var unknown = names.filter(function (name) {
      return !own(CRITERIA, name);
    });
    return known.concat(unknown).map(function (name) {
      return { text: api.criterionText(name), extent: api.extent(penalties[name]) };
    });
  };

  /* Blank lines separate paragraphs; a folded description is one. */
  api.paragraphs = function (text) {
    if (typeof text !== "string") return [];
    return text.split(/\n\s*\n/).map(function (part) {
      return part.replace(/\s+/g, " ").trim();
    }).filter(Boolean);
  };

  /* What tells one finished run from the next when no poll saw the phase in
   * between, such as a whole run played in another tab. */
  api.runIdentity = function (body) {
    var score = isObject(body.score) ? body.score.total : null;
    var actions = Array.isArray(body.actions) ? body.actions.length : null;
    return JSON.stringify([body.key, body.phase, body.outcome, body.elapsed_s, actions, score]);
  };

  /* The panel's view model from a result response, or null when the run is
   * not over (or the response is not a result), when nothing may be shown. */
  api.view = function (status, body) {
    if (!api.isFinished(status, body)) return null;
    var outcome = own(OUTCOMES, body.outcome) ? OUTCOMES[body.outcome] : UNKNOWN_OUTCOME;
    var scored = isObject(body.score) && isNumber(body.score.total);
    var timed = isNumber(body.elapsed_s) && isNumber(body.time_limit_s);
    return {
      identity: api.runIdentity(body),
      key: typeof body.key === "string" ? body.key : "",
      title: typeof body.title === "string" && body.title ? body.title : "Scenario",
      outcome: own(OUTCOMES, body.outcome) ? body.outcome : "unknown",
      outcomeText: outcome.text,
      glyph: outcome.glyph,
      time: timed ? "Run time " + Ribbon.formatClock(body.elapsed_s) + " of " + Ribbon.formatClock(body.time_limit_s) : "",
      scored: scored,
      total: scored ? String(Math.round(body.score.total)) : "",
      unscored: scored ? "" : body.outcome === "aborted" ? "Not scored, because the run was aborted." : "Not scored.",
      costs: scored ? api.costs(body.score.penalties) : [],
      debrief: api.paragraphs(body.debrief),
    };
  };

  api.retrySteps = function (key) {
    return [
      {
        url: LOAD_URL,
        init: { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenario: key }) },
      },
      { url: START_URL, init: { method: "POST" } },
    ];
  };

  api.refusalText = function (status) {
    if (status === 409) return "Another run has started since this one finished, so this one cannot be restarted from here.";
    if (status === 404) return "That scenario is not available any more. Choose another scenario.";
    if (status === 400) return "That scenario could not be set up, so nothing was changed.";
    if (status === 429) return "Too many requests. Wait a moment and try again.";
    if (status === 0 || status >= 500) return "The request did not finish. Check the run shown in the ribbon before trying again.";
    return "Something went wrong, so nothing was changed. Try again.";
  };

  /* browser glue */

  function el(doc, tag, className, text) {
    var made = doc.createElement(tag);
    if (className) made.setAttribute("class", className);
    if (text !== undefined) made.textContent = text;
    return made;
  }

  /* Builds the panel inside `doc`'s #debrief section. `options.fetch` exists
   * so a test can supply its own transport, and `options.timeoutMs` its own
   * deadline for each request. `options.onRetry(status, body, refusal)` is
   * handed the last response Try again had accepted, so the ribbon can adopt
   * it at once, with the words for a start that was then refused (the panel
   * closes on a loaded run, so they are shown elsewhere). `options.onRefusal(
   * text)` gets a refusal when nothing was accepted and the panel is no longer
   * showing, for the ribbon's notice, and `options.onResync()` is called
   * when a request did not finish, so the ribbon can read the run again.
   * Returns {result, open, close}: feed `result` every
   * (status, body) the ribbon's scenario poll applies. */
  function mount(doc, options) {
    var settings = options || {};
    var doFetch = settings.fetch || root.fetch.bind(root);
    var timeoutMs = settings.timeoutMs === undefined ? FETCH_TIMEOUT_MS : settings.timeoutMs;
    var panel = doc.getElementById("debrief");
    var button = doc.getElementById("scenario-result");
    var status = doc.getElementById("debrief-status");

    var head = el(doc, "div", "debrief-head");
    var heading = el(doc, "h2", "", "Run result");
    heading.setAttribute("id", "debrief-title");
    var subtitle = el(doc, "p", "debrief-sub");
    var close = el(doc, "button", "btn btn-quiet btn-icon", "\u2715");
    close.setAttribute("type", "button");
    close.setAttribute("aria-label", "Close result");
    head.appendChild(heading);
    head.appendChild(subtitle);
    head.appendChild(close);

    var body = el(doc, "div", "debrief-body");
    var summary = el(doc, "div", "debrief-summary");
    var outcome = el(doc, "p", "debrief-outcome");
    var glyph = el(doc, "span", "debrief-glyph");
    glyph.setAttribute("aria-hidden", "true");
    var outcomeText = el(doc, "span", "debrief-outcome-text");
    outcome.appendChild(glyph);
    outcome.appendChild(outcomeText);
    var time = el(doc, "p", "debrief-time");
    var score = el(doc, "p", "debrief-score");
    var costsHeading = el(doc, "h3", "debrief-label", "What counted against the score");
    var costs = el(doc, "ul", "debrief-costs");
    summary.appendChild(outcome);
    summary.appendChild(time);
    summary.appendChild(score);
    summary.appendChild(costsHeading);
    summary.appendChild(costs);
    var story = el(doc, "div", "debrief-story");
    story.appendChild(el(doc, "h3", "debrief-label", "Debrief"));
    var text = el(doc, "div", "debrief-text");
    story.appendChild(text);
    body.appendChild(summary);
    body.appendChild(story);

    var foot = el(doc, "div", "debrief-foot");
    var retry = el(doc, "button", "btn btn-primary");
    retry.setAttribute("type", "button");
    var retryGlyph = el(doc, "span", "btn-glyph", "\u25B6");
    retryGlyph.setAttribute("aria-hidden", "true");
    retry.appendChild(retryGlyph);
    retry.appendChild(doc.createTextNode("Try again"));
    var choose = el(doc, "a", "btn btn-quiet", "Choose another scenario");
    // The ribbon's own way back to the scenarios, so the URL has one source.
    var ribbonChoose = doc.getElementById("scenario-choose");
    choose.setAttribute("href", (ribbonChoose && ribbonChoose.getAttribute("href")) || "/");
    var notice = el(doc, "p", "debrief-notice");
    notice.setAttribute("role", "status");
    foot.appendChild(retry);
    foot.appendChild(choose);
    foot.appendChild(notice);

    panel.appendChild(head);
    panel.appendChild(body);
    panel.appendChild(foot);

    var shown = null;
    var opened = null;
    var opener = null;
    var busy = false;

    function setOpen(open) {
      if (!open && status) status.textContent = "";
      panel.hidden = !open;
      if (button) button.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function clear() {
      subtitle.textContent = "";
      outcome.setAttribute("data-outcome", "");
      glyph.textContent = "";
      outcomeText.textContent = "";
      time.textContent = "";
      score.textContent = "";
      costs.textContent = "";
      text.textContent = "";
      notice.textContent = "";
    }

    function draw(view) {
      clear();
      subtitle.textContent = view.title;
      outcome.setAttribute("data-outcome", view.outcome);
      glyph.textContent = view.glyph;
      outcomeText.textContent = view.outcomeText;
      time.textContent = view.time;
      if (view.scored) {
        score.appendChild(el(doc, "span", "debrief-score-label", "Score "));
        score.appendChild(el(doc, "span", "debrief-score-value", view.total));
        score.appendChild(el(doc, "span", "debrief-score-of", " out of 100"));
      } else {
        score.textContent = view.unscored;
      }
      // The server always names the run; without it there is nothing to load.
      retry.hidden = view.key === "";
      costsHeading.hidden = !view.scored;
      costs.hidden = !view.scored;
      if (!view.costs.length) costs.appendChild(el(doc, "li", "", "Nothing counted against the score."));
      view.costs.forEach(function (cost) {
        var item = el(doc, "li", "", cost.text);
        item.appendChild(el(doc, "span", "visually-hidden", ", "));
        item.appendChild(el(doc, "span", "debrief-extent", cost.extent));
        costs.appendChild(item);
      });
      (view.debrief.length ? view.debrief : ["There is no debrief for this scenario."]).forEach(function (words) {
        text.appendChild(el(doc, "p", "", words));
      });
    }

    function usable(target) {
      return target && target !== doc.body && !target.hidden && !target.disabled &&
        (target.isConnected === undefined || target.isConnected);
    }

    /* Focus moves into the panel when the operator asked for it or has none
     * to lose: on the body or in the ribbon's run controls. Someone typing in
     * a faceplate or working a trend window keeps focus, and the panel is
     * announced instead. */
    function takesFocus(active) {
      var bar = doc.getElementById("scenario-bar");
      return !active || active === doc.body || !!(bar && bar.contains && bar.contains(active));
    }

    function open(from) {
      if (shown === null) return;
      var active = from || doc.activeElement;
      if (!panel.hidden) return;
      setOpen(true);
      if (from || takesFocus(active)) {
        opener = active && !panel.contains(active) ? active : null;
        panel.focus();
      } else {
        opener = null;
        if (status) status.textContent = "Run finished: " + shown.outcomeText + ". The result is open above the plant.";
      }
    }

    /* Focus goes back to what opened the panel, or to the Result button when
     * that is gone or the panel opened itself. */
    function hide(refocus) {
      if (panel.hidden) return;
      var hadFocus = panel.contains(doc.activeElement);
      setOpen(false);
      if (!refocus || !hadFocus) return;
      var target = usable(opener) ? opener : button;
      if (usable(target)) target.focus();
      opener = null;
    }

    /* The panel went away under the keyboard (another tab loaded or started a
     * run), so focus goes to the first run control the ribbon now offers. */
    function focusRunControl() {
      var active = doc.activeElement;
      if (usable(active) && !panel.contains(active)) return;
      var offered = ["scenario-start", "scenario-abort", "scenario-choose"].map(function (id) {
        return doc.getElementById(id);
      }).filter(usable)[0];
      if (offered) offered.focus();
    }

    function result(status, body) {
      if (status !== 200 && status !== 409) return;
      var view = api.view(status, body);
      if (view === null) {
        var heldFocus = !panel.hidden && panel.contains(doc.activeElement);
        hide(false);
        // The ribbon renders the new phase before it calls this, but a later
        // caller might not, so the handoff runs after the caller's own
        // synchronous code.
        if (heldFocus) Promise.resolve().then(focusRunControl);
        shown = null;
        opened = null;
        clear();
        if (button) button.hidden = true;
        return;
      }
      if (shown === null || shown.identity !== view.identity) draw(view);
      shown = view;
      if (button) button.hidden = false;
      if (opened !== view.identity) {
        opened = view.identity;
        open(null);
      }
    }

    async function tryAgain() {
      if (busy || shown === null) return;
      busy = true;
      // A disabled button loses focus, so note it now and give it back.
      var held = doc.activeElement === retry;
      retry.disabled = true;
      notice.textContent = "";
      var accepted = null;
      var refusal = "";
      var ambiguous = false;
      try {
        var steps = api.retrySteps(shown.key);
        for (var i = 0; i < steps.length; i += 1) {
          var response = await Scenario.timedFetch(doFetch, steps[i], timeoutMs);
          if (!response.ok) {
            refusal = api.refusalText(response.status);
            // A proxy's 5xx does not prove the server refused the request.
            ambiguous = response.status >= 500;
            break;
          }
          accepted = response;
        }
      } catch (error) {
        refusal = api.refusalText(0);
        ambiguous = true;
      } finally {
        busy = false;
        retry.disabled = false;
        if (held && !panel.hidden) retry.focus();
      }
      notice.textContent = refusal;
      // A load that was accepted is a new run, even when its start was then
      // refused, so the console shows it. A refusal with nobody to read it
      // here (a poll took the run away meanwhile) goes to the ribbon's notice.
      if (accepted !== null) {
        if (settings.onRetry) settings.onRetry(accepted.status, accepted.body, refusal);
      } else if (refusal !== "" && panel.hidden && settings.onRefusal) {
        settings.onRefusal(refusal);
      }
      // A request that did not finish may still have been applied, so the
      // ribbon is asked to read the run again, after it has adopted any load
      // that was accepted. A 4xx refusal is definite and needs no read; a 5xx
      // or a request that did not finish is ambiguous.
      if (ambiguous && settings.onResync) settings.onResync();
    }

    close.addEventListener("click", function () { hide(true); });
    retry.addEventListener("click", tryAgain);
    panel.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      if (event.preventDefault) event.preventDefault();
      // Escape closes this panel only: the canvas's own listener would see the
      // focus handed back to a faceplate control and close that faceplate too.
      if (event.stopPropagation) event.stopPropagation();
      hide(true);
    });
    if (button) {
      button.addEventListener("click", function () {
        if (panel.hidden) open(button);
        else hide(true);
      });
    }

    setOpen(false);
    if (button) button.hidden = true;

    return { result: result, open: open, close: function () { hide(true); } };
  }

  api.mount = mount;

  if (node) module.exports = api;
  else root.Debrief = api;
})(typeof window !== "undefined" ? window : globalThis);
