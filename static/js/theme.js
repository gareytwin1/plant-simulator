/* Theme choice (T16-11): Auto follows the OS, Light and Dark override it.
 *
 * The choice is `data-theme` on <html>, which static/css/tokens.css turns into
 * a colour scheme; Auto removes the attribute. It is kept in localStorage,
 * which can be missing or throw (private windows, blocked site data), so every
 * access is guarded and the page works without it. Load this in <head> and call
 * Theme.apply(document) at once, so a saved choice paints on the first frame.
 * Everything above the "browser glue" marker is pure so it runs under Node;
 * tests/test_landing.py drives it that way.
 */
(function (root) {
  "use strict";

  var KEY = "plant_theme";
  var NEXT = { auto: "light", light: "dark", dark: "auto" };
  var LABEL = { auto: "Theme: Auto", light: "Theme: Light", dark: "Theme: Dark" };

  var api = {};

  api.normalise = function (value) {
    return Object.prototype.hasOwnProperty.call(NEXT, value) ? value : "auto";
  };

  api.next = function (choice) {
    return NEXT[api.normalise(choice)];
  };

  api.label = function (choice) {
    return LABEL[api.normalise(choice)];
  };

  api.read = function (storage) {
    try {
      return api.normalise(storage.getItem(KEY));
    } catch (error) {
      return "auto";
    }
  };

  api.write = function (storage, choice) {
    try {
      storage.setItem(KEY, choice);
    } catch (error) {
      // The choice still applies for this page view.
    }
  };

  api.paint = function (doc, choice) {
    if (choice === "auto") doc.documentElement.removeAttribute("data-theme");
    else doc.documentElement.setAttribute("data-theme", choice);
  };

  /* browser glue */

  function storageOf(win) {
    try {
      return win.localStorage;
    } catch (error) {
      return null;
    }
  }

  api.apply = function (doc, win) {
    var choice = api.read(storageOf(win || root));
    api.paint(doc, choice);
    return choice;
  };

  api.mount = function (doc, win) {
    var storage = storageOf(win || root);
    var button = doc.getElementById("theme-toggle");
    var choice = api.read(storage);

    if (!button) return;

    function show() {
      button.textContent = api.label(choice);
      button.setAttribute("aria-label", api.label(choice) + ". Activate to change.");
    }

    show();
    button.hidden = false;
    button.addEventListener("click", function () {
      choice = api.next(choice);
      api.paint(doc, choice);
      api.write(storage, choice);
      show();
    });
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Theme = api;
})(typeof window !== "undefined" ? window : globalThis);
