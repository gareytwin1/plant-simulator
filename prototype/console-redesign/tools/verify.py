"""Drive the prototype with real input events and check the review criteria."""
import json, sys, time
from wd import Browser

URL = "http://127.0.0.1:8765/prototype/console-redesign/"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (" - " + str(detail) if detail else ""))


LAYOUT = """
var s=document.querySelector('#canvas svg'); var r=s.getBoundingClientRect();
var m=s.getScreenCTM(); var eq={}; s.querySelectorAll('.eq[data-tag] .body').forEach(function(b){var t=b.closest('.eq').getAttribute('data-tag'); var x=b.getBBox(); eq[t]=[Math.round(m.a*x.x+m.e),Math.round(m.d*x.y+m.f),Math.round(m.a*x.width),Math.round(m.d*x.height)];});
return JSON.stringify({svg:[r.left,r.top,r.width,r.height], vb: s.getAttribute('viewBox'), eq:eq});
"""


def center(b, css):
    return b.js("var r=document.querySelector(arguments[0]).getBoundingClientRect(); return [r.left+r.width/2, r.top+r.height/2]", css)


def run():
    b = Browser(1440, 900)
    try:
        b.go(URL); time.sleep(2.5)
        b.js("window.__errors=[]; window.addEventListener('error', function(e){window.__errors.push(String(e.message))}); Prototype.demo(2)"); time.sleep(0.5)
        base = b.js(LAYOUT)

        # Hover does not open anything.
        x, y = center(b, '#canvas .eq[data-tag="V-301"] .body')
        fill0 = b.js("return getComputedStyle(document.querySelector('#canvas .eq[data-tag=\"V-301\"] .body')).fill")
        b.actions([{"type": "pointer", "id": "m", "parameters": {"pointerType": "mouse"}, "actions": [{"type": "pointerMove", "x": int(x), "y": int(y), "origin": "viewport"}]}])
        time.sleep(0.4)
        fill1 = b.js("return getComputedStyle(document.querySelector('#canvas .eq[data-tag=\"V-301\"] .body')).fill")
        check("hover tints the equipment", fill0 != fill1, (fill0, fill1))
        check("hover opens no faceplate, tooltip or box",
              b.js("return !document.querySelector('.faceplate') && !document.querySelector('.tip') && getComputedStyle(document.querySelector('#canvas .eq[data-tag=\"V-301\"] .eq-hit')).stroke.indexOf('rgba(0, 0, 0, 0)') >= 0"))
        cx0, cy0 = center(b, '#canvas .ctl[data-controller="LIC-301"] .ctl-balloon')
        sig0 = b.js("return getComputedStyle(document.querySelector('#canvas .ctl[data-controller=\"LIC-301\"] .signal')).stroke")
        b.actions([{"type": "pointer", "id": "m", "parameters": {"pointerType": "mouse"}, "actions": [{"type": "pointerMove", "x": int(cx0), "y": int(cy0), "origin": "viewport"}]}])
        time.sleep(0.4)
        sig1 = b.js("return getComputedStyle(document.querySelector('#canvas .ctl[data-controller=\"LIC-301\"] .signal')).stroke")
        check("hover tints a controller's signal lines", sig0 != sig1, (sig0, sig1))

        # Equipment click opens its faceplate and no trend.
        b.click_at(x, y); time.sleep(0.4)
        check("equipment click opens its faceplate",
              b.js("var f=document.querySelector('.faceplate'); return f && f.getAttribute('data-faceplate')") == "V-301")
        check("equipment click opens no trend", b.js("return document.querySelectorAll('.trend-window').length") == 0)
        fp = b.js("var r=document.querySelector('.faceplate').getBoundingClientRect(), a=document.querySelector('#canvas .eq[data-tag=\"V-301\"] .eq-hit').getBoundingClientRect(); return [r.left, r.right, a.left, a.right]")
        check("faceplate sits beside its component", fp[0] >= fp[3] or fp[1] <= fp[2], fp)
        check("opening a faceplate sends no command", b.js("return Prototype.commands.length") == 0)

        # Escape closes; background click closes.
        b.keys(""); time.sleep(0.2)
        check("Escape closes the faceplate", b.js("return !document.querySelector('.faceplate')"))
        b.click_at(x, y); time.sleep(0.3)
        b.click_at(1300, 760); time.sleep(0.3)
        check("background click closes the faceplate", b.js("return !document.querySelector('.faceplate')"))
        check("dismissing sends no command", b.js("return Prototype.commands.length") == 0)

        # Controller balloon opens the controller faceplate.
        cx, cy = center(b, '#canvas .ctl .ctl-balloon')
        b.click_at(cx, cy); time.sleep(0.3)
        check("controller point opens the controller faceplate",
              b.js("var f=document.querySelector('.faceplate'); return f && f.getAttribute('data-faceplate')") == "FIC-301")
        check("the controller faceplate has no tuning controls",
              b.js("var f=document.querySelector('.faceplate'); return !/tuning/i.test(f.textContent) && !f.querySelector('[id$=-kp],[id$=-ki],[id$=-kd]')"))
        b.keys(""); time.sleep(0.2)

        # Reading click opens a trend, not a faceplate.
        rx, ry = center(b, '.readout[data-point="V-301.level"] .readout-hit')
        b.click_at(rx, ry); time.sleep(0.4)
        check("reading click opens its trend",
              b.js("return Array.from(document.querySelectorAll('.trend-window')).map(function(w){return w.getAttribute('data-point')}).join()") == "V-301.level")
        check("reading click opens no faceplate", b.js("return !document.querySelector('.faceplate')"))
        b.click_at(rx, ry); time.sleep(0.3)
        check("selecting it again does not duplicate", b.js("return document.querySelectorAll('.trend-window').length") == 1)

        # Trend from a faceplate, and dedupe across entry points.
        b.click_at(x, y); time.sleep(0.3)
        b.click('[data-trend="V-301.pressure"]'); time.sleep(0.4)
        check("faceplate Trend opens that parameter",
              b.js("return !!document.querySelector('.trend-window[data-point=\"V-301.pressure\"]')"))
        b.click('[data-trend="V-301.pressure"]'); time.sleep(0.2)
        px, py = center(b, '.readout[data-point="V-301.pressure"] .readout-hit')
        b.js("document.querySelector('.faceplate .fp-close').click()")
        b.click_at(px, py); time.sleep(0.3)
        check("faceplate and reading share one window per parameter",
              b.js("return document.querySelectorAll('.trend-window').length") == 2)
        sel = b.js("var w=document.querySelector('.trend-window[data-point=\"V-301.pressure\"]').getBoundingClientRect(), a=document.querySelector('#canvas .eq[data-tag=\"V-301\"] .body').getBoundingClientRect(); return !(w.right<=a.left||w.left>=a.right||w.bottom<=a.top||w.top>=a.bottom)")
        check("a new window does not cover the selected component", not sel)

        b.shot('shots/after_open.png')
        after_open = b.js(LAYOUT)
        check("opening trends leaves the canvas unchanged", after_open == base)

        # Move by title bar.
        g0 = b.js("return Prototype.windowGeometry()")
        bx, by = center(b, '.trend-window[data-point="V-301.level"] .tw-name')
        b.drag(bx, by, bx + 300, by + 60); time.sleep(0.3)
        g1 = b.js("return Prototype.windowGeometry()")
        v0 = [w for w in g0 if w["point"] == "V-301.level"][0]; v1 = [w for w in g1 if w["point"] == "V-301.level"][0]
        check("dragging the title bar moves the window", abs((v1["x"] - v0["x"]) - 300) < 3 and abs((v1["y"] - v0["y"]) - 60) < 3, (v0, v1))
        check("moving leaves the canvas unchanged", b.js(LAYOUT) == base)

        # Resize by grip.
        gx, gy = center(b, '.trend-window[data-point="V-301.level"] .tw-resize')
        b.drag(gx, gy, gx + 140, gy + 90); time.sleep(0.4)
        v2 = [w for w in b.js("return Prototype.windowGeometry()") if w["point"] == "V-301.level"][0]
        check("dragging the grip resizes the window", abs(v2["w"] - v1["w"] - 140) < 3 and abs(v2["h"] - v1["h"] - 90) < 3, (v1, v2))
        check("chart redraws to the new size", b.js("var s=document.querySelector('.trend-window[data-point=\"V-301.level\"] .trend-svg'); return s.viewBox.baseVal.width") > v1["w"] - 40)
        check("resizing leaves the canvas unchanged", b.js(LAYOUT) == base)

        # Minimise and restore.
        b.click('.trend-window[data-point="V-301.level"] .tw-ctl[title="Minimise"]'); time.sleep(0.3)
        check("minimise hides the window and shows the restore tray",
              b.js("return document.querySelector('.trend-window[data-point=\"V-301.level\"]').hidden && !document.getElementById('tray').hidden"))
        check("minimising leaves the canvas unchanged", b.js(LAYOUT) == base)
        b.click('.tray-chip[data-point="V-301.level"]'); time.sleep(0.3)
        v3 = [w for w in b.js("return Prototype.windowGeometry()") if w["point"] == "V-301.level"][0]
        check("restore brings it back where it was", (v3["x"], v3["y"], v3["w"], v3["h"]) == (v2["x"], v2["y"], v2["w"], v2["h"]) and b.js("return document.getElementById('tray').hidden"))
        b.click('.trend-window[data-point="V-301.level"] .tw-spans [data-span="5"]'); time.sleep(0.2)

        # Close and reopen: geometry and range kept for the session.
        b.click('.trend-window[data-point="V-301.level"] .tw-ctl[title="Close"]'); time.sleep(0.2)
        check("closing removes the window", b.js("return !document.querySelector('.trend-window[data-point=\"V-301.level\"]')"))
        check("closing leaves the canvas unchanged", b.js(LAYOUT) == base)
        b.click_at(rx, ry); time.sleep(0.3)
        v4 = [w for w in b.js("return Prototype.windowGeometry()") if w["point"] == "V-301.level"][0]
        check("reopening keeps position and size", (v4["x"], v4["y"], v4["w"], v4["h"]) == (v2["x"], v2["y"], v2["w"], v2["h"]), (v2, v4))
        check("reopening keeps the time range",
              b.js("return document.querySelector('.trend-window[data-point=\"V-301.level\"] [aria-pressed=\"true\"]').getAttribute('data-span')") == "5")

        # Alarm overlay: covers, blocks, keeps the header, restores.
        b.js("Prototype.demo(10)"); time.sleep(0.5)
        b.click_at(*center(b, '#canvas .readout[data-point="V-301.level"] .readout-hit')); time.sleep(0.3)
        b.click_at(*center(b, '#canvas .readout[data-point="B-LV-301.flow"] .readout-hit')); time.sleep(0.3)
        b.click_at(*center(b, '#canvas .eq[data-tag="LV-301"] .body')); time.sleep(0.3)
        check("ribbon tints to the highest active priority", b.js("return document.getElementById('ribbon').getAttribute('data-severity')") == "low")
        check("ribbon pulses while alarms are new", b.js("return document.getElementById('ribbon').getAttribute('data-unack')") == "true")
        before = b.js("return JSON.stringify([Prototype.windowGeometry(), document.querySelector('.faceplate') && document.querySelector('.faceplate').getAttribute('data-faceplate')])")
        phase = b.js("return Prototype.state.session.phase")
        b.click("#ribbon-alarms"); time.sleep(0.4)
        check("alarm area opens the overlay", b.js("return !document.getElementById('alarm-overlay').hidden"))
        check("opening the overlay leaves the run as it was", b.js("return Prototype.state.session.phase") == phase)
        hit = b.js("var w=document.querySelector('.trend-window').getBoundingClientRect(); var e=document.elementFromPoint(w.left+w.width/2, w.bottom-20); return e && e.closest('#alarm-overlay') ? 'overlay' : (e && e.className && e.className.baseVal !== undefined ? e.className.baseVal : e && e.className)")
        check("overlay covers floating windows", hit == "overlay", hit)
        b.click_at(x, y); time.sleep(0.3)
        check("covered equipment cannot be clicked", b.js("return document.querySelector('.faceplate').getAttribute('data-faceplate') === 'LV-301'"))
        check("a backdrop click closes the overlay without reaching the canvas", b.js("return document.getElementById('alarm-overlay').hidden"))
        b.click("#ribbon-alarms"); time.sleep(0.4)
        check("ribbon controls stay clickable", b.js("var r=document.getElementById('run-button').getBoundingClientRect(); var e=document.elementFromPoint(r.left+5,r.top+5); return !!(e && e.closest('#run-button'))"))
        # Filter and acknowledge.
        b.js("var c=document.querySelector('input[name=priority][value=low]'); c.click();"); time.sleep(0.2)
        check("priority filter hides LOW", b.js("return document.querySelectorAll('#alarm-rows tr').length") == 0)
        b.js("var c=document.querySelector('input[name=priority][value=low]'); c.click();")
        b.js("var s=document.getElementById('filter-search'); s.value='pressure'; s.dispatchEvent(new Event('input'));"); time.sleep(0.2)
        check("search narrows to matching tag or message", b.js("return document.querySelectorAll('#alarm-rows tr').length") == 1)
        b.js("var s=document.getElementById('filter-search'); s.value=''; s.dispatchEvent(new Event('input'));")
        b.click('#alarm-rows tr[data-alarm-id*="V-301:level"] [data-row-action="ack"]'); time.sleep(0.3)
        row = b.js("var r=document.querySelector('#alarm-rows tr[data-alarm-id*=\"V-301:level\"]'); return [r.querySelector('.cond').textContent, r.querySelector('.ackd').textContent]")
        check("acknowledge keeps the condition shown as active", row == ["Active", "Yes"], row)
        check("ribbon stays tinted after acknowledgement", b.js("return document.getElementById('ribbon').getAttribute('data-severity')") == "low")
        b.keys(""); time.sleep(0.3)
        check("Escape closes the overlay", b.js("return document.getElementById('alarm-overlay').hidden"))
        after = b.js("return JSON.stringify([Prototype.windowGeometry(), document.querySelector('.faceplate') && document.querySelector('.faceplate').getAttribute('data-faceplate')])")
        check("closing the overlay restores the workspace", after == before, (before, after))
        _l = b.js(LAYOUT)
        check("canvas unchanged after the overlay", _l == base, (base, _l) if _l != base else "")

        # Small viewports: title bars and close controls stay reachable.
        for w, h in [(1024, 700), (800, 600), (390, 760)]:
            b.size(w, h); time.sleep(0.6)
            ok = b.js("""var vw=innerWidth, vh=innerHeight; return Array.from(document.querySelectorAll('.trend-window:not([hidden])')).every(function(win){
              var bar=win.querySelector('.tw-bar').getBoundingClientRect(); var c=win.querySelector('.tw-ctl[title=Close]').getBoundingClientRect();
              return bar.top>=0 && bar.bottom<=vh && c.left>=0 && c.right<=vw && c.top>=0 && c.bottom<=vh; })""")
            check("title bars and close buttons reachable at %dx%d" % (w, h), ok)
        b.size(1440, 900); time.sleep(0.4)

        # Keyboard: Tab reaches equipment, Enter opens it.
        b.js("document.querySelectorAll('.trend-window .tw-ctl[title=Close]').forEach(function(c){c.click()})")
        b.js("document.querySelector('#canvas .eq[data-tag=\"LV-301\"]').focus()")
        b.keys(""); time.sleep(0.3)
        check("keyboard Enter on equipment opens its faceplate",
              b.js("var f=document.querySelector('.faceplate'); return f && f.getAttribute('data-faceplate')") == "LV-301")
        check("focus moves into the faceplate", b.js("return document.activeElement.classList.contains('fp-close')"))

        # Command feedback, and isolation.
        b.keys("\ue00c"); time.sleep(0.2)
        b.click_at(*center(b, '#canvas .ctl[data-controller="PIC-301"] .ctl-balloon')); time.sleep(0.3)
        b.click('.faceplate [data-mode="auto"]'); time.sleep(0.1)
        check("a command shows pending feedback", b.js("return document.querySelector('[data-fp=feedback]').getAttribute('data-kind')") == "pending")
        time.sleep(0.9)
        check("then accepted feedback that does not claim the value is reached",
              b.js("var f=document.querySelector('[data-fp=feedback]'); return f.getAttribute('data-kind')==='accepted' && /responds over the next steps/.test(f.textContent)"))
        b.js("var i=document.querySelector('.fp-entry input'); i.value='abc'; i.form.requestSubmit();"); time.sleep(0.1)
        check("an invalid entry is rejected locally", b.js("return document.querySelector('[data-fp=feedback]').getAttribute('data-kind')") == "rejected")
        res = b.js("return performance.getEntriesByType('resource').map(function(e){return e.name}).filter(function(n){return /\\/api\\//.test(n)})")
        check("no request reached /api", res == [] and b.js("return Prototype.blocked.length") == 0, res)
        check("no script errors", b.js("return (window.__errors||[]).length") == 0)
    finally:
        b.quit()


run()
failed = [r for r in results if not r[1]]
print("\n%d checks, %d failed" % (len(results), len(failed)))
sys.exit(1 if failed else 0)
