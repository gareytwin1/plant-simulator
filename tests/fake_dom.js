// Just enough of a DOM for the console's browser glue (faceplate.js, canvas.js,
// trendwindows.js): elements with attributes, children, text, markup kept as a
// string, listeners that bubble, focus, a style, geometry a test assigns, and
// the selectors the glue uses (a tag, classes and [attr] or [attr="v"], comma
// separated). It checks what a click, a key and a submit do, not how anything
// looks; that is seen in a browser. Loaded into a `node -e` script by
// tests/test_faceplate.py, test_canvas.py and test_trend_windows.py.

const ZERO = { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };

class Node {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.attrs = {};
    this.children = [];
    this.parentNode = null;
    this.listeners = {};
    this.style = {};
    this._text = '';
    this._html = '';
    this.value = '';
    this.disabled = false;
    this.placeholder = '';
    this.ownerDocument = doc;
    this.screenRect = null;
    this.bbox = null;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  get firstChild() { return this.children[0] || null; }
  get classes() { return (this.attrs['class'] || '').split(/\s+/).filter(Boolean); }
  detach() {
    if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this);
    this.parentNode = null;
  }
  appendChild(c) { c.detach(); c.parentNode = this; this.children.push(c); return c; }
  insertBefore(c, ref) {
    c.detach();
    c.parentNode = this;
    const at = ref ? this.children.indexOf(ref) : -1;
    if (at < 0) this.children.push(c); else this.children.splice(at, 0, c);
    return c;
  }
  removeChild(c) { c.detach(); return c; }
  set textContent(t) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; this._html = ''; this._text = String(t); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  // Markup is kept as written, not parsed: a test reads it back as a string.
  set innerHTML(h) { this.textContent = ''; this._html = String(h); }
  get innerHTML() { return this._html; }
  get isConnected() { for (let n = this; n; n = n.parentNode) if (n === doc.body) return true; return false; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter(f => f !== fn); }
  // Dispatch to this node and up through its ancestors.
  dispatch(type, props) {
    const event = Object.assign({ type, target: this, defaultPrevented: false }, props);
    event.preventDefault = () => { event.defaultPrevented = true; };
    event.stopPropagation = () => { event.stopped = true; };
    // The path is fixed before any listener runs, as in a browser: a listener
    // that detaches the target does not stop the event reaching its ancestors.
    const path = [];
    for (let n = this; n; n = n.parentNode) path.push(n);
    path.forEach(n => { if (!event.stopped) (n.listeners[type] || []).forEach(fn => fn(event)); });
    if (!event.stopped) (doc.listeners[type] || []).forEach(fn => fn(event));
    return event;
  }
  fire(type, props) { return this.dispatch(type, props); }
  focus() { doc.activeElement = this; doc.focusLog.push(this); }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
  all() { return this.children.flatMap(c => [c, ...c.all()]); }
  matches(selector) {
    return selector.split(',').some(part => {
      const m = part.trim().match(/^([a-zA-Z][\w-]*)?((?:\.[\w-]+|\[[\w-]+(?:="[^"]*")?\])*)$/);
      if (!m) throw new Error('unsupported selector: ' + part);
      if (m[1] && m[1].toUpperCase() !== this.tagName) return false;
      return (m[2].match(/\.[\w-]+|\[[^\]]+\]/g) || []).every(piece => {
        if (piece[0] === '.') return this.classes.includes(piece.slice(1));
        const [name, value] = piece.slice(1, -1).split('=');
        const have = this.getAttribute(name);
        return value === undefined ? have !== null : have === value.slice(1, -1);
      });
    });
  }
  closest(sel) { for (let n = this; n && n.matches; n = n.parentNode) if (n.matches(sel)) return n; return null; }
  querySelectorAll(sel) { return this.all().filter(n => n.matches(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getBoundingClientRect() {
    if (this.screenRect) return this.screenRect;
    // A device's hit area is drawn over its group.
    if (this.classes.includes('eq-hit') && this.parentNode && this.parentNode.screenRect) return this.parentNode.screenRect;
    return ZERO;
  }
  getBBox() { return this.bbox || { x: 0, y: 0, width: 0, height: 0 }; }
  get offsetWidth() { return this.classes.includes('faceplate') ? doc.panelSize.w : 0; }
  get offsetHeight() { return this.classes.includes('faceplate') ? doc.panelSize.h : 0; }
}

const doc = {
  listeners: {},
  focusLog: [],
  activeElement: null,
  panelSize: { w: 330, h: 260 },
  createElement: tag => new Node(tag),
  createElementNS: (ns, tag) => new Node(tag),
  createTextNode: text => { const n = new Node('#text'); n._text = text; return n; },
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
};
doc.body = new Node('body');
doc.activeElement = doc.body;

const settle = () => new Promise(r => setTimeout(r, 0));
