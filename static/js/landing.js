/* Landing page (T16-11, T20-9): one selected scenario with a START that loads
 * and starts it, free play, and a way back to a run in progress. The Recent
 * scenarios card lists this browser's finished runs and best scores from
 * GET /api/history (T20-16), which names a scenario only by title and key.
 *
 * Talks to the scenario API (app/api/scenario.py). A refusal is shown in this
 * file's own plain words, chosen by status code, and never the server's text:
 * a scenario's id and its fault must not reach the page through an error.
 * The page names a scenario only by its catalogue key.
 * Everything above the "browser glue" marker is pure so it runs under Node
 * without a DOM; tests/test_landing.py drives it that way.
 */
(function (root) {
  "use strict";

  var LOAD_URL = "/api/scenario/load";
  var START_URL = "/api/scenario/start";
  var ABORT_URL = "/api/scenario/abort";
  var UNLOAD_URL = "/api/scenario/unload";
  var CONSOLE_URL = "/console";
  var HISTORY_URL = "/api/history";
  var STORAGE_KEY = "landing.scenario";
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var OUTCOMES = { succeeded: "Succeeded", failed: "Failed", timed_out: "Timed out" };

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

  api.buildStartRequest = function () {
    return { url: START_URL, init: { method: "POST" } };
  };

  api.buildAbortRequest = function () {
    return { url: ABORT_URL, init: { method: "POST" } };
  };

  api.buildUnloadRequest = function () {
    return { url: UNLOAD_URL, init: { method: "POST" } };
  };

  /* The requests, in order, that put the session where the trainee asked.
   * A run in progress must be aborted first (load and unload are a 409 while
   * one runs), and that is only ever done once the trainee has confirmed. */
  api.startSteps = function (scenarioKey, abortFirst) {
    var steps = [api.buildLoadRequest(scenarioKey), api.buildStartRequest()];
    return abortFirst ? [api.buildAbortRequest()].concat(steps) : steps;
  };

  api.freePlaySteps = function (abortFirst) {
    var steps = [api.buildUnloadRequest()];
    return abortFirst ? [api.buildAbortRequest()].concat(steps) : steps;
  };

  /* Whether a request must wait for the trainee to agree to abort first. */
  api.needsConfirmation = function (phase) {
    return phase === "running";
  };

  /* `labels` is the server's phase-to-text map, sent with the page so the text
   * has one source. */
  api.phaseLabel = function (labels, phase) {
    return Object.prototype.hasOwnProperty.call(labels, phase) ? labels[phase] : "";
  };

  /* The remembered choice, or the first entry. Storage can be missing or
   * throw (private window, blocked site data), and a stored key the catalogue
   * no longer has is ignored. */
  api.chooseScenario = function (storage, keys) {
    var stored = null;
    try {
      stored = storage.getItem(STORAGE_KEY);
    } catch (error) {
      stored = null;
    }
    return keys.indexOf(stored) >= 0 ? stored : keys[0];
  };

  api.rememberScenario = function (storage, key) {
    try {
      storage.setItem(STORAGE_KEY, key);
    } catch (error) {
      /* The choice just is not remembered. */
    }
  };

  /* A run's outcome as a word; a run stored before outcomes were kept has none. */
  api.outcomeLabel = function (outcome) {
    return Object.prototype.hasOwnProperty.call(OUTCOMES, outcome) ? OUTCOMES[outcome] : "Outcome not recorded";
  };

  api.formatScore = function (total) {
    return String(Math.round(total));
  };

  /* `recordedAt` is seconds since the epoch, shown in the browser's own time zone. */
  api.formatWhen = function (recordedAt) {
    var when = new Date(recordedAt * 1000);
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    return when.getDate() + " " + MONTHS[when.getMonth()] + " " + when.getFullYear() + ", " +
      pad(when.getHours()) + ":" + pad(when.getMinutes());
  };

  function isObject(value) {
    return value !== null && typeof value === "object";
  }

  function isNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function isBest(best) {
    return isObject(best) && typeof best.title === "string" && isNumber(best.total);
  }

  function isRun(run) {
    return isBest(run) && typeof run.difficulty === "string" && isNumber(run.recorded_at) &&
      isFinite(new Date(run.recorded_at * 1000).getTime());
  }

  /* What the Recent scenarios card shows of a GET /api/history body: the
   * recent runs as the server ordered them (newest first) and the bests by
   * title. Anything not shaped like a history, as a whole or one entry at a
   * time, shows as no history, so one bad row never hides the good ones. */
  api.historyView = function (body) {
    var recent = isObject(body) && Array.isArray(body.recent) ? body.recent : [];
    var best = isObject(body) && isObject(body.best) ? body.best : {};
    return {
      recent: recent.filter(isRun).map(function (run) {
        return {
          title: run.title,
          difficulty: run.difficulty,
          outcome: api.outcomeLabel(run.outcome),
          outcomeKey: Object.prototype.hasOwnProperty.call(OUTCOMES, run.outcome) ? run.outcome : "unknown",
          score: api.formatScore(run.total),
          when: api.formatWhen(run.recorded_at),
          iso: new Date(run.recorded_at * 1000).toISOString(),
        };
      }),
      best: Object.keys(best).filter(function (key) { return isBest(best[key]); }).map(function (key) {
        return { title: best[key].title, score: api.formatScore(best[key].total) };
      }).sort(function (a, b) { return a.title < b.title ? -1 : a.title > b.title ? 1 : 0; }),
    };
  };

  api.HISTORY_URL = HISTORY_URL;

  /* browser glue */

  function pageStorage() {
    try {
      return root.localStorage || null;
    } catch (error) {
      return null;
    }
  }

  /* `navigate` exists so a test can see where the page goes; the page itself
   * leaves for the console once its calls succeed. `options.storage` and
   * `options.refreshRibbon` exist for the same reason. */
  function mount(doc, fetchImpl, navigate, options) {
    var settings = options || {};
    var doFetch = fetchImpl || root.fetch.bind(root);
    var goTo = navigate || function (url) {
      if (root.location) root.location.assign(url);
    };
    var storage = settings.storage === undefined ? pageStorage() : settings.storage;
    var refreshRibbon = settings.refreshRibbon || function () {
      if (root.pageRibbon && root.pageRibbon.poll) root.pageRibbon.poll();
    };

    var launch = doc.getElementById("launch");
    var labels = JSON.parse(launch.getAttribute("data-phase-labels"));
    var phase = launch.getAttribute("data-phase");
    var strip = doc.getElementById("standing");
    var phaseEl = doc.getElementById("standing-phase");
    var noticeEl = doc.getElementById("notice");
    var buttons = Array.prototype.slice.call(doc.querySelectorAll("main button"));
    var freePlay = doc.getElementById("free-play");

    var start = doc.getElementById("hero-start");
    var choose = doc.getElementById("choose-another");
    var picker = doc.getElementById("scenario-picker");
    var actions = doc.getElementById("hero-actions");
    var confirmBox = doc.getElementById("abort-confirm");
    var confirmYes = doc.getElementById("abort-confirm-yes");
    var confirmNo = doc.getElementById("abort-confirm-no");
    var choices = Array.prototype.slice.call(doc.querySelectorAll(".scenario-option"));

    var pending = null;
    var opener = null;

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

    function standing(body) {
      if (!body || typeof body.phase !== "string") return;
      phase = body.phase;
      if (phaseEl) phaseEl.textContent = api.phaseLabel(labels, phase);
      if (strip && phase === "idle") strip.hidden = true;
    }

    /* Runs the requests in order and goes to the console only if every one
     * was accepted. The first refusal stops the sequence and is worded by its
     * status code; the ribbon is asked to catch up with whatever did change. */
    async function run(steps) {
      setBusy(true);
      try {
        for (var i = 0; i < steps.length; i += 1) {
          var response = await doFetch(steps[i].url, steps[i].init);
          /* Another tab or the console already ended the run: the abort has
           * nothing left to do, so carry on. */
          if (response.status === 409 && steps[i].url === ABORT_URL) continue;
          if (!response.ok) {
            /* Any other 409 means a run is going that this page did not
             * know of, so the next click asks before aborting it. */
            if (response.status === 409) phase = "running";
            notice(api.refusalText(response.status), "refused");
            refreshRibbon();
            return;
          }
          standing(await response.json());
        }
        goTo(CONSOLE_URL);
      } catch (error) {
        notice(api.refusalText(0), "refused");
        refreshRibbon();
      } finally {
        setBusy(false);
      }
    }

    function showConfirm(stepsFor, from) {
      pending = stepsFor;
      opener = from;
      if (actions) actions.hidden = true;
      confirmBox.hidden = false;
      confirmNo.focus();
    }

    function hideConfirm() {
      pending = null;
      confirmBox.hidden = true;
      if (actions) actions.hidden = false;
      if (opener) opener.focus();
      opener = null;
    }

    /* A request that would abort a run asks first, in the page. */
    function request(stepsFor, from) {
      if (api.needsConfirmation(phase)) showConfirm(stepsFor, from);
      else run(stepsFor(false));
    }

    function selectedKey() {
      return start.getAttribute("data-scenario");
    }

    var thumbnail = thumbnailFrame(doc, doFetch);

    function select(key) {
      choices.forEach(function (option) {
        var chosen = option.getAttribute("data-scenario") === key;
        option.setAttribute("aria-pressed", chosen ? "true" : "false");
        if (!chosen) return;
        thumbnail.show(option.getAttribute("data-thumbnail"));
        var difficulty = option.getAttribute("data-difficulty");
        var chip = doc.getElementById("hero-difficulty");
        doc.getElementById("hero-title").textContent = option.getAttribute("data-title");
        doc.getElementById("hero-briefing").textContent = option.getAttribute("data-briefing");
        doc.getElementById("hero-limit").textContent = "Time limit " + option.getAttribute("data-limit") + " min";
        chip.setAttribute("data-difficulty", difficulty);
        chip.textContent = difficulty.charAt(0).toUpperCase() + difficulty.slice(1);
      });
      start.setAttribute("data-scenario", key);
    }

    function setPicker(open) {
      picker.hidden = !open;
      choose.setAttribute("aria-expanded", open ? "true" : "false");
    }

    if (choices.length) {
      select(api.chooseScenario(storage, choices.map(function (o) { return o.getAttribute("data-scenario"); })));

      start.addEventListener("click", function () {
        request(function (abortFirst) { return api.startSteps(selectedKey(), abortFirst); }, start);
      });

      choose.addEventListener("click", function () {
        setPicker(picker.hidden);
      });

      choices.forEach(function (option) {
        option.addEventListener("click", function () {
          var key = option.getAttribute("data-scenario");
          select(key);
          api.rememberScenario(storage, key);
          setPicker(false);
          start.focus();
        });
      });
    }

    confirmYes.addEventListener("click", function () {
      var stepsFor = pending;
      hideConfirm();
      run(stepsFor(true));
    });

    confirmNo.addEventListener("click", hideConfirm);

    confirmBox.addEventListener("keydown", function (event) {
      if (event.key === "Escape") hideConfirm();
    });

    freePlay.addEventListener("click", function () {
      request(api.freePlaySteps, freePlay);
    });

    mountHistory(doc, doFetch);
  }

  function span(doc, className, text) {
    var node = doc.createElement("span");
    node.setAttribute("class", className);
    node.textContent = text;
    return node;
  }

  /* Rows are built as nodes with textContent, never markup, so a title is
   * only ever text. */
  function runRow(doc, run) {
    var row = doc.createElement("li");
    row.setAttribute("class", "run-row");
    row.appendChild(span(doc, "run-title", run.title));
    row.appendChild(span(doc, "run-score", "Score " + run.score));
    var meta = span(doc, "run-meta", "");
    var outcome = span(doc, "run-outcome", run.outcome);
    outcome.setAttribute("data-outcome", run.outcomeKey);
    meta.appendChild(outcome);
    meta.appendChild(span(doc, "run-difficulty", run.difficulty.charAt(0).toUpperCase() + run.difficulty.slice(1)));
    row.appendChild(meta);
    var when = doc.createElement("time");
    when.setAttribute("class", "run-when");
    when.setAttribute("datetime", run.iso);
    when.textContent = run.when;
    row.appendChild(when);
    return row;
  }

  function bestRow(doc, best) {
    var row = doc.createElement("li");
    row.setAttribute("class", "best-row");
    row.appendChild(span(doc, "best-title", best.title));
    row.appendChild(span(doc, "run-score", best.score));
    return row;
  }

  /* The card starts with nothing shown: the runs, or the empty state, once
   * the history is read; a history that cannot be read says so. */
  async function mountHistory(doc, doFetch) {
    var runs = doc.getElementById("recent-runs");
    var bests = doc.getElementById("best-runs");
    var empty = doc.getElementById("recent-empty");
    var bestSection = doc.getElementById("best-section");
    if (!runs || !bests || !empty || !bestSection) return;
    var view;
    try {
      var response = await doFetch(HISTORY_URL);
      if (!response.ok) throw new Error("history " + response.status);
      view = api.historyView(await response.json());
    } catch (error) {
      empty.textContent = "Your run history could not be read. Reload the page to try again.";
      empty.hidden = false;
      return;
    }
    view.recent.forEach(function (run) { runs.appendChild(runRow(doc, run)); });
    view.best.forEach(function (best) { bests.appendChild(bestRow(doc, best)); });
    runs.hidden = view.recent.length === 0;
    bestSection.hidden = view.best.length === 0;
    empty.hidden = view.recent.length > 0;
  }

  /* The graphic of the plant the selected scenario runs on, inert and with
   * its live readouts hidden by the stylesheet. Each graphic is fetched once
   * and only the latest selection is drawn; the old one stays until the new
   * one arrives, so the hero does not jump. A scenario with no graphic, or
   * one that cannot be fetched, hides the frame, and choosing it again tries
   * again. */
  function thumbnailFrame(doc, doFetch) {
    var frame = doc.getElementById("plant-thumb");
    var figure = frame && frame.closest ? frame.closest(".hero-art") : null;
    var shown = null;
    var cache = {};

    function fetchGraphic(url) {
      if (!cache[url]) {
        cache[url] = Promise.resolve(doFetch(url)).then(function (response) {
          if (!response.ok) throw new Error("graphic " + response.status);
          /* Decoration only: ids are dropped so none can collide with the page's. */
          return response.text().then(function (text) {
            return text.replace(/\sid="[^"]*"/g, "");
          });
        });
        cache[url].catch(function () {
          delete cache[url];
        });
      }
      return cache[url];
    }

    function empty() {
      frame.innerHTML = "";
      if (figure) figure.hidden = true;
    }

    return {
      show: function (url) {
        if (!frame || url === shown) return;
        shown = url;
        if (!url) {
          empty();
          return;
        }
        fetchGraphic(url).then(function (markup) {
          if (shown !== url) return;
          frame.innerHTML = markup;
          if (figure) figure.hidden = false;
        }, function () {
          if (shown !== url) return;
          shown = null;
          empty();
        });
      },
    };
  }

  api.mount = mount;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Landing = api;
})(typeof window !== "undefined" ? window : globalThis);
