import sys, time
from wd import Browser
w,h,dark,prefix=int(sys.argv[1]),int(sys.argv[2]),sys.argv[3]=="dark",sys.argv[4]
only=[int(x) for x in sys.argv[5].split(",")] if len(sys.argv)>5 else None
b=Browser(w,h,dark=dark)
try:
    b.go("http://127.0.0.1:8765/prototype/console-redesign/"); time.sleep(2)
    b.js("window.__errors=[]; window.addEventListener('error', e=>window.__errors.push(String(e.message)));")
    if dark: b.js("document.documentElement.setAttribute('data-theme','dark')")
    names=b.js("return Prototype.demos()")
    for i,n in enumerate(names):
        if only and i not in only: continue
        b.js(f"Prototype.demo({i})"); time.sleep(0.8)
        b.shot(f"shots/{prefix}{i:02d}.png")
    print("errors", b.logs(), "blocked", b.js("return Prototype.blocked"))
finally:
    b.quit()
