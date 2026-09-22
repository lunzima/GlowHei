"""Product verification: the verification strategy as executable assertions.

Reads the built files only and takes no part in building them. What the build
assumed and what ended up on disk can diverge: `hmtx`'s lsb lost touch with
`xMin` after the FontForge transplant, and only reading the product showed it.

    python -m build.verify                     # everything under out/
    python -m build.verify out/xxx.ttc
    python -m build.verify --strict            # warnings count as failures
"""
import argparse
import math
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTCollection, TTFont

from build import assemble, charset, ffsimplify, hint, metrics

OUT = Path(__file__).resolve().parent.parent / "out"

# Size ceiling per product in MB: the budget plus 10%. Going over means the
# simplification regressed or the hinting grew.
# Set from measurement with a little headroom, not from the design estimate:
# that estimate put hinting at 159 B a glyph where it costs 237, and every one
# of these was breached on the first hinted build. Unhinted the three come to
# 1.57, 5.19 and 1.72 MB; hinting roughly doubles them.
SIZE_BUDGET_MB = {
    "GlowHei-GB2312.ttc": 3.2,
    "GlowHei-GBK.ttc": 10.3,
    "GlowHei-ExtA.ttf": 3.6,
}

# Glyphs whose ink reaches past their advance in the source font, and by how
# far in upem-256 units. Measured over the whole charset, not guessed: these
# four are all of them.
#
# They are upstream's design, not this pipeline's doing, and the goal is no
# visible difference from the source, so they are allowed rather than
# corrected. The check still holds them to the measured figure, so
# a regression on these glyphs fails like any other.
#
# **Nine entries have left this table, all of them as a repair landed.** The
# first four went with the thirty half-width symbols - U+2197, U+2198, U+25BC
# and U+25BD overhung by 1.3 units because the source drew them into a 500
# unit cell and the ink ran five units past it. These five went with the
# tiling block: U+2571 to U+2573 are the box-drawing diagonals, which the
# source overhung by 8.2 units so that a run of them would join across the
# cell boundary, and U+25B2 and U+25B3 overhung by 1.3 for the same reason.
# Their outlines now come from a source that draws them inside the cell, so a
# measurement over the whole product puts them at 0.0 and -8.0 units *inside*
# their advance. The allowance is gone rather than left standing.
KNOWN_OVERHANG = {
    0x221A: 9.2,                                    # square root
    0x89E9: 2.3,                                    # a wide ideograph
    0x2167: 2.0, 0x2177: 2.0,                       # Roman numeral eight
}

# How much further than the measured figure a listed glyph may reach before it
# counts as a regression. Rounding through two coordinate systems moves these
# by a fraction of a unit.
OVERHANG_TOLERANCE = 0.5


@dataclass
class Report:
    """One face's results. `errors` fail the run; `warnings` need a human."""

    label: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def check(self, ok: bool, msg: str) -> None:
        if not ok:
            self.fail(msg)


def _is_mono(font: TTFont) -> bool:
    return bool(font["post"].isFixedPitch)


def check_metrics(font: TTFont, rep: Report) -> None:
    """Metrics must match the design.

    **`sTypo*` and `hhea` assert different things and must not be conflated.**
    `sTypo*` is design intent, fixed at the reference face's 220/-36. `hhea`
    follows the
    Windows convention and equals the win metrics computed from the *actual*
    ink. They differ by design, and that is not an error.
    """
    head, hhea, o2 = font["head"], font["hhea"], font["OS/2"]
    rep.check(head.unitsPerEm == assemble.UPEM,
              f"upem={head.unitsPerEm}, expected {assemble.UPEM}")
    rep.check(o2.sTypoAscender == metrics.ASCENT,
              f"sTypoAscender={o2.sTypoAscender}, expected {metrics.ASCENT}")
    rep.check(o2.sTypoDescender == metrics.DESCENT,
              f"sTypoDescender={o2.sTypoDescender}, expected {metrics.DESCENT}")

    # The win metrics must cover the real ink or glyphs get clipped (spec
    # 9.1.2). No fixed value is asserted: it is derived from the ink, and
    # hard-coding it would pass falsely after a source font change.
    top, bottom = _ink_extremes(font)
    rep.check(o2.usWinAscent >= top,
              f"usWinAscent={o2.usWinAscent} does not cover ink top {top}")
    rep.check(o2.usWinDescent >= -bottom,
              f"usWinDescent={o2.usWinDescent} does not cover ink bottom {bottom}")
    rep.check(hhea.ascent == o2.usWinAscent and hhea.descent == -o2.usWinDescent,
              f"hhea({hhea.ascent}/{hhea.descent}) disagrees with win metrics"
              f"({o2.usWinAscent}/{-o2.usWinDescent})")
    line = o2.usWinAscent + o2.usWinDescent + hhea.lineGap
    rep.note(f"line height {line / assemble.UPEM:.3f} em (ink {top} / {bottom})")


def _ink_extremes(font: TTFont) -> tuple[int, int]:
    """Highest and lowest ink over every glyph.

    From the drawn curve, not from `glyph.yMax` and `glyph.yMin`: those give
    the control-point box, which for a quadratic sits outside the curve. The
    build measures the same way, and using the two measures in two places is
    what made the first full build fail.
    """
    glyph_set = font.getGlyphSet()
    top, bottom = 0, 0
    for name in font.getGlyphOrder():
        pen = BoundsPen(glyph_set)
        glyph_set[name].draw(pen)
        if pen.bounds is None:
            continue
        top = max(top, int(pen.bounds[3]))
        bottom = min(bottom, int(pen.bounds[1]))
    return top, bottom


def check_legacy_fields(font: TTFont, rep: Report) -> None:
    """Fields older Windows applications insist on."""
    o2, head, name = font["OS/2"], font["head"], font["name"]
    rep.check(o2.version == 3, f"OS/2 version={o2.version}, expected 3")
    rep.check(o2.fsType == 0, f"fsType={o2.fsType}, expected 0")
    rep.check(o2.fsSelection == metrics.FSSELECTION_REGULAR,
              f"fsSelection={o2.fsSelection:#x}")
    rep.check(head.macStyle == 0, f"macStyle={head.macStyle} disagrees with fsSelection")
    # `maxZones` says whether the interpreter needs a twilight zone. Hinted, it
    # has to be 2, because the bytecode uses one. Unhinted, either value
    # describes a correct font - 1 is the truth, 2 is harmless - but 0 is not:
    # some older rasterisers mishandle it, so that is what is ruled out.
    zones = font["maxp"].maxZones
    if hint.is_hinted(font):
        rep.check(zones == 2, f"maxZones={zones}, expected 2 for a hinted font")
    else:
        rep.check(zones != 0, "maxZones=0; some older rasterisers mishandle it")
    for nid in (1, 2, 3, 4, 6):
        rep.check(bool(name.getDebugName(nid)), f"name is missing ID {nid}")
    family = name.getDebugName(1) or ""
    rep.check(len(family) <= metrics.MAX_FACE_NAME,
              f"family name {family!r} exceeds {metrics.MAX_FACE_NAME} chars")
    rep.check("gasp" in font, "no gasp table")


def check_codepage(font: TTFont, rep: Report, expect_gbk: bool) -> None:
    """ulCodePageRange1 against what the product is meant to declare.

    The GB products declare CP936 without covering it, so this is not an
    honesty check. It is the codepage GDI filters on, and a font that stays
    silent cannot be picked for GB2312_CHARSET at all.
    """
    got = font["OS/2"].ulCodePageRange1
    want = metrics.CODEPAGE_LATIN1 | (metrics.CODEPAGE_GBK if expect_gbk else 0)
    rep.check(got == want, f"ulCodePageRange1={got:#x}, expected {want:#x}")


def check_coverage(font: TTFont, rep: Report, expect: frozenset[int]) -> None:
    """Every target codepoint needs a glyph, and it must not be empty."""
    cmap = font.getBestCmap()
    glyf = font["glyf"]
    missing = sorted(cp for cp in expect if cp not in cmap)
    if missing:
        rep.fail(f"{len(missing)} codepoints missing, first U+{missing[0]:04X}")
    blank = [
        cp for cp in sorted(expect)
        if cp in cmap and cp != 0x20 and cp != 0x3000
        and getattr(glyf[cmap[cp]], "numberOfContours", 0) == 0
    ]
    if blank:
        rep.fail(f"{len(blank)} codepoints are blank, first U+{blank[0]:04X}")


def check_cp936(font: TTFont, rep: Report) -> None:
    """The full build must cover all of CP936."""
    missing = sorted(charset.cp936_codepoints() - set(font.getBestCmap()))
    if missing:
        rep.fail(f"{len(missing)} CP936 characters uncovered, first U+{missing[0]:04X}")


def check_hinting(font: TTFont, rep: Report) -> None:
    """Either the font is hinted throughout or it is not hinted at all.

    A half-hinted font renders its common characters one way and its rare ones
    another, which is worse than either. Latin is exempt: the configuration's
    passes select by unicode range and leave it alone on purpose.
    """
    tables = [tag for tag in ("fpgm", "prep", "cvt ") if tag in font]
    if not tables:
        rep.note("no hinting (built with --no-hinting)")
        return
    if len(tables) != 3:
        rep.fail(f"partial hinting tables: {tables}")
        return

    glyf = font["glyf"]
    cmap = font.getBestCmap()
    han = [name for cp, name in cmap.items() if 0x4E00 <= cp <= 0x9FA5]
    if not han:
        rep.note("no Han in this product; hinting coverage not applicable")
        return
    hinted = 0
    for name in han:
        glyph = glyf[name]
        glyph.expand(glyf)
        program = getattr(glyph, "program", None)
        if program and program.getBytecode():
            hinted += 1
    if hinted < len(han) * 0.95:
        rep.fail(f"only {hinted}/{len(han)} Han glyphs carry instructions")
    else:
        rep.note(f"{hinted}/{len(han)} Han glyphs hinted")


def check_no_layout_tables(font: TTFont, rep: Report) -> None:
    """Kerning is a non-goal: it can only pull a fixed-pitch face off the grid
    it exists to hold."""
    for tag in ("GSUB", "GPOS", "GDEF"):
        if tag in font:
            rep.fail(f"{tag} present")


def check_overlap_flags(font: TTFont, rep: Report) -> None:
    """Flag bit 6 must be clear on every simple glyph.

    The source font sets it almost everywhere and Chlorophytum's writer sets it
    again; ots-sanitize rejects the whole `glyf` table over a handful.
    """
    glyf = font["glyf"]
    offenders = 0
    for name in font.getGlyphOrder():
        glyph = glyf[name]
        glyph.expand(glyf)
        flags = getattr(glyph, "flags", None)
        if flags is None:
            continue
        if any(flag & ffsimplify.OVERLAP_SIMPLE for flag in flags):
            offenders += 1
    rep.check(offenders == 0,
              f"{offenders} glyphs keep OVERLAP_SIMPLE; ots will reject glyf")


def check_widths(font: TTFont, rep: Report) -> None:
    """Two widths only, full 256 and half 128.

    The source font arrives this way - 500 and 1000 at upem 1000 - so this
    checks that nothing in the pipeline disturbed it rather than that anything
    achieved it.

    **The tabular block is asserted by name, and that addition is what the
    earlier version of this check could not see.** `{128, 256}` says an advance
    is one of two widths, not that it is the right one of the two, so the 175
    box drawing, block element and geometric characters sat half width through
    every green run of this function. They are named in `charset` and the rule
    that makes them full width is `is_full_width`, so both are asserted here
    rather than a third hand-written list.
    """
    cmap, hmtx = font.getBestCmap(), font["hmtx"]
    widths = {}
    for codepoint, name in cmap.items():
        widths.setdefault(hmtx[name][0], []).append(codepoint)
    unexpected = {w: cps for w, cps in widths.items() if w not in (128, 256)}
    for width, cps in list(unexpected.items())[:5]:
        rep.fail(f"advance {width} on {len(cps)} glyphs, "
                 f"first U+{cps[0]:04X}")
    for codepoint in range(0x21, 0x7F):
        name = cmap.get(codepoint)
        if name and hmtx[name][0] != 128:
            rep.fail(f"U+{codepoint:04X} advance {hmtx[name][0]}, expected 128")
            break
    for codepoint in sorted(charset.TABULAR_SYMBOLS & set(cmap)):
        if hmtx[cmap[codepoint]][0] != assemble.UPEM:
            rep.fail(f"U+{codepoint:04X} is full width by "
                     f"charset.is_full_width but has advance "
                     f"{hmtx[cmap[codepoint]][0]}")
            break
    rep.note(f"advances in use: {sorted(widths)}")


# How far short of a cell edge a tiling glyph may stop, in upem-256 units.
#
# `refit` shrinks a glyph uniformly until its worst side fits, so the other
# three land just inside the line box rather than on it: measured on the full
# block, the bottom comes to -58.1 against the box's -60. Three units is 1.2%
# of the cell, and a glyph that was not tiled at all misses by a hundred and
# twenty, so nothing this check exists to catch can hide behind it.
REACH_TOLERANCE = 3


def _tiling_edges(codepoint: int) -> frozenset[str]:
    """The cell edges this character's own name says its ink has to reach.

    Read off the Unicode name rather than listed here, so the requirement comes
    from the character's definition instead of from this file's idea of it. A
    box-drawing character reaches exactly the sides it is named for: `LIGHT
    VERTICAL AND RIGHT` meets the right edge and both ends of the line box, and
    not the left edge, which is why centring one of these - the seating the
    other thirty got - would pull the frame apart.

    Two kinds are exempt. A `DASH` is a gap by construction, measured at 56 to
    944 in a 1000 unit cell, so it is required to reach nothing. A `DIAGONAL`
    is required to reach all four, because it is the corner-to-corner ink that
    makes a run of them one line.

    A block element that names no side spans the whole of that axis - `LEFT
    HALF BLOCK` fills the cell's full height and only its left half - while a
    `QUADRANT` names every side it fills and reaches only those.
    """
    name = unicodedata.name(chr(codepoint), "")
    words = set(re.split(r"[^A-Z]+", name))
    if "DASH" in words:
        return frozenset()
    if "DIAGONAL" in words:
        return frozenset({"left", "right", "up", "down"})
    edges = set()
    for word, edge in (("LEFT", "left"), ("RIGHT", "right")):
        if word in words:
            edges.add(edge)
    for word, edge in (("UP", "up"), ("UPPER", "up"),
                       ("DOWN", "down"), ("LOWER", "down")):
        if word in words:
            edges.add(edge)
    if "HORIZONTAL" in words:
        edges |= {"left", "right"}
    if "VERTICAL" in words:
        edges |= {"up", "down"}
    if "BLOCK" in words and "QUADRANT" not in words:
        if not edges & {"left", "right"}:
            edges |= {"left", "right"}
        if not edges & {"up", "down"}:
            edges |= {"up", "down"}
    return frozenset(edges)


# How thick a rule has to be to render as a rule, in upem-256 units.
#
# **This is the check that measures weight rather than geometry, and it is here
# because the geometry check could not see the defect.** A rule whose ink
# reaches both cell edges can still be a ghost: at 0.64 px the rasteriser does
# not draw a thin line, it draws two half lit columns, and a frame whose
# verticals are grey and whose horizontals are black reads as broken. Measured
# on the product before this bound existed: at 22px the vertical rule was 0 of
# 25 sampled rows solid, minimum grey 140, against the horizontal's 66 of 66 at
# grey 0.
#
# 15 units is 0.94 px at 16px, which rounds up to a full pixel; the reference
# face draws the same rule at 18.5, and Sarasa at 17.9. Anything under this
# cannot be solid at the sizes these characters are for, whatever its edges do.
MIN_RULE = 15


def check_rule_weight(font: TTFont, rep: Report) -> None:
    """Every rule must be thick enough to render as a rule.

    The light and heavy horizontal and vertical rules are measured across the
    stroke and held to `MIN_RULE`; a pair is measured bar by bar, because a
    double rule whose bars are 40 units inside a 120 unit pair passes a test
    that only looks at the pair.
    """
    cmap, glyph_set = font.getBestCmap(), font.getGlyphSet()
    # codepoint: (axis the stroke is measured along, minimum number of strokes)
    for codepoint, axis in ((0x2500, "y"), (0x2501, "y"), (0x2502, "x"),
                            (0x2503, "x"), (0x2550, "y"), (0x2551, "x")):
        name = cmap.get(codepoint)
        if name is None:
            continue            # Ext A carries no box drawing, by design
        pen = BoundsPen(glyph_set)
        glyph_set[name].draw(pen)
        if pen.bounds is None:
            rep.fail(f"U+{codepoint:04X} is a rule and draws no ink")
            continue
        lo, bottom, hi, top = pen.bounds
        thickness = (hi - lo) if axis == "x" else (top - bottom)
        strokes = 2 if codepoint in (0x2550, 0x2551) else 1
        # A pair is measured as the whole pair minus its gap; taking the ink's
        # extent and dividing by the stroke count is what catches a pair whose
        # bars are thin however wide the pair is.
        per_stroke = thickness / (2 * strokes - 1)
        if per_stroke < MIN_RULE:
            rep.fail(
                f"U+{codepoint:04X} ({unicodedata.name(chr(codepoint), '?')}) "
                f"measures {per_stroke:.1f} units a stroke, under the "
                f"{MIN_RULE} a rule needs to render solid")
    rep.note(f"rule weight floor {MIN_RULE} units at upem {assemble.UPEM}")


def _polygons(glyph_set, name: str):
    """The glyph as closed polygons, or None if it has any curve in it."""
    recorder = RecordingPen()
    glyph_set[name].draw(recorder)
    out, current = [], None
    for operator, args in recorder.value:
        if operator == "moveTo":
            current = [args[0]]
        elif operator == "lineTo":
            current.append(args[0])
        elif operator == "closePath":
            if current:
                out.append(current)
            current = None
        elif operator in ("curveTo", "qCurveTo"):
            return None
    return out


def _perpendicular_width(points) -> float | None:
    """The gap between a band's two longest parallel edges, or None.

    `area / length` is not this measure: a band whose ends are clipped against
    the cell boundary loses area without getting any narrower, and the two long
    edges' distance is the width itself.
    """
    n = len(points)
    edges = sorted(((points[i], points[(i + 1) % n]) for i in range(n)),
                   key=lambda e: -math.dist(*e))
    if len(edges) < 2:
        return None

    def normal(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        return (-dy / length, dx / length), (-dy * a[0] + dx * a[1]) / length

    (n1, c1) = normal(*edges[0])
    (n2, c2) = normal(*edges[1])
    dot = n1[0] * n2[0] + n1[1] * n2[1]
    if abs(dot) < 0.99:
        return None                 # the two longest edges are not parallel
    return abs(c1 - (1 if dot > 0 else -1) * c2)


def _border_width(polygons) -> float | None:
    """The border of an outlined shape: the difference of the two inradii."""
    if len(polygons) < 2:
        return None

    def inradius(points):
        n = len(points)
        area = abs(sum(points[i][0] * points[(i + 1) % n][1]
                       - points[(i + 1) % n][0] * points[i][1]
                       for i in range(n)) / 2)
        perimeter = sum(math.dist(points[i], points[(i + 1) % n])
                        for i in range(n))
        return 2 * area / perimeter if perimeter else 0.0

    radii = sorted(inradius(p) for p in polygons)
    return radii[-1] - radii[0]


# The three diagonals and the three outlined shapes in the tiling block carry
# no stroke on either axis, so `check_rule_weight` cannot see them: its two
# axes and its ink extents do not describe a band at 45 degrees or a border
# that goes round a triangle. They are measured across their own width instead,
# and held to the same figure as the rules.
BAND_SYMBOLS = (0x2571, 0x2572)                 # the diagonals, not the cross
BORDER_SYMBOLS = (0x25B3, 0x25BD, 0x25C7)       # the white triangles and diamond


def check_diagonal_weight(font: TTFont, rep: Report) -> None:
    """The block's off-axis strokes must weigh what the rules weigh.

    Measured on the product before this bound existed: U+2571 at a
    perpendicular width of 41.8 units against the rules' 70 - 0.67 px at 16px,
    a wash - and the white triangle's border at 36.3, which is 0.58 px. Both
    are the same defect `check_rule_weight` catches on the axes, in shapes its
    two axes cannot describe.
    """
    cmap, glyph_set = font.getBestCmap(), font.getGlyphSet()
    for codepoint in BAND_SYMBOLS:
        name = cmap.get(codepoint)
        if name is None:
            continue            # Ext A carries no box drawing, by design
        polygons = _polygons(glyph_set, name)
        width = _perpendicular_width(polygons[0]) if polygons else None
        if width is None:
            rep.fail(f"U+{codepoint:04X} is a band and has no two parallel "
                     f"long edges to measure")
        elif width < MIN_RULE:
            rep.fail(
                f"U+{codepoint:04X} ({unicodedata.name(chr(codepoint), '?')}) "
                f"measures {width:.1f} units across, under the {MIN_RULE} a "
                f"rule needs to render solid")
    for codepoint in BORDER_SYMBOLS:
        name = cmap.get(codepoint)
        if name is None:
            continue
        polygons = _polygons(glyph_set, name)
        width = _border_width(polygons) if polygons else None
        if width is None:
            continue
        if width < MIN_RULE:
            rep.fail(
                f"U+{codepoint:04X} ({unicodedata.name(chr(codepoint), '?')}) "
                f"has a border {width:.1f} units thick, under the {MIN_RULE} a "
                f"rule needs to render solid")
    measured = sum(1 for cp in BAND_SYMBOLS + BORDER_SYMBOLS if cp in font.getBestCmap())
    rep.note(f"{measured} off-axis strokes measured across, floor {MIN_RULE} units")


def check_hinting_coverage(font: TTFont, rep: Report) -> None:
    """Every glyph that draws ink must carry instructions.

    **This is the debt the rest of this file was built around.** The hinter is
    fed a font and asks a configuration which glyphs to hint; that
    configuration is an allow-list of Unicode blocks - the CJK blocks, kana
    and Hangul - so everything outside them came back bare: 592 glyphs of the
    full build, 175 of them the tiling block. A bare rule cannot be grid
    fitted, so at 16px a 1.12 px stroke that straddles a pixel boundary stays
    two half lit columns instead of being snapped to one, which is the grey
    ghost a rendered frame caught and no geometric check could.

    A glyph with no ink is exempt: there is nothing to fit. So is `.notdef`,
    which the hinter never sees. **And so is the tiling block, which is bare by
    measurement**: three arms were run on the rasteriser and no model renders a
    rule better than leaving it alone - `hm-ideograph` takes the horizontal
    rule at 12px to 224, and ttfautohint is worse than bare in five of six
    cells - so the block is excluded from both sides on purpose. The exemption
    is `hint.UNHINTED_SYMBOLS`, one named range rather than a loosened test.
    """
    if not hint.is_hinted(font):
        rep.note("no hinting (built with --no-hinting); coverage not applicable")
        return
    glyf, cmap = font["glyf"], font.getBestCmap()
    bare = []
    for codepoint, name in cmap.items():
        glyph = glyf[name]
        glyph.expand(glyf)
        if getattr(glyph, "numberOfContours", 0) <= 0:
            continue
        program = getattr(glyph, "program", None)
        if program is None or not program.getBytecode():
            if any(lo <= codepoint <= hi for lo, hi in hint.UNHINTED_SYMBOLS):
                continue
            bare.append(codepoint)
    if bare:
        shown = ", ".join(f"U+{cp:04X}" for cp in sorted(bare)[:6])
        rep.fail(f"{len(bare)} inked glyphs carry no instructions, "
                 f"first {shown}")
    else:
        rep.note("every inked glyph carries instructions")
    # A class is held out whole or not at all: half a class renders one bar
    # black and the one beside it grey.
    for fault in hint.hold_out_faults(sorted(cmap)):
        rep.fail(fault)


def check_tiling(font: TTFont, rep: Report) -> None:
    """A tiling character must reach the cell edges it joins on.

    The width of these characters is only half of what they are for: a run of
    them has to come out as one unbroken rule, which is what the sibling
    project's `bitmap.CONNECTING` names. A full-width advance with the ink
    still drawn to a half cell would pass every width check in this file and
    still break every frame it was used in, so the ink is measured here.

    Horizontal edges are the advance, 0 and 256. Vertical edges are the line
    box, -60 and 232: this project's tabular characters fill the line rather
    than the em, so that a vertical rule reaches into the line above and the
    line below, and `refit` is what puts them there.
    """
    cmap, hmtx = font.getBestCmap(), font["hmtx"]
    glyph_set = font.getGlyphSet()
    edges = {"left": 0.0, "right": float(assemble.UPEM),
             "down": float(metrics.DESCENT), "up": float(metrics.WIN_ASCENT)}
    checked = short = 0
    for codepoint in sorted(charset.TILING_SYMBOLS & set(cmap)):
        pen = BoundsPen(glyph_set)
        glyph_set[cmap[codepoint]].draw(pen)
        if pen.bounds is None:
            rep.fail(f"U+{codepoint:04X} tiles and draws no ink")
            continue
        checked += 1
        lo, bottom, hi, top = pen.bounds
        reached = {"left": lo <= edges["left"] + REACH_TOLERANCE,
                   "right": hi >= edges["right"] - REACH_TOLERANCE,
                   "down": bottom <= edges["down"] + REACH_TOLERANCE,
                   "up": top >= edges["up"] - REACH_TOLERANCE}
        for edge in sorted(_tiling_edges(codepoint)):
            if reached[edge]:
                continue
            short += 1
            if short <= 5:
                want = edges[edge]
                got = {"left": lo, "right": hi,
                       "down": bottom, "up": top}[edge]
                rep.fail(
                    f"U+{codepoint:04X} ({unicodedata.name(chr(codepoint),'?')}) "
                    f"stops {abs(got - want):.1f} short of its {edge} edge "
                    f"({got:.1f} against {want:.0f})")
    rep.note(f"{checked} tiling glyphs measured, {short} edge(s) not reached")


def check_avg_char_width(font: TTFont, rep: Report) -> None:
    """xAvgCharWidth must be the half width, which GDI reports as
    tmAveCharWidth and applications size a character cell from.

    The mean over the glyph set would be 255 here, the font being almost all
    full-width CJK, and every cell computed from it would be twice too wide.
    """
    got = font["OS/2"].xAvgCharWidth
    rep.check(got == metrics.AVG_CHAR_WIDTH,
              f"OS/2.xAvgCharWidth={got}, expected {metrics.AVG_CHAR_WIDTH}")


def check_pitch_flags(font: TTFont, rep: Report) -> None:
    """The two faces differ in these flags and nothing else, as the original does."""
    panose = font["OS/2"].panose
    if _is_mono(font):
        rep.check(font["post"].isFixedPitch == 1,
                  f"monospace face has post.isFixedPitch="
                  f"{font['post'].isFixedPitch}, expected 1")
        rep.check(panose.bProportion == 9,
                  f"monospace face has panose.bProportion={panose.bProportion}")
    else:
        rep.check(panose.bProportion == 0,
                  f"proportional face has panose.bProportion={panose.bProportion}")
        rep.check(font["post"].isFixedPitch == 0,
                  "proportional face has post.isFixedPitch set")


def check_lsb(font: TTFont, rep: Report) -> None:
    """hmtx's lsb must equal xMin.

    When they disagree the spec has renderers shift the glyph by the difference,
    which fontTools does: hiragana `zo` moved 12 units right and burst out of
    its cell. The offset exists only at draw time; reading coordinates hides it.
    """
    glyf, hmtx = font["glyf"], font["hmtx"]
    bad = []
    for name in font.getGlyphOrder():
        g = glyf[name]
        if getattr(g, "numberOfContours", 0) <= 0:
            continue
        if hmtx[name][1] != int(g.xMin):
            bad.append(name)
    if bad:
        rep.fail(f"{len(bad)} glyphs have lsb != xMin, first {bad[0]}")


def check_drawn_bounds(font: TTFont, rep: Report) -> None:
    """Check ink against the advance on the *drawn* outline.

    Every glyph is held to its cell now that latin is half-width too: ink past
    the advance collides with the neighbour, in a document as much as in a
    terminal.

    Sampled rather than exhaustive: BoundsPen over twenty thousand glyphs takes
    tens of seconds, and this is a last safety net.
    """
    cmap, hmtx = font.getBestCmap(), font["hmtx"]
    glyph_set = font.getGlyphSet()
    cps = sorted(cmap)
    # Every known overhang is checked, plus a spread over everything else.
    # Sampling alone would miss thirteen glyphs in twenty thousand.
    sample = sorted(set(cps[::max(1, len(cps) // 2000)])
                    | (set(KNOWN_OVERHANG) & set(cps)))
    bad, checked, inherited = [], 0, 0
    for cp in sample:
        name = cmap[cp]
        adv = hmtx[name][0]
        pen = BoundsPen(glyph_set)
        glyph_set[name].draw(pen)
        if not pen.bounds:
            continue
        checked += 1
        allowed = KNOWN_OVERHANG.get(cp, 0.0) + OVERHANG_TOLERANCE
        overhang = pen.bounds[2] - adv
        if overhang > allowed:
            bad.append(
                f"U+{cp:04X} reaches {overhang:.1f} past its advance, "
                f"allowed {allowed:.1f}"
            )
        elif cp in KNOWN_OVERHANG:
            inherited += 1
    for msg in bad[:5]:
        rep.fail(msg)
    rep.note(f"sampled {len(sample)} glyphs, {checked} of them cell-constrained, "
             f"{inherited} with the source's own overhang")


def verify_font(font: TTFont, label: str, expect: frozenset[int],
                expect_gbk: bool, full_charset: bool) -> Report:
    rep = Report(label)
    check_metrics(font, rep)
    check_legacy_fields(font, rep)
    check_codepage(font, rep, expect_gbk)
    check_coverage(font, rep, expect)
    if full_charset:
        check_cp936(font, rep)
    check_hinting(font, rep)
    check_no_layout_tables(font, rep)
    check_overlap_flags(font, rep)
    check_lsb(font, rep)
    check_drawn_bounds(font, rep)
    check_widths(font, rep)
    check_tiling(font, rep)
    check_rule_weight(font, rep)
    check_diagonal_weight(font, rep)
    check_hinting_coverage(font, rep)
    check_avg_char_width(font, rep)
    check_pitch_flags(font, rep)
    return rep


# {filename: (charset function, declares CP936, must cover all of CP936)}
#
# The GB product declares the codepage without covering it. That is deliberate:
# a font that does not declare it cannot be selected for GB2312_CHARSET at all.
# See metrics.SLIM_PROP.
PRODUCTS = {
    "GlowHei-GB2312.ttc": (charset.slim, True, False),
    "GlowHei-GBK.ttc": (charset.full, True, True),
    "GlowHei-ExtA.ttf": (charset.ext_a, False, False),
}


def ots_sanitize(path: Path, rep: Report) -> None:
    """Run ots-sanitize over the file if WSL has it.

    A structural check nothing else here provides: fontTools round-trips its own
    output happily, which says little about whether a stricter consumer accepts
    it. Skipped with a note when the tool is absent, since it is not required to
    build.
    """
    try:
        result = subprocess.run(
            ["wsl", "-e", "ots-sanitize", ffsimplify.wsl_path(path), "/dev/null"],
            capture_output=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        rep.note("ots-sanitize unavailable, structural check skipped")
        return
    if result.returncode == 0:
        rep.note("ots-sanitize passed")
    else:
        detail = (result.stdout + result.stderr).decode("utf-8", "replace")
        rep.fail(f"ots-sanitize rejected the file: {detail.strip()[:200]}")


def verify_product(path: Path) -> list[Report]:
    spec = PRODUCTS.get(path.name)
    if spec is None:
        return [Report(path.name, errors=[f"unknown product {path.name}"])]
    charset_fn, expect_gbk, full_charset = spec
    expect = charset_fn()

    fonts = (TTCollection(str(path)).fonts if path.suffix.lower() == ".ttc"
             else [TTFont(str(path))])
    reports = []
    for i, font in enumerate(fonts):
        label = f"{path.name}[{i}] {font['name'].getDebugName(1)}"
        reports.append(
            verify_font(font, label, expect, expect_gbk, full_charset)
        )

    rep = Report(f"{path.name} overall")
    ots_sanitize(path, rep)
    size_mb = path.stat().st_size / 1048576
    budget = SIZE_BUDGET_MB.get(path.name)
    rep.note(f"{size_mb:.2f} MB, {len(fonts)} face(s)")
    if budget and size_mb > budget:
        rep.fail(f"{size_mb:.2f} MB exceeds the {budget} MB budget")
    if len(fonts) > 1:
        loca = {f["head"].indexToLocFormat for f in fonts}
        rep.check(len(loca) == 1,
                  f"faces disagree on indexToLocFormat {loca}")
    reports.append(rep)
    return reports


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify the GlowSong products")
    ap.add_argument("paths", nargs="*", type=Path,
                    help="products to verify; omit for everything under out/")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = ap.parse_args(argv)

    paths = args.paths or [OUT / n for n in PRODUCTS if (OUT / n).exists()]
    if not paths:
        print(f"nothing to verify in {OUT}", file=sys.stderr)
        return 1

    errors = warnings = 0
    for path in paths:
        for rep in verify_product(path):
            mark = "FAIL" if rep.errors else ("WARN" if rep.warnings else "ok")
            print(f"[{mark:4}] {rep.label}")
            for msg in rep.notes:
                print(f"         · {msg}")
            for msg in rep.warnings:
                print(f"         ! {msg}")
            for msg in rep.errors:
                print(f"         X {msg}")
            errors += len(rep.errors)
            warnings += len(rep.warnings)

    print(f"\n{errors} error(s), {warnings} warning(s)")
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
