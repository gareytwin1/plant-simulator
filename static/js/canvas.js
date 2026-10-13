/* Canvas selection (T20-10; trends from T20-11).
 *
 * The process graphic is the console's canvas. This file makes what is drawn
 * on it selectable and owns the one faceplate that is open at a time: which
 * item is selected, where its panel sits and when it closes. What is in the
 * panel is faceplate.js's.
 *
 *   a device (a .eq group with data-tag) and a loop's balloon (a .ctl group
 *   with data-controller) are buttons: focusable, named, opened by click,
 *   Enter or Space. A reading (a data-bind text) outside any device group is
 *   a button to its trend window when the trend listing publishes its point
 *   and the page has trend windows (trendwindows.js); without them, one a
 *   device lists is a button to that device's faceplate, and any other is
 *   inert. A reading inside a device is already part of its button; its trend
 *   opens from the faceplate.
 *
 * Which items are selectable, and what each one is, comes from GET /api/plant
 * (T20-2), never from a tag in this file: a tag the plant does not describe is
 * drawn but is not a button. graphic.js remounts the SVG when the plant
 * changes, so the buttons are re-marked whenever the SVG in the canvas does
 * not carry the current plant's id, and a different plant closes the panel.
 *
 * A panel is closed by its close button, by Escape and by a click on empty
 * canvas; the first two return focus to the item that opened it. It sits
 * beside what it describes and never covers it: `place` is the pure rule (a
 * port of the prototype's placeBeside), run over rectangles so it is tested
 * under Node without a layout engine.
 *
 * Selection and hover are tints (console.css); the ring is keyboard focus
 * only. Everything above the "browser glue" marker is pure.
 */
(function (root) {
  "use strict";

  var PLANT_URL = "/api/plant";
  var LISTING_URL = "/api/trend/points";
  var SVG_NS = "http://www.w3.org/2000/svg";

  var GAP = 14; // between a panel and what it sits beside
  var MARGIN = 8; // between a panel and the workspace edge
  var OTHER_WEIGHT = 1; // drawn items: covered only if nothing better is free
  var FALLBACK_SIZE = { w: 330, h: 260 };
  var LOCATE_MS = 1600; // how long Locate tints the mark it points at

  function isObject(value) {
    return value !== null && typeof value === "object";
  }

  function clamp(value, lo, hi) {
    return Math.min(hi, Math.max(lo, value));
  }

  /* ---- placement ------------------------------------------------------- */

  /* Where a panel goes: beside `anchor` when it fits there, else wherever it
   * covers least. Rectangles are {left, top, right, bottom} in workspace
   * coordinates. The request is
   *   size    {w, h} of the panel
   *   bounds  {width, height} of the workspace
   *   anchor  the rect it must not sit on, or null
   *   avoid   [{rect, weight}] - things to stay clear of, such as open trend
   *           windows; the panel also tries to sit beside each of them
   *   others  [rect] - everything else drawn, covered only if nothing is free
   *   besideOnly  true to try only the four sides of the anchor, so a panel
   *           that cannot find a clear side takes the least covered one
   *           rather than jumping across the canvas (a faceplate); without it
   *           the panel may also sit beside the `avoid` rects or in a corner
   * and the answer {x, y} is the top left corner. Covering the anchor
   * outranks everything: a panel never sits on it while any spot clear of it
   * exists, however much else that spot covers. */
  function place(request) {
    var w = request.size.w;
    var h = request.size.h;
    var width = request.bounds.width;
    var height = request.bounds.height;
    var anchor = request.anchor || null;
    var avoid = Array.isArray(request.avoid) ? request.avoid : [];
    var others = Array.isArray(request.others) ? request.others : [];

    var weighed = avoid.map(function (item) {
      return { rect: item.rect, weight: item.weight };
    });
    others.forEach(function (rect) {
      weighed.push({ rect: rect, weight: OTHER_WEIGHT });
    });

    // Right, left, below, above: the order is also the tie-break.
    function around(r) {
      var cx = (r.left + r.right) / 2;
      var cy = (r.top + r.bottom) / 2;
      return [
        { x: r.right + GAP, y: cy - h / 2 },
        { x: r.left - GAP - w, y: cy - h / 2 },
        { x: cx - w / 2, y: r.bottom + GAP },
        { x: cx - w / 2, y: r.top - GAP - h },
      ];
    }

    var candidates = anchor ? around(anchor) : [{ x: width / 2 - w / 2, y: height / 2 - h / 2 }];
    if (!(request.besideOnly && anchor)) {
      avoid.forEach(function (item) {
        candidates = candidates.concat(around(item.rect));
      });
      candidates = candidates.concat([
        { x: MARGIN, y: MARGIN },
        { x: width - w - MARGIN, y: MARGIN },
        { x: MARGIN, y: height - h - MARGIN },
        { x: width - w - MARGIN, y: height - h - MARGIN },
      ]);
    }

    candidates = candidates.map(function (c) {
      return {
        x: clamp(c.x, MARGIN, Math.max(MARGIN, width - w - MARGIN)),
        y: clamp(c.y, MARGIN, Math.max(MARGIN, height - h - MARGIN)),
      };
    });

    function overlap(c, r) {
      var dx = Math.max(0, Math.min(c.x + w, r.right) - Math.max(c.x, r.left));
      var dy = Math.max(0, Math.min(c.y + h, r.bottom) - Math.max(c.y, r.top));
      return dx * dy;
    }

    var best = candidates[0];
    var bestOnAnchor = Infinity;
    var bestCost = Infinity;
    candidates.forEach(function (c, index) {
      var onAnchor = anchor ? overlap(c, anchor) : 0;
      var covered = weighed.reduce(function (sum, item) {
        return sum + item.weight * overlap(c, item.rect);
      }, 0);
      // Equal cover goes to the earlier, closer candidate.
      var cost = covered + index;
      if (onAnchor < bestOnAnchor || (onAnchor === bestOnAnchor && cost < bestCost)) {
        best = c;
        bestOnAnchor = onAnchor;
        bestCost = cost;
      }
    });
    return { x: best.x, y: best.y };
  }

  /* ---- what the plant says --------------------------------------------- */

  /* A GET /api/plant body as {id, equipment, controllers, interlocks}, or null
   * when it names no plant. */
  function descriptionOf(body) {
    if (!isObject(body) || typeof body.plant !== "string") return null;
    return {
      id: body.plant,
      equipment: isObject(body.equipment) ? body.equipment : {},
      controllers: isObject(body.controllers) ? body.controllers : {},
      interlocks: isObject(body.interlocks) ? body.interlocks : {},
    };
  }

  /* The trend listing's points, words and units, all empty when it is
   * missing. */
  function vocabOf(listing) {
    var l = isObject(listing) ? listing : {};
    return {
      points: Array.isArray(l.points) ? l.points : [],
      descriptors: isObject(l.descriptors) ? l.descriptors : {},
      units: isObject(l.units) ? l.units : {},
    };
  }

  /* The device that lists the point a data-bind path ("section.id.field")
   * reads, or null when none does. */
  function ownerOf(plant, path) {
    var parts = String(path).split(".");
    if (parts.length !== 3 || !plant) return null;
    var point = parts[1] + "." + parts[2];
    var tags = Object.keys(plant.equipment);
    for (var i = 0; i < tags.length; i++) {
      var points = plant.equipment[tags[i]] && plant.equipment[tags[i]].points;
      if (Array.isArray(points) && points.indexOf(point) >= 0) return tags[i];
    }
    return null;
  }

  var api = {
    place: place,
    descriptionOf: descriptionOf,
    vocabOf: vocabOf,
    ownerOf: ownerOf,
  };

  /* ---- browser glue ---------------------------------------------------- */

  function has(node, name) {
    return node.getAttribute(name) !== null;
  }

  function hasClass(node, name) {
    return (" " + (node.getAttribute("class") || "") + " ").indexOf(" " + name + " ") >= 0;
  }

  function inside(ancestor, node) {
    for (var n = node; n; n = n.parentNode) {
      if (n === ancestor) return true;
    }
    return false;
  }

  function list(parent, selector) {
    return Array.prototype.slice.call(parent.querySelectorAll(selector));
  }

  /* Make the shown plant's graphic selectable and operate its one faceplate.
   * `options`:
   *   canvas      the element graphic.js mounts the SVG into
   *   workspace   the element around it, where clicks and the panel live
   *   layer       the element the panel is appended to (inside the workspace)
   *   fetch       a transport, for a test
   *   faceplates  faceplate.js's exports (default: the page's Faceplates)
   *   obstacles   function returning [{rect, weight}] the panel stays clear of,
   *               in workspace coordinates (the open trend windows)
   *   trends      the trend windows: {open(point, opener, anchor), isOpen(point)};
   *               without them no reading opens a trend
   * Returns {update, refresh, select, close, selected, panelRect, mark,
   * locate, showTrend}: call update with every snapshot, refresh when the shown
   * plant may have changed, and mark when a trend window opens or closes.
   * locate and showTrend act for the alarm overlay on a tag and, when the
   * alarm names one, its point (`<tag>.<variable>`). */
  function mount(options) {
    var settings = options || {};
    var canvas = settings.canvas;
    var workspace = settings.workspace;
    var layer = settings.layer;
    var doc = canvas.ownerDocument;
    var doFetch = settings.fetch || root.fetch.bind(root);
    var F = settings.faceplates || root.Faceplates;
    var trends = settings.trends || null;
    var post = F.makePost(doFetch);

    var plant = null;
    var vocab = vocabOf(null);
    var latest = null;
    var loading = false;
    var ticket = 0;
    var selection = null; // {kind, tag, opener, panel, size}

    function getJson(url) {
      return doFetch(url).then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      });
    }

    function svg() {
      return canvas.querySelector("svg");
    }

    /* The element that stands for kind/tag on the canvas, not a reading of it. */
    function subjectElement(kind, tag) {
      var root_ = svg();
      if (!root_) return null;
      var found = list(root_, "[data-canvas-tag]").filter(function (node) {
        return node.getAttribute("data-canvas-kind") === kind && node.getAttribute("data-canvas-tag") === tag && !has(node, "data-canvas-reading");
      });
      return found[0] || null;
    }

    function describe(kind, tag) {
      if (!plant) return null;
      var table = kind === "loop" ? plant.controllers : plant.equipment;
      return Object.prototype.hasOwnProperty.call(table, tag) ? table[tag] : null;
    }

    /* ---- marking the graphic ---- */

    function inGroup(node, group) {
      for (var n = node.parentNode; n && n !== group; n = n.parentNode) {
        if (n.getAttribute && (hasClass(n, "eq") || hasClass(n, "ctl"))) return true;
      }
      return false;
    }

    function mark(node, kind, tag, label, reading) {
      node.setAttribute("tabindex", "0");
      node.setAttribute("role", "button");
      node.setAttribute("aria-label", label);
      node.setAttribute("data-canvas-kind", kind);
      node.setAttribute("data-canvas-tag", tag);
      if (reading) node.setAttribute("data-canvas-reading", "");
    }

    function decorate() {
      var drawing = svg();
      if (!plant || !drawing || drawing.getAttribute("data-canvas") === plant.id) return;
      drawing.setAttribute("data-canvas", plant.id);

      list(drawing, "[data-tag]").forEach(function (group) {
        var tag = group.getAttribute("data-tag");
        var info = describe("device", tag);
        if (!hasClass(group, "eq") || !info) return;
        var service = typeof info.service === "string" && info.service ? ", " + info.service : "";
        mark(group, "device", tag, tag + " " + (info.kind || "") + service + ". Open faceplate.", false);
        if (list(group, ".eq-hit").length) return;
        var hit = doc.createElementNS(SVG_NS, "rect");
        hit.setAttribute("class", "eq-hit");
        group.insertBefore(hit, group.firstChild);
      });

      list(drawing, "[data-controller]").forEach(function (group) {
        var tag = group.getAttribute("data-controller");
        var info = describe("loop", tag);
        if (!hasClass(group, "ctl") || !info) return;
        var service = typeof info.service === "string" && info.service ? ", " + info.service : "";
        mark(group, "loop", tag, tag + " controller" + service + ". Open faceplate.", false);
      });

      list(drawing, "[data-bind]").forEach(function (reading) {
        if (inGroup(reading, drawing)) return;
        var point = reading.getAttribute("data-bind").split(".").slice(1).join(".");
        if (trends && vocab.points.indexOf(point) >= 0) {
          mark(reading, "trend", point, trendLabel(point), true);
          return;
        }
        var owner = ownerOf(plant, reading.getAttribute("data-bind"));
        if (owner === null) return;
        mark(reading, "device", owner, owner + " " + F.readingLabel(point, vocab).toLowerCase() + ". Open " + owner + " faceplate.", true);
      });
    }

    /* What stands for an alarm on the canvas: the reading of its point if one
     * is drawn, else its device or loop, else any reading of its tag (a node
     * has no item of its own). Null when the graphic shows none of them. */
    function findMark(tag, point) {
      var drawing = svg();
      if (!drawing) return null;
      var readings = list(drawing, "[data-canvas-reading]").filter(function (node) {
        return node.getAttribute("data-canvas-kind") === "trend";
      });
      var exact = point ? readings.filter(function (node) {
        return node.getAttribute("data-canvas-tag") === point;
      })[0] : null;
      if (exact) return exact;
      var own = subjectElement("device", tag) || subjectElement("loop", tag);
      if (own) return own;
      return readings.filter(function (node) {
        return node.getAttribute("data-canvas-tag").indexOf(tag + ".") === 0;
      })[0] || null;
    }

    var located = null; // {node, timer}

    function unlocate() {
      if (!located) return;
      root.clearTimeout(located.timer);
      located.node.removeAttribute("data-located");
      located = null;
    }

    /* Point at an alarm's tag: tint its mark for a moment and put focus on it.
     * Opens nothing. Returns false when the graphic does not show it. */
    function locate(tag, point) {
      var node = findMark(tag, point);
      unlocate();
      if (!node) return false;
      node.setAttribute("data-located", "true");
      located = { node: node, timer: root.setTimeout(unlocate, LOCATE_MS) };
      if (typeof node.focus === "function") node.focus();
      return true;
    }

    /* Open an alarm's point in a trend window beside its mark. False when the
     * point is not one the trend listing offers, or the window did not open. */
    function showTrend(tag, point) {
      if (!trends || !point || vocab.points.indexOf(point) < 0) return false;
      var node = findMark(tag, point);
      var at = node ? rectIn(node) : null;
      return trends.open(point, node, sized(at) ? at : null);
    }

    /* A trend reading's name is fixed: a name that followed the value would be
     * announced again with every snapshot. */
    function trendLabel(point) {
      var id = point.slice(0, point.lastIndexOf("."));
      return id + " " + F.readingLabel(point, vocab).toLowerCase() + ". Open trend.";
    }

    /* The hit area behind each device follows what is drawn in the group, so
     * it stays a snug box as readings change length. */
    function refreshHits() {
      var drawing = svg();
      if (!drawing) return;
      list(drawing, "[data-canvas-tag]").forEach(function (group) {
        var hit = list(group, ".eq-hit")[0];
        if (!hit || typeof group.getBBox !== "function") return;
        hit.setAttribute("width", "0");
        hit.setAttribute("height", "0");
        var box;
        try {
          box = group.getBBox();
        } catch (error) {
          return; // not rendered (display:none): the next snapshot tries again
        }
        if (!box.width) return;
        hit.setAttribute("x", String(box.x - 6));
        hit.setAttribute("y", String(box.y - 6));
        hit.setAttribute("width", String(box.width + 12));
        hit.setAttribute("height", String(box.height + 12));
      });
    }

    function markSelected() {
      var drawing = svg();
      if (!drawing) return;
      list(drawing, "[data-canvas-tag]").forEach(function (node) {
        var reading = has(node, "data-canvas-reading");
        var kind = node.getAttribute("data-canvas-kind");
        var chosen;
        if (kind === "trend") {
          // A trend reading is marked while its window is open.
          chosen = trends.isOpen(node.getAttribute("data-canvas-tag"));
        } else {
          chosen =
            selection !== null &&
            (reading ? node === selection.opener : kind === selection.kind && node.getAttribute("data-canvas-tag") === selection.tag);
        }
        node.setAttribute("data-selected", chosen ? "true" : "false");
      });
    }

    /* ---- placement ---- */

    function rectIn(node) {
      if (!node || typeof node.getBoundingClientRect !== "function") return null;
      var r = node.getBoundingClientRect();
      var ws = workspace.getBoundingClientRect();
      return { left: r.left - ws.left, right: r.right - ws.left, top: r.top - ws.top, bottom: r.bottom - ws.top };
    }

    // A mark with no size (an empty text, a hidden element) says nothing.
    function sized(r) {
      return r !== null && (r.right > r.left || r.bottom > r.top);
    }

    /* The rectangle the panel must not cover: the item's own mark, and for a
     * loop its balloon together with the mode drawn beside it. */
    function anchorRect() {
      var node = selection.opener;
      if (!node || !inside(canvas, node)) node = subjectElement(selection.kind, selection.tag);
      if (!node) return null;
      var parts = has(node, "data-canvas-reading")
        ? [node]
        : list(node, selection.kind === "loop" ? ".ctl-balloon, [data-controller-mode]" : ".eq-hit");
      var rects = (parts.length ? parts : [node]).map(rectIn).filter(sized);
      if (!rects.length) return null;
      return rects.reduce(function (all, r) {
        return {
          left: Math.min(all.left, r.left),
          top: Math.min(all.top, r.top),
          right: Math.max(all.right, r.right),
          bottom: Math.max(all.bottom, r.bottom),
        };
      });
    }

    function reposition() {
      if (!selection) return;
      var element = selection.panel.element;
      var ws = workspace.getBoundingClientRect();
      var size = { w: element.offsetWidth || FALLBACK_SIZE.w, h: element.offsetHeight || FALLBACK_SIZE.h };
      var anchor = anchorRect();
      var drawing = svg();
      var others = [];
      if (drawing) {
        list(drawing, "[data-canvas-tag]").forEach(function (node) {
          if (has(node, "data-canvas-reading")) return;
          list(node, ".eq-hit, .ctl-balloon, [data-controller-mode]").forEach(function (box) {
            others.push(rectIn(box));
          });
        });
      }
      var pos = place({
        size: size,
        bounds: { width: ws.width, height: ws.height },
        anchor: anchor,
        avoid: settings.obstacles ? settings.obstacles() : [],
        others: others.filter(sized),
        besideOnly: true,
      });
      element.style.left = pos.x + "px";
      element.style.top = pos.y + "px";
      selection.size = size.w + "x" + size.h;
    }

    /* ---- selection ---- */

    function close(restoreFocus) {
      if (!selection) return;
      var closing = selection;
      selection = null;
      layer.textContent = "";
      markSelected();
      if (!restoreFocus) return;
      var target = closing.opener && inside(canvas, closing.opener) ? closing.opener : subjectElement(closing.kind, closing.tag);
      if (target && typeof target.focus === "function") target.focus();
    }

    /* Open the faceplate of `kind` ("device" or "loop") `tag`; `opener` is the
     * element focus returns to (default: the item itself). Selecting what is
     * already open puts focus back on its panel. */
    function select(kind, tag, opener) {
      var info = describe(kind, tag);
      if (info === null) return;
      if (selection && selection.kind === kind && selection.tag === tag) {
        selection.panel.close.focus();
        return;
      }
      close(false);

      var panel = F.createPanel(doc, kind, tag, {
        info: info,
        vocab: vocab,
        interlocks: plant.interlocks,
        post: post,
        close: function () {
          close(true);
        },
        open: function (nextKind, nextTag) {
          select(nextKind, nextTag, null);
        },
        // A trend opened from a panel sits beside the panel.
        trend: trends
          ? function (point, button) {
              trends.open(point, button, selection ? rectIn(selection.panel.element) : null);
            }
          : null,
      });
      layer.appendChild(panel.element);
      if (latest !== null) panel.update(latest);
      selection = { kind: kind, tag: tag, opener: opener || subjectElement(kind, tag), panel: panel, size: "" };
      reposition();
      markSelected();
      panel.close.focus();
    }

    /* Press a canvas item: a reading's trend, or a faceplate. */
    function press(item) {
      var kind = item.getAttribute("data-canvas-kind");
      var tag = item.getAttribute("data-canvas-tag");
      if (kind !== "trend") {
        select(kind, tag, item);
        return;
      }
      var at = rectIn(item);
      trends.open(tag, item, sized(at) ? at : null);
    }

    function selectableOf(node) {
      for (var n = node; n && n !== workspace; n = n.parentNode) {
        if (n.getAttribute && has(n, "data-canvas-tag")) return n;
      }
      return null;
    }

    workspace.addEventListener("click", function (event) {
      // A click on a panel's own button may have replaced the panel (a hop to
      // the loop) before it reaches here: its target is then detached, and the
      // click was not on empty canvas. A click in a trend window or anything
      // else floating over the canvas is not on the canvas either.
      var target = event.target;
      if (inside(layer, target) || !inside(workspace, target)) return;
      if (target !== workspace && !inside(canvas, target)) return;
      var item = selectableOf(event.target);
      if (item) press(item);
      else close(false);
    });

    // An SVG group is not a native button: Enter and Space press it here.
    workspace.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      var item = selectableOf(event.target);
      if (!item || item !== event.target) return;
      event.preventDefault();
      press(item);
    });

    /* Escape belongs to whatever has focus: it closes the panel from the
     * panel or the canvas, or from nowhere in particular - not from a trend
     * window or anything else on the page. */
    doc.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || !selection) return;
      var active = doc.activeElement;
      if (active && active !== doc.body && !inside(layer, active) && !inside(canvas, active)) return;
      close(true);
    });

    if (root.addEventListener) root.addEventListener("resize", reposition);

    function refresh() {
      var mine = ++ticket;
      loading = true;
      return Promise.all([getJson(PLANT_URL), getJson(LISTING_URL).catch(function () { return null; })])
        .then(function (bodies) {
          var next = descriptionOf(bodies[0]);
          if (mine !== ticket || next === null) return;
          if (plant !== null && plant.id !== next.id) close(false);
          plant = next;
          vocab = vocabOf(bodies[1]);
          decorate();
          refreshHits();
          markSelected();
        })
        .catch(function () {})
        .then(function () {
          if (mine === ticket) loading = false;
        });
    }

    return {
      ready: refresh(),
      refresh: refresh,
      select: select,
      close: close,
      selected: function () {
        return selection ? { kind: selection.kind, tag: selection.tag } : null;
      },
      panelRect: function () {
        return selection ? rectIn(selection.panel.element) : null;
      },
      mark: markSelected,
      locate: locate,
      showTrend: showTrend,
      update: function (snapshot) {
        latest = snapshot;
        if (plant === null && !loading) refresh();
        decorate();
        refreshHits();
        if (selection) {
          selection.panel.update(snapshot);
          // A panel that grew or shrank (a message under the controls) may no
          // longer fit where it was put.
          var element = selection.panel.element;
          if (selection.size !== (element.offsetWidth || FALLBACK_SIZE.w) + "x" + (element.offsetHeight || FALLBACK_SIZE.h)) reposition();
        }
        markSelected();
      },
    };
  }

  api.mount = mount;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Canvas = api;
})(typeof window !== "undefined" ? window : globalThis);
