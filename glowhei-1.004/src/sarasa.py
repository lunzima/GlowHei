"""Locating the source font, checking it, and cutting it to a charset.

The source is Sarasa Fixed SC Regular, taken from the Unhinted archive.
Hinting is dropped on subsetting regardless of which archive the file came
from: TrueType instructions are bound to point indices, and both the upem
rescale and the simplify pass move points. Instructions that survived would
address points that had moved, which is worse than no instructions at all.
Hinting is put back at the end of the pipeline instead.
"""
from pathlib import Path

from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
SOURCE = "SarasaFixedSC-Regular.ttf"
DOWNLOAD = "https://github.com/be5invis/Sarasa-Gothic/releases"

EXPECTED_UPEM = 1000
ASCII_ADVANCE = 500
HAN_ADVANCE = 1000

# Dropped outright. The project ships no layout features, and kerning in a
# fixed-pitch font can only pull glyphs off the grid it exists to hold.
DROP_TABLES = ["GSUB", "GPOS", "GDEF", "DSIG", "vhea", "vmtx", "VORG"]


def source_path(root: Path = WORK) -> Path:
    return root / SOURCE


def open_source(root: Path = WORK) -> TTFont:
    path = source_path(root)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Download SarasaFixed-TTF-Unhinted from "
            f"{DOWNLOAD} and put {SOURCE} there."
        )
    font = TTFont(str(path))
    check_source(font)
    return font


def check_source(font: TTFont) -> None:
    """Fail early if this is not the font the pipeline was measured against.

    Every downstream constant - the Han ink span, the half-width Latin, the
    count of overshooting glyphs - was measured on one specific release. A
    different one should stop the build rather than quietly produce something
    else.
    """
    upem = font["head"].unitsPerEm
    if upem != EXPECTED_UPEM:
        raise ValueError(f"expected upem {EXPECTED_UPEM}, found {upem}")
    if "glyf" not in font:
        raise ValueError("expected a TrueType outline font, found no glyf")

    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    for codepoint, want in ((0x41, ASCII_ADVANCE), (0x4E00, HAN_ADVANCE)):
        name = cmap.get(codepoint)
        if name is None:
            raise ValueError(f"source does not cover U+{codepoint:04X}")
        got = hmtx[name][0]
        if got != want:
            raise ValueError(
                f"U+{codepoint:04X} has advance {got}, expected {want}; "
                "this does not look like Sarasa Fixed"
            )


def subset(font: TTFont, codepoints) -> TTFont:
    """Cut `font` down to `codepoints`, in place. Returns the same object."""
    options = Options()
    options.hinting = False             # see the module docstring
    options.glyph_names = False         # post format 3, as the products ship
    options.notdef_outline = True
    options.recalc_bounds = True
    options.layout_features = []
    options.drop_tables += DROP_TABLES
    subsetter = Subsetter(options=options)
    subsetter.populate(unicodes=sorted(codepoints))
    subsetter.subset(font)
    return font
