#!/usr/bin/env python3
"""Build the animated HUGO.CLI card for the profile README.

Writes card-dark.svg and card-light.svg next to this file. The card replays a
short HUGO.CLI session (boot, a typed command, its output), then holds on the
last frame with a blinking caret.

Sources, all in this folder:

  session.json        what the terminal says, and when it says it
  dark.tokens.json    colours, one file per theme (Design Tokens format 2025.10)
  light.tokens.json
  fonts/              JetBrains Mono, variable (SIL OFL 1.1)

Rules the output follows:

- Text is drawn as glyph outlines read from the font file, because an SVG shown
  through <img> cannot load a webfont. Each glyph is defined once in <defs>
  and placed with <use>.
- Motion is CSS keyframes in discrete steps. Nothing is interpolated and the
  caret is the only animation that loops. Without animation support, or with
  prefers-reduced-motion, the card shows its last frame.
- Both themes share one layout and one timeline. Only the colour classes
  differ.
- Sizes and rhythm are the CSS values of the TUI view on hugo.mourlev.at, and
  the "at" times in session.json are the cadence measured there. Tune the
  playback with the "timing" block, not by editing this file.

Usage:
    pip install -r hugo-cli/requirements.txt
    python hugo-cli/build.py
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = Path(__file__).resolve().parent
FONT = HERE / "fonts" / "JetBrainsMono-Variable.ttf"
THEMES = ("dark", "light")

# --- Geometry (px) ----------------------------------------------------------
W = 688               # the README column of a GitHub profile, so the card shows 1:1
PAD = 24
RADIUS = 10

SIZE = 13.6           # body text (0.85rem)
SMALL = 9.6           # labels and chips (0.6rem)
INPUT = 16.0          # the prompt line (1rem)
ADVANCE = 0.6         # JetBrains Mono advance width, in em
CELL = SIZE * ADVANCE
PITCH = 23.8          # one terminal row: 22.44 line-height + 1.36 margin
BASELINE = 16.1       # from the top of a row down to its baseline
TRACKING = 0.96       # letter-spacing of the uppercase labels (0.1em)

TOPBAR = 42
TOPBAR_BASELINE = 25.8
CHIP_H = 21
CHIP_Y = 10.5
CHIP_BASELINE = 24.4
CHIP_GAP = 8

OUT_TOP = TOPBAR + 1 + 12          # under the top rule, 12px of padding
BOOT_TOP = OUT_TOP + 4
USER_TOP = OUT_TOP + 6
USER_H = 58
LABEL_BASELINE = 11.4              # a 9.6px label inside its 15.84px line
REPLY_TOP = USER_TOP + USER_H + 6.8
ROWS_TOP = REPLY_TOP + 8 + 15.84 + 3.84

LOGO_COL = 1                       # one cell in, as in the TUI
INFO_GAP = 7                       # cells between the logo and the info column
CHECK_VALUE_COL = 8                # boot lines: where the value starts
CHECK_STATUS_COL = 44              # boot lines: where [OK] starts

CARET_W = 1
CARET_UP = 15                      # caret extent above and below the baseline
CARET_DOWN = 4
BLINK = "1.06s"

# --- Colour classes ---------------------------------------------------------
# class -> (token path, CSS property). The two themes differ only here.
PAINT = {
    "cv": ("canvas", "fill"),
    "fr": ("line.frame", "stroke"),
    "ch": ("line.chip", "stroke"),
    "hl": ("line.hairline", "fill"),
    "ed": ("line.edge", "fill"),
    "s": ("text.strong", "fill"),
    "d": ("text.default", "fill"),
    "m": ("text.dim", "fill"),
    "f": ("text.faint", "fill"),
    "p": ("text.placeholder", "fill"),
    "a": ("accent.default", "fill"),
    "as": ("accent.subtle", "fill"),
    "ab": ("accent.border", "stroke"),
    "ok": ("success", "fill"),
    "rs": ("surface.raised", "fill"),
}

# Tone names used in session.json -> colour class.
TONES = {"strong": "s", "default": "d", "dim": "m", "accent": "a", "ok": "ok"}

# Every animation holds one state for a window of time, and outside that window
# the element falls back to its resting style. Nothing relies on the value an
# animation ends on: a short "appear" step with a forwards fill can land on
# either side of a rounding error and leave the element hidden for good.
MOTION = (
    ".w{opacity:0;animation:w linear}"
    ".o{animation:o linear}"
    f".k{{animation:k {BLINK} step-end infinite}}"
    ".ty{opacity:0;animation-name:w,ty;animation-timing-function:linear,step-end}"
    "@keyframes w{from{opacity:1}to{opacity:1}}"
    "@keyframes o{from{opacity:0}to{opacity:0}}"
    "@keyframes k{50%{opacity:0}}"
)
REDUCED = "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"


def n(value: float, digits: int = 2) -> str:
    """A number as short as SVG allows."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


# --- Motion -----------------------------------------------------------------
# Each helper returns (class, inline style). Times are milliseconds from load.


def during(start: int, end: int) -> tuple[str, str]:
    """Visible only between start and end."""
    return "w", f"animation-delay:{start}ms;animation-duration:{end - start}ms"


def except_during(start: int, end: int) -> tuple[str, str]:
    """Visible all the time, except between start and end."""
    delay = f"animation-delay:{start}ms;" if start else ""
    return "o", f"{delay}animation-duration:{end - start}ms"


def at(t: int) -> tuple[str, str]:
    """Appears at t and stays: hidden from load until then."""
    return except_during(0, t)


def attrs(*classes: str, motion: tuple[str, str] | None = None) -> str:
    names = [c for c in classes if c]
    style = ""
    if motion:
        names.append(motion[0])
        style = f' style="{motion[1]}"'
    return (f' class="{" ".join(names)}"' if names else "") + style


def group(children: str, motion: tuple[str, str] | None = None) -> str:
    return f"<g{attrs(motion=motion)}>{children}</g>"


# --- Type -------------------------------------------------------------------


class Glyphs:
    """Glyph outlines, registered on first use and emitted once in <defs>."""

    def __init__(self, path: Path) -> None:
        self.font = TTFont(path)
        self.cmap = self.font.getBestCmap()
        self.upm = self.font["head"].unitsPerEm
        self.used: dict[tuple[int, int], str] = {}

    def ref(self, char: str, weight: int) -> str:
        code = ord(char)
        if code not in self.cmap:
            raise SystemExit(f"JetBrains Mono has no glyph for {char!r} (U+{code:04X})")
        return self.used.setdefault((weight, code), f"{'b' if weight >= 600 else 'r'}{code:x}")

    def defs(self) -> str:
        scale = SIZE / self.upm
        sets: dict[int, object] = {}
        paths = []
        for (weight, code), ident in sorted(self.used.items()):
            glyphs = sets.setdefault(weight, self.font.getGlyphSet(location={"wght": weight}))
            pen = SVGPathPen(glyphs, ntos=n)
            glyphs[self.cmap[code]].draw(TransformPen(pen, (scale, 0, 0, -scale, 0, 0)))
            paths.append(f'<path id="{ident}" d="{pen.getCommands()}"/>')
        return "".join(paths)

    def contours(self, char: str, weight: int = 400) -> list[list[tuple[float, float]]] | None:
        """The contours of a glyph in px, y down, or None if any edge is curved."""
        if char == " ":
            return []
        code = ord(char)
        if code not in self.cmap:
            raise SystemExit(f"JetBrains Mono has no glyph for {char!r} (U+{code:04X})")
        glyphs = self.font.getGlyphSet(location={"wght": weight})
        pen = DecomposingRecordingPen(glyphs)
        glyphs[self.cmap[code]].draw(pen)
        scale = SIZE / self.upm
        found: list[list[tuple[float, float]]] = []
        for op, points in pen.value:
            if op == "moveTo":
                found.append([])
            elif op not in ("lineTo", "closePath", "endPath"):
                return None
            if op in ("moveTo", "lineTo"):
                x, y = points[0]
                found[-1].append((x * scale, -y * scale))
        return found


def winding(contour: list[tuple[float, float]], px: float, py: float) -> int:
    """Winding number of a polygon around a point that is not on its edges."""
    turns = 0
    for (x0, y0), (x1, y1) in zip(contour, contour[1:] + contour[:1]):
        if (y0 <= py) != (y1 <= py):
            if x0 + (py - y0) * (x1 - x0) / (y1 - y0) > px:
                turns += 1 if y1 > y0 else -1
    return turns


def outline(shapes: list[list[list[tuple[float, float]]]]) -> str:
    """Union of axis-aligned shapes as a single path.

    Glyphs set side by side overlap by a fraction of a pixel, and anti-aliasing
    turns every such joint into a hairline. One outline per row has no joints.
    Each shape is the list of contours of one glyph.
    """
    xs = sorted({round(x, 3) for shape in shapes for contour in shape for x, _ in contour})
    ys = sorted({round(y, 3) for shape in shapes for contour in shape for _, y in contour})
    filled = set()
    for i in range(len(xs) - 1):
        cx = (xs[i] + xs[i + 1]) / 2
        for j in range(len(ys) - 1):
            cy = (ys[j] + ys[j + 1]) / 2
            if any(sum(winding(c, cx, cy) for c in shape) for shape in shapes):
                filled.add((i, j))

    # Edges between a filled cell and an empty one, clockwise around the fill.
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for i, j in sorted(filled):  # sorted, so the path is the same on every run
        if (i, j - 1) not in filled:
            edges.setdefault((i, j), []).append((i + 1, j))
        if (i + 1, j) not in filled:
            edges.setdefault((i + 1, j), []).append((i + 1, j + 1))
        if (i, j + 1) not in filled:
            edges.setdefault((i + 1, j + 1), []).append((i, j + 1))
        if (i - 1, j) not in filled:
            edges.setdefault((i, j + 1), []).append((i, j))

    commands = []
    while edges:
        start = point = next(iter(edges))
        loop = [point]
        while True:
            following = edges[point].pop()
            if not edges[point]:
                del edges[point]
            point = following
            if point == start:
                break
            loop.append(point)
        # Keep the corners only.
        corners = [
            p for k, p in enumerate(loop)
            if (loop[k - 1][0] == p[0]) != (p[0] == loop[(k + 1) % len(loop)][0])
        ]
        (i, j), rest = corners[0], corners[1:]
        commands.append(f"M{n(xs[i])} {n(ys[j])}")
        last = (i, j)
        for i, j in rest:
            commands.append(f"H{n(xs[i])}" if j == last[1] else f"V{n(ys[j])}")
            last = (i, j)
        commands.append("Z")
    return "".join(commands)


def width(text: str, size: float = SIZE, tracking: float = 0.0) -> float:
    return len(text) * (size * ADVANCE + tracking)


def run(
    glyphs: Glyphs,
    x: float,
    y: float,
    text: str,
    tone: str,
    *,
    weight: int = 400,
    size: float = SIZE,
    tracking: float = 0.0,
    slant: bool = False,
    motion: tuple[str, str] | None = None,
    each: list[tuple[str, str]] | None = None,
) -> str:
    """One run of text in one tone, its baseline starting at (x, y).

    `each` gives every character its own motion, for text that is typed.
    """
    k = size / SIZE
    step = CELL + tracking / k
    uses = []
    for index, char in enumerate(text):
        if char == " ":
            continue
        pos = f' x="{n(index * step)}"' if index else ""
        own = attrs(motion=each[index]) if each else ""
        uses.append(f'<use href="#{glyphs.ref(char, weight)}"{pos}{own}/>')
    transform = f"translate({n(x)} {n(y)})"
    if k != 1:
        transform += f" scale({n(k, 4)})"
    if slant:
        transform += " skewX(-10)"
    return f'<g{attrs(tone, motion=motion)} transform="{transform}">{"".join(uses)}</g>'


def line(glyphs: Glyphs, x: float, y: float, segments: list[tuple]) -> str:
    """Runs set one after the other on the same baseline: (tone, text[, weight])."""
    out, col = [], 0
    for tone, text, *rest in segments:
        out.append(run(glyphs, x + col * CELL, y, text, tone, weight=rest[0] if rest else 400))
        col += len(text)
    return "".join(out)


def logo_line(glyphs: Glyphs, x: float, y: float, text: str) -> str:
    """One row of the block-letter logo, as a single outline when it can be."""
    shapes = []
    for index, char in enumerate(text):
        contours = glyphs.contours(char)
        if contours is None:
            return run(glyphs, x, y, text, "a")
        if contours:
            shapes.append([[(px + index * CELL, py) for px, py in contour] for contour in contours])
    return f'<path class="a" transform="translate({n(x)} {n(y)})" d="{outline(shapes)}"/>'


def chip_width(text: str, pad: float) -> int:
    return round(width(text, SMALL) + 2 * pad + 2)


def chip(glyphs: Glyphs, x: float, text: str, tone: str, box: str, pad: float) -> str:
    """A small outlined label in the top bar. `box` holds its fill and stroke classes."""
    fill = "" if " " in box else ' fill="none"'
    rect = (
        f'<rect class="{box}"{fill} x="{n(x + 0.5)}" y="{CHIP_Y}" '
        f'width="{chip_width(text, pad) - 1}" height="{CHIP_H - 1}" rx="3"/>'
    )
    return rect + run(glyphs, x + 1 + pad, CHIP_BASELINE, text, tone, size=SMALL)


# --- Tokens -----------------------------------------------------------------


def load_tokens(theme: str) -> dict[str, tuple[str, float]]:
    """Flatten a tokens file to {'text.dim': ('#ffffff', 0.3), ...}."""
    tree = json.loads((HERE / f"{theme}.tokens.json").read_text(encoding="utf-8"))
    flat: dict[str, tuple[str, float]] = {}

    def walk(node: dict, path: list[str], kind: str | None) -> None:
        kind = node.get("$type", kind)
        if "$value" in node:
            value = node["$value"]
            name = ".".join(path)
            if kind != "color" or value.get("colorSpace") != "srgb":
                raise SystemExit(f"{theme}.tokens.json: {name} must be an sRGB colour token")
            r, g, b = (round(float(c) * 255) for c in value["components"])
            flat[name] = (f"#{r:02x}{g:02x}{b:02x}", float(value.get("alpha", 1)))
            return
        for key, child in node.items():
            if not key.startswith("$") and isinstance(child, dict):
                walk(child, path + [key], kind)

    walk(tree["color"], [], tree["color"].get("$type"))
    return flat


def paint_css(theme: str) -> str:
    tokens = load_tokens(theme)
    rules = []
    for name, (path, prop) in PAINT.items():
        colour, alpha = tokens[path]
        rule = f"{prop}:{colour}"
        if alpha < 1:
            rule += f";{prop}-opacity:{n(alpha)}"
        rules.append(f".{name}{{{rule}}}")
    return "".join(rules)


# --- The card ---------------------------------------------------------------


def compose(session: dict, glyphs: Glyphs) -> tuple[str, float, str]:
    """Lay the session out. Returns the SVG body, its height and extra CSS."""
    timing = session["timing"]
    speed = timing["boot_speed"]
    command = session["command"]
    fetch = session["neofetch"]
    logo = [row.rstrip() for row in session["logo"]]

    # Timeline (ms).
    t_boot = [round(entry["at"] * speed) for entry in session["boot"]]
    t_clear = round(session["boot_ends_at"] * speed)
    t_keys = [t_clear + timing["pause_before_typing"] + i * timing["key"] for i in range(len(command))]
    t_enter = t_keys[-1] + timing["key"] + timing["pause_before_enter"]
    t_reply = t_enter + timing["thinking"]

    key_w = max(len(row[0]) for row in fetch["rows"]) + 2
    info = [[("s", fetch["user"], 700), ("m", "@"), ("a", fetch["host"])], None]
    for name, value, *note in fetch["rows"]:
        row = [("a", f"{name}:".ljust(key_w)), ("s", value)]
        if note:
            row.append(("m", " " + note[0]))
        info.append(row)
    info_w = max(sum(len(seg[1]) for seg in row) for row in info if row)
    rows = max(len(info), len(logo))
    logo_row = (rows - len(logo)) // 2
    info_x = PAD + (LOGO_COL + max(map(len, logo)) + INFO_GAP) * CELL

    input_rule = round(ROWS_TOP + rows * PITCH + 16) + 0.5
    input_baseline = input_rule + 0.5 + 12 + 18.56
    input_x = round(PAD + INPUT * ADVANCE + 8)
    height = round(input_rule + 0.5 + 12 + 25.6 + 12)
    out: list[str] = []

    # Window.
    out.append(f'<rect class="cv fr" x=".5" y=".5" width="{W - 1}" height="{height - 1}" rx="{RADIUS}"/>')

    # Top bar: name and version on the left, model and site on the right.
    bar = session["topbar"]
    x = PAD
    for text, tone in (("HUGO", "s"), (".", "a"), ("CLI", "s")):
        out.append(run(glyphs, x, TOPBAR_BASELINE, text, tone, weight=700, tracking=0.272))
        x += width(text, tracking=0.272)
    out.append(chip(glyphs, round(x + 10), bar["version"], "f", "ch", 6))
    site = bar["site"] + " ↗"
    site_x = W - PAD - chip_width(site, 8)
    out.append(chip(glyphs, site_x, site, "m", "rs ch", 8))
    out.append(chip(glyphs, site_x - CHIP_GAP - chip_width(bar["model"], 8), bar["model"], "a", "as ab", 8))
    out.append(f'<rect class="hl" x="{PAD}" y="{TOPBAR}" width="{W - 2 * PAD}" height="1"/>')

    # Boot: each line shows from its own time until the screen clears.
    for index, entry in enumerate(session["boot"]):
        y = BOOT_TOP + index * PITCH + BASELINE
        if "check" in entry:
            label, value = entry["check"]
            parts = (
                run(glyphs, PAD, y, f"{label}:", "s")
                + run(glyphs, PAD + CHECK_VALUE_COL * CELL, y, value, "m")
                + run(glyphs, PAD + CHECK_STATUS_COL * CELL, y, "[OK]", "ok")
            )
        elif entry["line"]:
            parts = line(glyphs, PAD, y, [(TONES[tone], text) for tone, text in entry["line"]])
        else:
            continue
        out.append(group(parts, during(t_boot[index], t_clear)))

    # The command, once sent: a user message.
    inner = W - 2 * PAD
    block = (
        f'<path class="rs" d="M{PAD} {USER_TOP}h{inner - 4}a4 4 0 0 1 4 4v{USER_H - 8}a4 4 0 0 1-4 4h-{inner - 4}z"/>'
        f'<rect class="ed" x="{PAD}" y="{USER_TOP}" width="2" height="{USER_H}"/>'
        + run(glyphs, PAD + 14, USER_TOP + 8 + LABEL_BASELINE, "YOU", "f", size=SMALL, tracking=TRACKING)
        + run(glyphs, PAD + 14, USER_TOP + 8 + 15.84 + 2.4 + BASELINE, command, "d")
    )
    out.append(group(block, at(t_enter)))

    # A beat of thinking, then the reply.
    y = REPLY_TOP + 8 + BASELINE
    dots = "".join(
        run(glyphs, PAD + (8 + i) * CELL, y, ".", "m", motion=during(t_enter + (i + 1) * timing["thinking"] // 4, t_reply))
        for i in range(3)
    )
    out.append(group(run(glyphs, PAD, y, "Thinking", "m"), during(t_enter, t_reply)) + dots)

    label_y = REPLY_TOP + 8
    label = f'<circle class="a" cx="{n(PAD + 2.2)}" cy="{n(label_y + 7.9)}" r="2.2"/>' + run(
        glyphs, PAD + 8.2, label_y + LABEL_BASELINE, session["title"].upper(), "a", size=SMALL, tracking=TRACKING
    )
    out.append(group(label, at(t_reply)))

    for index in range(rows):
        top = ROWS_TOP + index * PITCH
        y = top + BASELINE
        parts = ""
        if 0 <= index - logo_row < len(logo):
            parts += logo_line(glyphs, PAD + LOGO_COL * CELL, y, logo[index - logo_row])
        if index < len(info):
            if info[index] is None:
                parts += f'<rect class="m" x="{n(info_x)}" y="{n(top + 11)}" width="{n(info_w * CELL)}" height="1"/>'
            else:
                parts += line(glyphs, info_x, y, info[index])
        out.append(group(parts, at(t_reply + index * timing["row"])))

    # Prompt line. The placeholder gives way while the command is typed.
    out.append(f'<rect class="hl" x="{PAD}" y="{n(input_rule - 0.5)}" width="{inner}" height="1"/>')
    out.append(run(glyphs, PAD, input_baseline, "❯", "a", weight=700, size=INPUT))
    out.append(
        run(glyphs, input_x, input_baseline, session["placeholder"], "p", size=INPUT, slant=True,
            motion=except_during(t_keys[0], t_enter))
    )
    out.append(
        run(glyphs, input_x, input_baseline, command, "s", size=INPUT, each=[during(t, t_enter) for t in t_keys])
    )
    caret = f'x="0" y="{-CARET_UP}" width="{CARET_W}" height="{CARET_UP + CARET_DOWN}"'
    span = t_enter - t_clear
    out.append(
        f'<g transform="translate({input_x} {round(input_baseline)})">'
        f'<rect class="s ty" style="animation-delay:{t_clear}ms;animation-duration:{span}ms" {caret}/>'
        f'<g{attrs(motion=at(t_enter))}><rect class="s k" style="animation-delay:{t_enter}ms" {caret}/></g>'
        "</g>"
    )

    # The typing caret steps one cell per key.
    stops = ["0%{transform:translateX(0)}"]
    for count, t in enumerate(t_keys, start=1):
        stops.append(f"{n((t - t_clear) / span * 100)}%{{transform:translateX({n(count * INPUT * ADVANCE)}px)}}")
    stops.append(f"to{{transform:translateX({n(len(command) * INPUT * ADVANCE)}px)}}")
    return "".join(out), height, f"@keyframes ty{{{''.join(stops)}}}"


def render(theme: str, session: dict, glyphs: Glyphs, body: str, height: float, extra: str) -> str:
    title = html.escape(session["title"])
    desc = html.escape(session["alt"])
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" viewBox="0 0 {W} {height}" '
        'role="img" aria-labelledby="t d">'
        f'<title id="t">{title}</title><desc id="d">{desc}</desc>'
        f"<style>{paint_css(theme)}{MOTION}{extra}{REDUCED}</style>"
        f"<defs>{glyphs.defs()}</defs>"
        f"{body}</svg>\n"
    )


def main() -> None:
    session = json.loads((HERE / "session.json").read_text(encoding="utf-8"))
    glyphs = Glyphs(FONT)
    body, height, extra = compose(session, glyphs)
    for theme in THEMES:
        target = HERE / f"card-{theme}.svg"
        target.write_text(render(theme, session, glyphs, body, height, extra), encoding="utf-8")
        print(f"{target.relative_to(HERE.parent)}  {target.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
