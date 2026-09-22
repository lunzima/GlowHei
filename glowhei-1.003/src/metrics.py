"""Metrics and naming: fill in `name`, `OS/2`, `head`, `post` and `gasp`.

FontBuilder's defaults are not enough for Windows to load the font. It writes
only name IDs 1 and 2, while Windows needs 3 (unique ID), 4 (full name) and 6
(PostScript name) as well. This module fills in every required field and
applies the metrics of the design.
"""
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont, newTable

# Localised family names live in a data file so the source itself stays ASCII.
NAMES_PATH = Path(__file__).resolve().parent / "names.json"


@lru_cache(maxsize=1)
def _names() -> dict:
    return json.loads(NAMES_PATH.read_text(encoding="utf-8"))


VERSION = _names()["version"]
VENDOR_ID = _names()["vendor_id"]

# Design metrics. The vertical figures are the reference sans's; the
# line box is this project's own, 232 + 60 + 0 = 292 units = 1.141 em, which is
# the line height GlowSong settles on as well so the two can be mixed.
ASCENT, DESCENT, LINE_GAP = 220, -36, 0
WIN_ASCENT, WIN_DESCENT = 232, 60

# OS/2.xAvgCharWidth, which GDI hands applications as tmAveCharWidth. The half
# width, not the mean over the glyph set: applications size a character cell
# from it, and this font is 99% full-width CJK, so the mean would be 255 and
# every cell computed from it would come out twice too wide. The reference
# declares 128 for the same reason.
AVG_CHAR_WIDTH = 128

# gasp: grid-fitting and greyscale together, at every size.
#
# The sibling project turns antialiasing off between 9 and 16px to keep its
# embedded bitmaps sharp. This font has no bitmaps, so the same setting would
# render outlines in pure black and white exactly where they are weakest.
# Grid-fitting is on throughout because the hinting only takes effect where
# it is.
GASP_RANGES = {0xFFFF: 0x03}

# Below this the outlines have nothing left to say.
LOWEST_REC_PPEM = 8

FSSELECTION_REGULAR = 0x40
CODEPAGE_LATIN1 = 1 << 0        # CP1252
CODEPAGE_GBK = 1 << 18          # CP936


@dataclass(frozen=True)
class FontSpec:
    """One face's identity and characteristics."""

    family_en: str
    ps_name: str
    monospace: bool = False
    gbk_codepage: bool = True
    style: str = "Regular"

    @property
    def full_en(self) -> str:
        return f"{self.family_en} {self.style}" if self.style != "Regular" else self.family_en

    @property
    def unique_id(self) -> str:
        return f"{VERSION};{VENDOR_ID};{self.ps_name}"

    @property
    def localised(self) -> dict[str, str]:
        """{language tag: family name} from names.json, keyed by PostScript name."""
        return _names()["faces"].get(self.ps_name, {})

    @property
    def notices(self) -> dict[str, str]:
        """Copyright, licence and licence URL, for name IDs 0, 13 and 14."""
        return _names()["legal"]


# The five faces. The full build takes the plain name and the slim
# one carries a GB suffix, so both can be installed side by side.
FULL_PROP = FontSpec("GlowHei", "GlowHei-Regular")
FULL_MONO = FontSpec("GlowHei Mono", "GlowHeiMono-Regular", monospace=True)

# The slim build holds only GB2312's 6763 characters, so the CP936 codepage bit
# does overstate it. It claims the codepage anyway: GDI skips any font that does
# not when an application asks for GB2312_CHARSET, and Chinese Windows
# applications ask for it constantly. Being unselectable costs more than tofu on
# the GBK extensions.
SLIM_PROP = FontSpec("GlowHei GB", "GlowHeiGB-Regular")
SLIM_MONO = FontSpec("GlowHei GB Mono", "GlowHeiGBMono-Regular", monospace=True)

# Ext A is outside CP936 entirely, and is reached as a fallback rather than
# chosen by charset, so the claim would be both false and useless.
EXT_A = FontSpec("GlowHei ExtA", "GlowHeiExtA-Regular", gbk_codepage=False)

MAX_FACE_NAME = 31              # LOGFONT.lfFaceName limit


def _set_name(font: TTFont, name_id: int, value: str, *, mac: bool = True) -> None:
    """Write a name record. Windows always; Mac only for ASCII values."""
    table = font["name"]
    table.setName(value, name_id, 3, 1, 0x0409)
    if mac and value.isascii():
        table.setName(value, name_id, 1, 0, 0)


def apply_names(font: TTFont, spec: FontSpec) -> None:
    """Write the full set of name records, including the Chinese family name."""
    if len(spec.family_en) > MAX_FACE_NAME:
        raise ValueError(
            f"family name {spec.family_en!r} exceeds {MAX_FACE_NAME} chars; "
            "Windows truncates it in LOGFONT.lfFaceName"
        )
    font["name"].names = []
    notices = spec.notices
    # Both upstream licences require their notice to travel with the font, so
    # IDs 0, 13 and 14 are an obligation rather than decoration.
    _set_name(font, 0, notices["copyright"], mac=False)
    _set_name(font, 1, spec.family_en)
    _set_name(font, 2, spec.style)
    _set_name(font, 3, spec.unique_id)
    _set_name(font, 4, spec.full_en)
    _set_name(font, 5, f"Version {VERSION}")
    _set_name(font, 6, spec.ps_name)
    _set_name(font, 11, _names()["project_url"])
    _set_name(font, 13, notices["licence"], mac=False)
    _set_name(font, 14, notices["licence_url"])
    # Localised family names, on platform 3 with the language tag from the data
    # file. Style is written alongside so the pair is complete in that language.
    for tag, family in spec.localised.items():
        language = int(tag, 16)
        font["name"].setName(family, 1, 3, 1, language)
        font["name"].setName(spec.style, 2, 3, 1, language)
        font["name"].setName(family, 4, 3, 1, language)


def _measure(font: TTFont, char: str, default: int) -> int:
    """Height of one character's glyph, or the default if it is absent."""
    name = font.getBestCmap().get(ord(char))
    if name is None:
        return default
    g = font["glyf"][name]
    return int(g.yMax) if getattr(g, "numberOfContours", 0) > 0 else default


# The line box, 1.141 em at upem 256.
TARGET_LINE_HEIGHT = WIN_ASCENT + WIN_DESCENT + LINE_GAP


def ink_extent(font: TTFont) -> tuple[int, int]:
    """(yMax, -yMin) over all ink, the minimum win ascent/descent needed.

    Measured from the drawn curve, not from `glyph.yMax` and `glyph.yMin`.
    Those are the control-point box, and a quadratic's control points sit
    outside the curve they describe - measured on this font, eight units
    outside at the worst glyph. Nothing renders there, so nothing clips there,
    and `refit` decides what fits by the same measure. Using the two different
    measures in the two places is what made the first full build fail.
    """
    glyph_set = font.getGlyphSet()
    ymax, ymin = 0, 0
    for name in font.getGlyphOrder():
        pen = BoundsPen(glyph_set)
        glyph_set[name].draw(pen)
        if pen.bounds is None:
            continue
        ymax = max(ymax, int(pen.bounds[3]))
        ymin = min(ymin, int(pen.bounds[1]))
    return ymax, -ymin


def win_metrics(font: TTFont) -> tuple[int, int, int]:
    """Return (usWinAscent, usWinDescent, lineGap), checking the ink fits.

    **The win metrics must cover every bit of ink**, because Windows clips to
    them. Here they are fixed rather than computed: the design settles the
    line box and `refit` is what makes the ink fit it, so this is the place
    that catches a glyph `refit` missed. Growing the box instead would
    silently change the line height the whole project is built around.
    """
    need_ascent, need_descent = ink_extent(font)
    if need_ascent > WIN_ASCENT or need_descent > WIN_DESCENT:
        raise ValueError(
            f"ink reaches {need_ascent}/-{need_descent}, outside the "
            f"{WIN_ASCENT}/-{WIN_DESCENT} line box; run refit first"
        )
    return WIN_ASCENT, WIN_DESCENT, LINE_GAP


def apply_os2(font: TTFont, spec: FontSpec) -> None:
    """OS/2: the design metrics, and a version old applications accept."""
    o2 = font["OS/2"]
    o2.version = 3                       # v4 bit 7 is misread by older suites
    o2.usWeightClass = 400
    o2.usWidthClass = 5
    o2.xAvgCharWidth = AVG_CHAR_WIDTH
    o2.fsType = 0                        # installable embedding, unrestricted
    o2.fsSelection = FSSELECTION_REGULAR
    o2.sFamilyClass = 0
    o2.achVendID = VENDOR_ID
    o2.sTypoAscender = ASCENT
    o2.sTypoDescender = DESCENT
    o2.sTypoLineGap = LINE_GAP
    # These must cover all ink or Windows clips the descenders.
    asc, desc, _ = win_metrics(font)
    o2.usWinAscent = asc
    o2.usWinDescent = desc
    o2.sxHeight = _measure(font, "x", 112)
    o2.sCapHeight = _measure(font, "H", 160)
    o2.usDefaultChar = 0
    o2.usBreakChar = 32
    o2.usMaxContext = 0
    o2.ulCodePageRange1 = CODEPAGE_LATIN1 | (CODEPAGE_GBK if spec.gbk_codepage else 0)
    o2.ulCodePageRange2 = 0
    o2.recalcUnicodeRanges(font, pruneOnly=False)
    cps = sorted(font.getBestCmap())
    o2.usFirstCharIndex = min(cps)
    o2.usLastCharIndex = min(max(cps), 0xFFFF)

    panose = o2.panose
    panose.bFamilyType = 2               # latin text
    panose.bSerifStyle = 11              # normal sans
    panose.bWeight = 5
    # 0 (any) for the proportional face, 9 (monospaced) for the mono one. This
    # and post.isFixedPitch are the only two fields the two faces differ in.
    panose.bProportion = 9 if spec.monospace else 0
    for attr in ("bContrast", "bStrokeVariation", "bArmStyle",
                 "bLetterform", "bMidline", "bXHeight"):
        setattr(panose, attr, 0)


def apply_hhea(font: TTFont) -> None:
    """hhea follows the Windows convention and tracks the win metrics.

    Its lineGap pads the line height back towards the original 1.14 em.
    """
    asc, desc, gap = win_metrics(font)
    hhea = font["hhea"]
    hhea.ascender = asc
    hhea.descender = -desc
    hhea.lineGap = gap


def apply_head(font: TTFont, spec: FontSpec) -> None:
    head = font["head"]
    head.macStyle = 0                    # regular; must agree with fsSelection
    head.lowestRecPPEM = LOWEST_REC_PPEM
    head.fontRevision = float(VERSION)
    _ = spec


def apply_post(font: TTFont, spec: FontSpec) -> None:
    post = font["post"]
    post.formatType = 3.0                # glyph names are not worth the bytes
    post.isFixedPitch = 1 if spec.monospace else 0
    post.italicAngle = 0
    post.underlinePosition = -20
    post.underlineThickness = 12


def apply_gasp(font: TTFont) -> None:
    """Write the rendering ladder. One ladder; see GASP_RANGES."""
    gasp = newTable("gasp")
    gasp.version = 1
    gasp.gaspRange = dict(GASP_RANGES)
    font["gasp"] = gasp


def apply(font: TTFont, spec: FontSpec) -> None:
    """Fill in every metric and naming table in one go."""
    apply_names(font, spec)
    apply_hhea(font)
    apply_os2(font, spec)
    apply_head(font, spec)
    apply_post(font, spec)
    apply_gasp(font)
