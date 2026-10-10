"""Minimal WebDriver client (stdlib only) for headless Firefox via geckodriver."""

import base64
import json
import subprocess
import time
import urllib.request

PORT = 4455


class Browser:
    def __init__(self, width=1440, height=900, reduced_motion=False, dark=False):
        self.proc = subprocess.Popen(
            ["geckodriver", "--port", str(PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/status", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        prefs = {"ui.prefersReducedMotion": 1 if reduced_motion else 0, "layout.css.prefers-color-scheme.content-override": 0 if dark else 1}
        caps = {
            "capabilities": {
                "alwaysMatch": {
                    "browserName": "firefox",
                    "moz:firefoxOptions": {"args": ["-headless"], "prefs": prefs},
                }
            }
        }
        self.sid = self._req("POST", "/session", caps)["sessionId"]
        self.size(width, height)

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}").get("value")

    def s(self, method, path, body=None):
        return self._req(method, f"/session/{self.sid}{path}", body)

    def size(self, w, h):
        self.s("POST", "/window/rect", {"width": w, "height": h})

    def go(self, url):
        self.s("POST", "/url", {"url": url})

    def js(self, script, *args):
        return self.s("POST", "/execute/sync", {"script": script, "args": list(args)})

    def shot(self, path):
        png = self.s("GET", "/screenshot")
        with open(path, "wb") as f:
            f.write(base64.b64decode(png))

    def find(self, css):
        r = self.s("POST", "/element", {"using": "css selector", "value": css})
        return list(r.values())[0]

    def click(self, css):
        self.s("POST", f"/element/{self.find(css)}/click", {})

    def actions(self, seq):
        self.s("POST", "/actions", {"actions": seq})
        self.s("DELETE", "/actions")

    def keys(self, text):
        self.actions([{"type": "key", "id": "kb", "actions": sum([[{"type": "keyDown", "value": c}, {"type": "keyUp", "value": c}] for c in text], [])}])

    def drag(self, x0, y0, x1, y1):
        self.actions([{"type": "pointer", "id": "m", "parameters": {"pointerType": "mouse"}, "actions": [
            {"type": "pointerMove", "x": int(x0), "y": int(y0), "origin": "viewport"},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerMove", "x": int((x0 + x1) / 2), "y": int((y0 + y1) / 2), "origin": "viewport", "duration": 100},
            {"type": "pointerMove", "x": int(x1), "y": int(y1), "origin": "viewport", "duration": 100},
            {"type": "pointerUp", "button": 0}]}])

    def click_at(self, x, y):
        self.actions([{"type": "pointer", "id": "m", "parameters": {"pointerType": "mouse"}, "actions": [
            {"type": "pointerMove", "x": int(x), "y": int(y), "origin": "viewport"},
            {"type": "pointerDown", "button": 0}, {"type": "pointerUp", "button": 0}]}])

    def logs(self):
        return self.js("return window.__errors || []")

    def quit(self):
        try:
            self.s("DELETE", "")
        finally:
            self.proc.terminate()
