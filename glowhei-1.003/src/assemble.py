"""The pipeline, and the collection that carries two faces of it.

The two faces differ in `post.isFixedPitch` and `panose.bProportion` and in
nothing else. Outlines, metrics and hinting are the same bytes, so the
collection shares those tables and stores them once.
"""
import io
from pathlib import Path

from fontTools.ttLib import TTCollection, TTFont
from fontTools.ttLib.scaleUpem import scale_upem

from build import (charset, decompose, ffsimplify, hansans, hint, metrics,
                   refit, sarasa)

UPEM = 256


def build_outlines(codepoints, *, use_fontforge: bool = True,
                   workdir: Path | None = None) -> TTFont:
    """Steps 1 to 5: from the source font to finished outlines at upem 256."""
    font = sarasa.subset(sarasa.open_source(), codepoints)
    # Composite glyphs are flattened *before* the transplant, and the order is
    # load bearing. Accented letters are built from these codepoints: `Agrave`
    # is `A` plus U+02CB, `i` is `dotlessi` plus U+02D9, and 222 composites in
    # the source reference one of the thirty. Flattening after the transplant
    # would hand every one of them a full-width accent on a half-width letter -
    # measured, `Egrave` came out 50 units past its advance.
    decompose.decompose(font)
    # The one step that does not come from the Sarasa subset. Two batches are
    # half width in that source and full width by the rule this project decides
    # a width by - thirty CP936 symbols, and the box drawing, block elements
    # and geometric shapes - so both their outline and their advance are
    # replaced here, before anything is scaled or simplified; `hansans` says
    # why, and why the second batch is seated differently. It is a no-op for a
    # charset without them, which is Ext A.
    hansans.adopt(font, charset.CP936_SYMBOLS | charset.TABULAR_SYMBOLS)
    scale_upem(font, UPEM)
    if use_fontforge and ffsimplify.available():
        ffsimplify.simplify(font, workdir=workdir)
    else:
        # simplify() would have done this on the way past. Without it the flag
        # the source sets on almost every glyph would reach the product, and
        # ots-sanitize rejects the whole table over it.
        ffsimplify.clear_overlap_flags(font)
    refit.refit(font)
    return font


def clone(font: TTFont) -> TTFont:
    """An independent copy, by way of the wire format.

    Two TTFont objects over the same tables would share every edit, and the
    second face's metrics would overwrite the first's.
    """
    buffer = io.BytesIO()
    font.save(buffer)
    buffer.seek(0)
    return TTFont(buffer)


def build_single(codepoints, spec: metrics.FontSpec, *,
                 use_fontforge: bool = True, use_hinting: bool = True,
                 workdir: Path | None = None) -> TTFont:
    font = build_outlines(codepoints, use_fontforge=use_fontforge,
                          workdir=workdir)
    if use_hinting and hint.available():
        font = hint.apply(font, workdir=workdir)
    metrics.apply(font, spec)
    return font


def build_pair(codepoints, prop: metrics.FontSpec, mono: metrics.FontSpec, *,
               use_fontforge: bool = True, use_hinting: bool = True,
               workdir: Path | None = None) -> TTCollection:
    """Both faces of one charset.

    Hinting runs once, on the shared outlines, before the faces diverge: it
    reads only the outlines, and running it twice would cost twice as long for
    byte-identical results.
    """
    font = build_outlines(codepoints, use_fontforge=use_fontforge,
                          workdir=workdir)
    if use_hinting and hint.available():
        font = hint.apply(font, workdir=workdir)

    first, second = font, clone(font)
    metrics.apply(first, prop)
    metrics.apply(second, mono)

    collection = TTCollection()
    collection.fonts = [first, second]
    return collection


def save_ttc(collection: TTCollection, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    collection.save(str(path))
    return path.stat().st_size
