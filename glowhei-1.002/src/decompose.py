"""Flattening composite glyphs into simple ones.

This runs before FontForge. `ffsimplify` transplants only `glyf` back from
FontForge's output, and FontForge rewrites the glyph order; a composite that
returned still holding its old component names makes fontTools raise KeyError
when it writes the table. The source has 87 composites, 0.4% of the font, so
flattening them costs almost nothing.
"""
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont


def composite_names(font: TTFont) -> list[str]:
    glyf = font["glyf"]
    return [name for name in font.getGlyphOrder() if glyf[name].isComposite()]


def decompose(font: TTFont) -> int:
    """Replace every composite glyph with an equivalent simple one, in place.

    Returns the number replaced. Every glyph is drawn through one glyph set
    captured before any replacement happens, so a composite whose component is
    itself a composite still resolves - writing back as we went would have the
    second one read a half-rebuilt first one.
    """
    names = composite_names(font)
    if not names:
        return 0

    glyph_set = font.getGlyphSet()
    glyf = font["glyf"]
    rebuilt = {}
    for name in names:
        recorder = DecomposingRecordingPen(glyph_set)
        glyph_set[name].draw(recorder)
        pen = TTGlyphPen(None)
        recorder.replay(pen)
        glyph = pen.glyph()
        glyph.recalcBounds(glyf)
        rebuilt[name] = glyph

    hmtx = font["hmtx"]
    for name, glyph in rebuilt.items():
        glyf[name] = glyph
        advance, _ = hmtx[name]
        xmin = int(glyph.xMin) if glyph.numberOfContours > 0 else 0
        hmtx[name] = (advance, xmin)
    return len(names)
