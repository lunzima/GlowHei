"""Pulling the few glyphs that overshoot the line box back into it.

The source font draws its box-drawing characters to fill a 1.25 em line box,
because that is the line height it was designed for. This project's line height
is 1.141 em, and about 152 glyphs - 0.7% - stick out of the smaller
box.

Scaling the whole font down instead was evaluated and rejected: it would also
shrink the Han ink span from 245 to 229 units, 6.7% under the reference, and
the source's span already matches it exactly.
"""
from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

ASCENT = 232
DESCENT = -60
# Where the tabular characters are scaled about. Vertical rules join across
# lines, so both halves have to shrink by the same amount; scaling about the
# baseline would not do that and the seam would step.
CENTRE = (ASCENT + DESCENT) // 2        # 86

TABULAR = frozenset(range(0x2500, 0x25A0))

# Integer rounding can leave a scaled glyph a unit outside the box. Rather than
# reason about where, shrink a hair more and look again.
NUDGE = 0.999
MAX_PASSES = 4


def _bounds(glyph_set, name):
    pen = BoundsPen(glyph_set)
    glyph_set[name].draw(pen)
    return pen.bounds


def _scale(font: TTFont, name: str, factor: float, pivot: int) -> None:
    glyph_set = font.getGlyphSet()
    transform = (Transform()
                 .translate(0, pivot)
                 .scale(1, factor)
                 .translate(0, -pivot))
    pen = TTGlyphPen(None)
    glyph_set[name].draw(TransformPen(pen, transform))
    glyph = pen.glyph()
    glyph.recalcBounds(font["glyf"])
    font["glyf"][name] = glyph


def _factor(ymin: float, ymax: float, pivot: int,
            ascent: int, descent: int) -> float:
    """The vertical scale that brings the worse side just inside the box."""
    over = 1.0
    if ymax > ascent:
        over = max(over, (ymax - pivot) / (ascent - pivot))
    if ymin < descent:
        over = max(over, (ymin - pivot) / (descent - pivot))
    return 1.0 / over


def refit(font: TTFont, ascent: int = ASCENT, descent: int = DESCENT) -> int:
    """Scale every overshooting glyph until its ink fits. Returns the count.

    `hmtx`'s lsb is resynced at the end for the same reason `ffsimplify` does
    it: fontTools shifts a glyph at draw time by lsb minus xMin, and an lsb
    left behind moves the glyph sideways in a way no coordinate check can see.
    """
    reverse = {}
    for codepoint, name in font.getBestCmap().items():
        reverse.setdefault(name, codepoint)

    fixed = 0
    for name in font.getGlyphOrder():
        bounds = _bounds(font.getGlyphSet(), name)
        if bounds is None:
            continue
        _, ymin, _, ymax = bounds
        if ymin >= descent and ymax <= ascent:
            continue

        pivot = CENTRE if reverse.get(name) in TABULAR else 0
        for attempt in range(MAX_PASSES):
            factor = _factor(ymin, ymax, pivot, ascent, descent)
            _scale(font, name, factor * (NUDGE ** attempt), pivot)
            _, ymin, _, ymax = _bounds(font.getGlyphSet(), name)
            if ymin >= descent and ymax <= ascent:
                break
        else:
            raise RuntimeError(
                f"{name} still outside {descent}..{ascent} after "
                f"{MAX_PASSES} passes: {ymin}..{ymax}"
            )
        fixed += 1

    sync_lsb(font)
    return fixed


def sync_lsb(font: TTFont) -> int:
    """Align each hmtx lsb with its glyph's actual xMin. Returns the count."""
    glyf, hmtx = font["glyf"], font["hmtx"]
    fixed = 0
    for name in font.getGlyphOrder():
        glyph = glyf[name]
        xmin = int(glyph.xMin) if getattr(glyph, "numberOfContours", 0) > 0 else 0
        advance, lsb = hmtx[name]
        if lsb != xmin:
            hmtx[name] = (advance, xmin)
            fixed += 1
    return fixed
