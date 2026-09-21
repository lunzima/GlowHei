"""The pipeline, and the collection that carries two faces of it.

The two faces differ in `post.isFixedPitch` and `panose.bProportion` and in
nothing else. Outlines, metrics and hinting are the same bytes, so the
collection shares those tables and stores them once.
"""
import io
from pathlib import Path

from fontTools.ttLib import TTCollection, TTFont
from fontTools.ttLib.scaleUpem import scale_upem

from build import decompose, ffsimplify, hint, metrics, refit, sarasa

UPEM = 256


def build_outlines(codepoints, *, use_fontforge: bool = True,
                   workdir: Path | None = None) -> TTFont:
    """Steps 1 to 5: from the source font to finished outlines at upem 256."""
    font = sarasa.subset(sarasa.open_source(), codepoints)
    decompose.decompose(font)
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
