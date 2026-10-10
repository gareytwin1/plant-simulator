---
name: desktop-first
description: Console redesign (M20) targets the desktop browser first; phone-width work and checks are deferred
metadata:
  node_type: memory
  type: project
  originSessionId: 44afb8d3-c901-4a55-92a8-8ee3c4d82d71
  modified: 2026-10-10T13:40:13.908Z
---

On 10 October 2026, during T20-8, the user said to focus on the desktop browser version of the console redesign and worry about phone width later.

**Why:** The desktop experience is the current priority; phone layout is a later pass.

**How to apply:** For M20 tasks, verify and polish at desktop widths. Don't spend effort on phone-width layout or treat the build plan's "at phone width" acceptance checks as blocking. Mention the deferral when reporting a task rather than claiming phone checks passed. Existing phone CSS can stay as it is. See [[model-split]] for who does what.
