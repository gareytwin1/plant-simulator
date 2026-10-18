/* How a simulated time reads on a page (T20-23).
 *
 * Free play shows the plant clock, hh:mm:ss. A scenario shows run time,
 * mm:ss from the moment the run began, as the ribbon's run clock does, so a
 * trainee sees one clock for one run. The run's start is `started_at` in
 * GET /api/scenario/result (the plant clock at run time zero, fixed for the
 * run): the page that holds it passes it here as `origin`, null in free play.
 * This file never learns about scenarios; it only shifts and formats.
 *
 * Pure, so it runs under Node; tests/test_run_clock.py drives it that way.
 */
(function (root) {
  "use strict";

  // A run time is a difference of two sums of steps, so 12 s can arrive as
  // 11.999999999; it must still read 00:12.
  var SLACK = 1e-6;

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  /* The plant clock, hh:mm:ss, never below zero. */
  function plant(seconds) {
    var whole = Math.max(0, Math.floor(seconds));
    return pad(Math.floor(whole / 3600)) + ":" + pad(Math.floor(whole / 60) % 60) + ":" + pad(whole % 60);
  }

  /* Run time, mm:ss with minutes unbounded. A time before the run began reads
   * as negative ("-00:05") rather than wrapping. */
  function run(seconds) {
    var whole = Math.floor(seconds + SLACK);
    var size = Math.abs(whole);
    return (whole < 0 ? "-" : "") + pad(Math.floor(size / 60)) + ":" + pad(size % 60);
  }

  /* `seconds` of plant clock as the page shows it: run time when `origin` is
   * a number, the plant clock otherwise. */
  function format(seconds, origin) {
    return typeof origin === "number" ? run(seconds - origin) : plant(seconds);
  }

  /* The origin a scenario result response sets: its `started_at`, or null
   * for free play (the result's 409, or an idle body). Undefined when the
   * response says nothing either way (a failure), so the caller keeps what
   * it had. */
  function originOf(status, body) {
    if (status === 409) return null;
    if (status !== 200 || body === null || typeof body !== "object") return undefined;
    if (body.phase === "idle") return null;
    return typeof body.started_at === "number" ? body.started_at : undefined;
  }

  /* The one origin a page holds, starting from `text` (the server's
   * data-started-at, "" in free play) and following each result response.
   * `result` answers whether the origin changed, so the caller redraws. */
  function follow(text) {
    var first = typeof text === "string" && text !== "" ? Number(text) : NaN;
    var origin = isFinite(first) ? first : null;
    return {
      get: function () {
        return origin;
      },
      result: function (status, body) {
        var next = originOf(status, body);
        if (next === undefined || next === origin) return false;
        origin = next;
        return true;
      },
    };
  }

  var api = { plant: plant, run: run, format: format, originOf: originOf, follow: follow };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SimClock = api;
})(typeof window !== "undefined" ? window : globalThis);
