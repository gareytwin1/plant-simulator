# Console standards

The rules every operator-console surface follows for colour, symbol and state.
Values live in [static/css/tokens.css](../static/css/tokens.css) and nowhere
else: a UI branch references a token, never a hex literal. Measured contrast
and colour-blind figures come from `python tests/test_console_tokens.py`;
[tests/test_console_tokens.py](../tests/test_console_tokens.py) fails the build
if a token drops below its floor.

## Rules

1. **Colour never carries a state alone.** Every alarm priority and equipment
   state also has a glyph (`--symbol-*`) and a text label. A reader with no
   colour vision, or a monochrome print, gets the same information.
2. **Process graphics stay neutral.** Pipes, vessels and readouts use
   `--surface`, `--text` and `--border`; colour appears only for alarm
   priority, envelope band and equipment state, so a coloured element is
   something to look at.
3. **One hue family per meaning, across every surface.** The band behind a
   trend, the badge in the summary and the frame on a faceplate use the same
   family for the same severity.
4. **Both themes, always.** Tokens are `light-dark()` pairs, so a theme is
   `color-scheme` on `<html>`: the OS preference by default, `data-theme="light"`
   or `"dark"` to override. A component that needs a theme-specific branch is
   missing a token.

## Alarm priority

Priorities are `app.alarms.manager.Priority`. The default mapping from an
envelope severity is in that module; this file does not restate it.

| Priority | Fill / on / mark | Glyph | Label |
|---|---|---|---|
| critical | `--alarm-critical-*` (red family) | `--symbol-alarm-critical` triangle | CRIT |
| high | `--alarm-high-*` (red family, as `critical` for now) | `--symbol-alarm-high` diamond | HIGH |
| low | `--alarm-low-*` (yellow family) | `--symbol-alarm-low` circle | LOW |

- `fill` is a badge or row background, `on` is the text and glyph drawn on it,
  `mark` is a line or glyph drawn directly on the surface. Use `mark` for a
  glyph with no badge, never `fill`.
- The three fills differ in hue **and** lightness, which is what keeps them
  apart under protanopia, deuteranopia and tritanopia. A new priority colour
  must pass the same check before it is added.

### Alarm lifecycle

`app.alarms.state.AlarmState` is shown by behaviour, not by a fourth colour, so
it stays legible whatever the priority colour is.

| State | Presentation |
|---|---|
| `unack` | Filled, flashing at `--alarm-flash-period` |
| `acked` | Filled, steady |
| `rtn_unack` | Outlined in the `mark` colour, no fill, steady |
| `normal` | Not shown as an alarm |

Under `prefers-reduced-motion: reduce`, flashing becomes a steady fill with a
thick border. Flashing is never the only cue (rule 1).

## Envelope bands

Shading behind a trend or gauge for the limit bands in
`app.envelope.evaluator.Severity`. Bands use the `tint` tokens: quiet enough
that a trace stays readable on top, and distinct enough to tell bands and the
surface apart.

| Severity | Token | Hue family |
|---|---|---|
| warning | `--band-warning-tint` | yellow, as `low` |
| alarm | `--band-alarm-tint` | red, as `high` |
| trip | `--band-trip-tint` | red, as `critical` |

`--text` must stay legible on every tint. A limit line is drawn in the matching
alarm `mark` colour so the threshold remains visible where the shading stops.

## Equipment state

| State | Token | Glyph | Label |
|---|---|---|---|
| running | `--equip-running-*` (green) | `--symbol-equip-running` play | RUN |
| stopped | `--equip-stopped-*` (grey) | `--symbol-equip-stopped` square | STOP |
| tripped | `--equip-tripped-*` (purple) | `--symbol-equip-tripped` cross | TRIP |

- **Tripped is purple, not red.** Red belongs to critical alarms. A tripped
  machine usually has an alarm active too, and the two must be separable at a
  glance: the purple fill says "the protection acted", the red alarm badge says
  "the operator must respond".
- **Stopped is grey, not a warning colour.** A stopped machine is a normal
  state; it earns attention only through an alarm or a trip.
- The alarm and equipment glyph sets share no shape, so a glyph on its own
  identifies its group.

## Adding a token

1. Add it to `tokens.css` as a literal `light-dark(#rrggbb, #rrggbb)` pair.
2. Add its role to the contrast tests: a `fill`/`on` pair needs text contrast,
   a `mark` needs graphic contrast against both surfaces, and a new status hue
   needs the colour-blind distinctness check against its neighbours.
3. Give it a glyph and a label here if it names a state.

Floors, and why they are what they are, are defined as constants at the top of
the test file: WCAG 2.2 text (4.5:1) and graphic (3:1) contrast, and a CIEDE2000
distance for colour-blind simulation (Machado et al. 2009 matrices).
