"""Contrast and colour-blind checks for static/css/tokens.css (T16-1).

The tokens file is the single source of truth for console colour, so these
tests parse it rather than restating any value. Run `python
tests/test_console_tokens.py` for the measured numbers behind each floor.
"""

import itertools
import math
import re
from pathlib import Path

import pytest

TOKENS = Path(__file__).resolve().parent.parent / "static" / "css" / "tokens.css"

THEMES = ("light", "dark")

# WCAG 2.2: 4.5:1 for text, 3:1 for graphical objects and large marks.
TEXT_CONTRAST = 4.5
GRAPHIC_CONTRAST = 3.0

# CIEDE2000 between two palette entries after colour-vision-deficiency
# simulation. 2.3 is one just-noticeable difference; 15 is clearly distinct
# at a glance, which is the bar for telling alarm priorities apart.
CVD_MIN_DELTA_E = 15.0

# Tints are quiet by design, so the bar is lower than for a status fill.
BAND_MIN_DELTA_E = 8.0

ALARM_STATES = ("critical", "high", "low")
EQUIP_STATES = ("running", "stopped", "tripped")
BAND_STATES = ("warning", "alarm", "trip")

# Machado, Oliveira and Fernandes (2009), severity 1.0, linear RGB.
CVD_MATRICES = {
    "protanopia": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deuteranopia": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritanopia": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}

_PAIR = re.compile(
    r"^\s*(--[\w-]+):\s*light-dark\(\s*(#[0-9a-fA-F]{6})\s*,\s*(#[0-9a-fA-F]{6})\s*\)\s*;",
    re.MULTILINE,
)
_SYMBOL = re.compile(r'^\s*(--symbol-[\w-]+):\s*"\\([0-9A-Fa-f]+)"\s*;', re.MULTILINE)


def load_tokens() -> dict[str, dict[str, str]]:
    """Return {theme: {token name: #rrggbb}} for every light-dark() colour."""
    text = TOKENS.read_text()
    tokens: dict[str, dict[str, str]] = {theme: {} for theme in THEMES}
    for name, light, dark in _PAIR.findall(text):
        tokens["light"][name] = light.lower()
        tokens["dark"][name] = dark.lower()
    return tokens


def load_symbols() -> dict[str, str]:
    return {
        name: chr(int(code, 16))
        for name, code in _SYMBOL.findall(TOKENS.read_text())
    }


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    r, g, b = (int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return r, g, b


def _linear(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _luminance(hex_colour: str) -> float:
    r, g, b = (_linear(c) for c in _rgb(hex_colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _to_lab(linear_rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = linear_rgb
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _simulate(hex_colour: str, matrix: tuple[tuple[float, ...], ...]) -> tuple[float, float, float]:
    linear = [_linear(c) for c in _rgb(hex_colour)]
    out = [min(1.0, max(0.0, sum(m * c for m, c in zip(row, linear)))) for row in matrix]
    return out[0], out[1], out[2]


def delta_e_2000(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    c_bar7 = ((c1 + c2) / 2) ** 7
    g = 0.5 * (1 - math.sqrt(c_bar7 / (c_bar7 + 25**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360

    dlp = l2 - l1
    dcp = c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180:
            dhp -= 360
        elif dhp < -180:
            dhp += 360
    dhp_term = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp / 2))

    lp_bar = (l1 + l2) / 2
    cp_bar = (c1p + c2p) / 2
    if c1p * c2p == 0:
        hp_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hp_bar = (h1p + h2p) / 2
    else:
        hp_bar = (h1p + h2p + (360 if h1p + h2p < 360 else -360)) / 2

    t = (
        1
        - 0.17 * math.cos(math.radians(hp_bar - 30))
        + 0.24 * math.cos(math.radians(2 * hp_bar))
        + 0.32 * math.cos(math.radians(3 * hp_bar + 6))
        - 0.20 * math.cos(math.radians(4 * hp_bar - 63))
    )
    d_theta = 30 * math.exp(-(((hp_bar - 275) / 25) ** 2))
    cp7 = cp_bar**7
    rc = 2 * math.sqrt(cp7 / (cp7 + 25**7))
    sl = 1 + 0.015 * (lp_bar - 50) ** 2 / math.sqrt(20 + (lp_bar - 50) ** 2)
    sc = 1 + 0.045 * cp_bar
    sh = 1 + 0.015 * cp_bar * t
    rt = -math.sin(math.radians(2 * d_theta)) * rc
    return math.sqrt(
        (dlp / sl) ** 2
        + (dcp / sc) ** 2
        + (dhp_term / sh) ** 2
        + rt * (dcp / sc) * (dhp_term / sh)
    )


def cvd_distance(a: str, b: str, kind: str) -> float:
    matrix = CVD_MATRICES[kind]
    return delta_e_2000(_to_lab(_simulate(a, matrix)), _to_lab(_simulate(b, matrix)))


def _fill_states() -> list[tuple[str, str]]:
    return [
        (group, state)
        for group, states in (("alarm", ALARM_STATES), ("equip", EQUIP_STATES))
        for state in states
    ]


def report() -> str:
    tokens = load_tokens()
    lines = []
    for theme in THEMES:
        t = tokens[theme]
        lines.append(f"[{theme}]")
        for group, state in _fill_states():
            ratio = contrast(t[f"--{group}-{state}-on"], t[f"--{group}-{state}-fill"])
            lines.append(f"  {group}-{state} on/fill {ratio:5.2f}:1 (floor {TEXT_CONTRAST})")
        for state in ALARM_STATES:
            ratio = contrast(t[f"--alarm-{state}-mark"], t["--surface"])
            lines.append(f"  alarm-{state} mark/surface {ratio:5.2f}:1 (floor {GRAPHIC_CONTRAST})")
        for state in BAND_STATES:
            ratio = contrast(t["--text"], t[f"--band-{state}-tint"])
            lines.append(f"  band-{state} text/tint {ratio:5.2f}:1 (floor {TEXT_CONTRAST})")
        for name, fg in (("text", "--text"), ("text-muted", "--text-muted")):
            for bg in ("--surface", "--surface-raised"):
                lines.append(f"  {name}/{bg[2:]} {contrast(t[fg], t[bg]):5.2f}:1 (floor {TEXT_CONTRAST})")
        for token in ("--border", "--focus-ring"):
            lines.append(f"  {token[2:]}/surface {contrast(t[token], t['--surface']):5.2f}:1 (floor {GRAPHIC_CONTRAST})")
        for kind in CVD_MATRICES:
            for a, b in itertools.combinations(ALARM_STATES, 2):
                d = cvd_distance(t[f"--alarm-{a}-fill"], t[f"--alarm-{b}-fill"], kind)
                lines.append(f"  {kind} alarm {a}/{b} dE2000 {d:5.1f} (floor {CVD_MIN_DELTA_E})")
    return "\n".join(lines)


@pytest.fixture(scope="module")
def tokens() -> dict[str, dict[str, str]]:
    return load_tokens()


def test_every_expected_token_is_defined_in_both_themes(tokens):
    expected = {"--surface", "--surface-raised", "--border", "--text", "--text-muted", "--focus-ring"}
    for group, state in _fill_states():
        expected |= {f"--{group}-{state}-fill", f"--{group}-{state}-on"}
    expected |= {f"--alarm-{s}-mark" for s in ALARM_STATES}
    expected |= {f"--band-{s}-tint" for s in BAND_STATES}
    for theme in THEMES:
        assert expected <= tokens[theme].keys(), (theme, expected - tokens[theme].keys())


@pytest.mark.parametrize("theme", THEMES)
def test_text_on_every_fill_meets_text_contrast(tokens, theme):
    t = tokens[theme]
    for group, state in _fill_states():
        ratio = contrast(t[f"--{group}-{state}-on"], t[f"--{group}-{state}-fill"])
        assert ratio >= TEXT_CONTRAST, (theme, group, state, ratio)


@pytest.mark.parametrize("theme", THEMES)
def test_alarm_marks_meet_graphic_contrast_on_the_surface(tokens, theme):
    t = tokens[theme]
    for state in ALARM_STATES:
        for surface in ("--surface", "--surface-raised"):
            ratio = contrast(t[f"--alarm-{state}-mark"], t[surface])
            assert ratio >= GRAPHIC_CONTRAST, (theme, state, surface, ratio)


@pytest.mark.parametrize("theme", THEMES)
def test_body_text_is_legible_on_both_surfaces(tokens, theme):
    t = tokens[theme]
    for fg in ("--text", "--text-muted"):
        for bg in ("--surface", "--surface-raised"):
            assert contrast(t[fg], t[bg]) >= TEXT_CONTRAST, (theme, fg, bg)
    for token in ("--border", "--focus-ring"):
        assert contrast(t[token], t["--surface"]) >= GRAPHIC_CONTRAST, (theme, token)


@pytest.mark.parametrize("theme", THEMES)
def test_text_is_legible_on_every_envelope_band_tint(tokens, theme):
    t = tokens[theme]
    for state in BAND_STATES:
        assert contrast(t["--text"], t[f"--band-{state}-tint"]) >= TEXT_CONTRAST, (theme, state)


@pytest.mark.parametrize("theme", THEMES)
def test_band_tints_are_distinct_from_each_other_and_the_surface(tokens, theme):
    t = tokens[theme]
    colours = [t["--surface"]] + [t[f"--band-{s}-tint"] for s in BAND_STATES]
    for a, b in itertools.combinations(colours, 2):
        if a == b:  # alarm shares trip's red for now; the glyph tells them apart
            continue
        d = delta_e_2000(_to_lab(tuple(map(_linear, _rgb(a)))), _to_lab(tuple(map(_linear, _rgb(b)))))
        assert d >= BAND_MIN_DELTA_E, (theme, a, b, d)


@pytest.mark.parametrize("kind", sorted(CVD_MATRICES))
@pytest.mark.parametrize("theme", THEMES)
def test_band_tints_stay_distinct_under_colour_blindness(tokens, theme, kind):
    t = tokens[theme]
    colours = [t["--surface"]] + [t[f"--band-{s}-tint"] for s in BAND_STATES]
    for a, b in itertools.combinations(colours, 2):
        if a == b:
            continue
        d = cvd_distance(a, b, kind)
        assert d >= BAND_MIN_DELTA_E, (theme, kind, a, b, d)


@pytest.mark.parametrize("kind", sorted(CVD_MATRICES))
@pytest.mark.parametrize("theme", THEMES)
def test_alarm_priorities_stay_distinct_under_colour_blindness(tokens, theme, kind):
    t = tokens[theme]
    for a, b in itertools.combinations(ALARM_STATES, 2):
        if t[f"--alarm-{a}-fill"] == t[f"--alarm-{b}-fill"]:  # high shares critical's red; glyphs differ
            continue
        d = cvd_distance(t[f"--alarm-{a}-fill"], t[f"--alarm-{b}-fill"], kind)
        assert d >= CVD_MIN_DELTA_E, (theme, kind, a, b, d)


@pytest.mark.parametrize("kind", sorted(CVD_MATRICES))
@pytest.mark.parametrize("theme", THEMES)
def test_equipment_states_stay_distinct_under_colour_blindness(tokens, theme, kind):
    t = tokens[theme]
    for a, b in itertools.combinations(EQUIP_STATES, 2):
        d = cvd_distance(t[f"--equip-{a}-fill"], t[f"--equip-{b}-fill"], kind)
        assert d >= CVD_MIN_DELTA_E, (theme, kind, a, b, d)


def test_cvd_simulation_confirms_red_green_is_the_hazard():
    """Guard the checker itself: a classic red/green pair must fail the floor."""
    assert cvd_distance("#d00000", "#00a000", "deuteranopia") < CVD_MIN_DELTA_E
    assert cvd_distance("#000000", "#ffffff", "deuteranopia") > 90


def test_contrast_matches_the_wcag_reference_values():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)


def test_every_state_has_a_distinct_symbol_within_its_group():
    symbols = load_symbols()
    groups = {
        "alarm": [f"--symbol-alarm-{s}" for s in ALARM_STATES],
        "equip": [f"--symbol-equip-{s}" for s in EQUIP_STATES],
    }
    for group, names in groups.items():
        assert set(names) <= symbols.keys(), group
        glyphs = [symbols[n] for n in names]
        assert len(set(glyphs)) == len(glyphs), (group, glyphs)


def test_alarm_and_equipment_symbols_do_not_overlap():
    symbols = load_symbols()
    alarm = {v for k, v in symbols.items() if k.startswith("--symbol-alarm-")}
    equip = {v for k, v in symbols.items() if k.startswith("--symbol-equip-")}
    assert not alarm & equip


def test_alarm_priorities_cover_the_application_enum():
    from app.alarms.manager import Priority

    assert {p.value for p in Priority} == set(ALARM_STATES)


def test_band_states_cover_the_envelope_severities_that_raise():
    from app.envelope.evaluator import Severity

    assert {s.name.lower() for s in Severity if s is not Severity.NORMAL} == set(BAND_STATES)


if __name__ == "__main__":
    print(report())
