"""The second source: Source Han Sans, for the symbols Sarasa draws half width.

`charset.CP936_SYMBOLS` names thirty codepoints that CP936 encodes in two bytes
and Sarasa Fixed SC draws at the 500 units it gives its latin - the accent, the
dashes, the arrows, the geometric shapes. Two bytes is this project's test for
a full cell (`is_full_width`), the thirty sit beside the ideographic comma in
the same CJK-symbol run, and Sarasa itself gives the comma 1000. One face
cannot hold both answers.

`charset.TABULAR_SYMBOLS` is the second batch, and it is the same defect: box
drawing, block elements and the geometric shapes beside them, 169 of which the
source also draws half width. It is not the same repair. A box-drawing
character has to meet its neighbours - the sibling project's `bitmap.CONNECTING`
exists for exactly this set - so those 160 keep the source's x position and
have their y mapped onto this project's line box, where the geometric shapes
are seated like any other symbol. `TILING` and the constants under it carry the
measurements.

And their strokes have to be re-weighed, which the first attempt at this
missed and a rendered frame caught. The source draws a rule 40 units thick
where this project's own face drew it at 70 and the reference face at 74; 40
units is 0.64 px at 16px, and a sub-pixel rule renders as two half-lit columns
rather than a line. The geometry was right and the frame still looked broken.
See `SOURCE_LIGHT_HALF` and what follows it.

Widening the advance alone would not do. The outlines are drawn to a half cell -
measured on the source, the ink of U+25BC is 0.510 em and of U+25E2 0.420 em,
against Han ink of about 0.94 - so a half-cell glyph in a full cell reads as a
small light character beside the Han. The design has to change with the metric.

Source Han Sans is where the design comes from, and it is the same hand.
Sarasa Gothic is Iosevka latin over Source Han Sans CJK, so the glyphs this
module transplants are drawn by the same typeface the rest of the product's CJK
already is.

Source Han Sans does not hand over the whole answer. Measured on the release
pinned below, twenty-six of the thirty carry its full 1000 advance already, but
four - the two spacing accents U+02CA and U+02CB, the emphasis dot U+02D9 and
the en dash U+2013 - it draws to latin proportions at 600, 600, 500 and 536,
and it seats them for a cell that narrow. So the advance is set to the full
cell here for all thirty rather than taken from the source, and the ink is
centred in it. Centring is close to a no-op for the other twenty-six: measured,
the largest shift it asks of them is 26 units, and the four quadrant blocks
U+25E2 to U+25E5 measure ink 0..1000 and do not move at all.

Source Han Serif would not do, though the sibling project uses it: this face
is a sans, and the sibling's serif shapes would sit wrong against the Han here.

The transplant crosses from CFF to TrueType, so the winding has to be brought
across too - and not by flipping the whole source, which is the obvious move:
measured, Source Han Sans's own outlines are not wound consistently. Each glyph
is measured and flipped on its own; `_wound_clockwise` has the figures.

The download is pinned to one release for the same reason `sarasa.py` pins its
source: the result is measured, and a different release should stop the build
rather than quietly produce something else. It is OFL 1.1, like Sarasa, so the
notice travels in `names.json` with Sarasa's.
"""
import math
import urllib.request
import zipfile
from pathlib import Path

from fontTools.pens.areaPen import AreaPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from build import charset

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"

# Pinned: 2.004R. Measured, not assumed - Sarasa 1.0.41's own Han agree with
# this release's to within a unit of rounding over a thirty glyph sample, so
# the transplant is drawn by the same vintage the rest of the CJK is. A later
# release would be a different hand.
ARCHIVE = "SourceHanSansSC-2.004R.zip"
SOURCE = "SourceHanSansSC-Regular.otf"
DOWNLOAD = ("https://github.com/adobe-fonts/source-han-sans/releases/download/"
            "2.004R/SourceHanSansSC.zip")

EXPECTED_UPEM = 1000
HAN_ADVANCE = 1000
# U+3001 is CJK punctuation Source Han Sans draws to the full cell; it stands in
# for the whole CJK here the way ASCII does in `sarasa.check_source`.
IDEOGRAPHIC_COMMA = 0x3001

# Box drawing and block elements do not merely sit in their cell, they *tile*
# it: a run of them has to come out as one unbroken rule, which is what the
# sibling project's `bitmap.CONNECTING` names and why WenQuanYi's bitmaps have
# their ink carried out to the cell edge rather than centred. The set is
# `charset.TILING_SYMBOLS`; what follows is why it is seated differently.
TILING = charset.TILING_SYMBOLS

# A tiling glyph cannot be seated the way the other thirty were. Measured
# on the thirty, 71 of these have ink that is not symmetric about the cell -
# U+250C runs from x 480 to 1000, U+258C from 0 to 500, U+2595 from 875 to 1000
# - so centring one moves it off the edge it exists to meet, which is exactly
# the seam the repair would have introduced. They keep the x position the
# source gave them.
#
# The vertical axis is a different question, and the answer is not "leave it
# alone" either. Measured, the source draws every one of its tiles inside its
# own em box, y -120 to 880; Sarasa draws the same shapes inside y -285 to 965,
# the 1.25 em line box that `refit` then shrinks to this project's 1.141 em, so
# that a vertical rule reaches into the line above and the line below it.
# Transplanting the source's box unchanged would leave every vertical rule 140
# units - 12% of the line - short of the next one, and every frame in every
# terminal would come apart at the row joins. So y is mapped from one box to
# the other and x is not, and the product comes out with the vertical geometry
# it ships today: measured over the whole charset, the same glyphs land on the
# same pixel row before and after.
SOURCE_CELL = (-120.0, 880.0)
TILING_CELL = (-285.0, 965.0)
# Derived: 1.25, and -135. Both in source units, so both scale with the upem.
TILING_SCALE_Y = (TILING_CELL[1] - TILING_CELL[0]) / (SOURCE_CELL[1] - SOURCE_CELL[0])
TILING_SHIFT_Y = TILING_CELL[0] - SOURCE_CELL[0] * TILING_SCALE_Y

# And the strokes are half the weight they need to be, which is the half of
# this that geometry alone does not show. Measured across every font in play,
# the light rule is 40 units in Source Han Sans and 70 in Sarasa, 74 in the
# reference face this project measures against, and 40 in the sibling project.
# 40 units is 0.04 em: 0.64 px at 16px and 0.88 px at 22px, which is *sub
# pixel*, and a sub pixel stroke does not render as a thin line - it renders as
# two half lit columns. Measured on the rendered product at 22px, the vertical
# rule came out 0 of 25 sampled rows solid and 25 of 25 light, minimum grey
# 140, against the horizontal rule's 66 of 66 solid at grey 0. A frame drawn
# with one solid rule and one ghost is a frame that looks broken, and no
# amount of edge-reaching measurement can see it.
#
# The source's own weights do not vary by weight class - all seven of its
# weights draw this block at 40 - so there is no heavier cut to take instead.
# The stroke is widened here, per axis, about the rule's own centre line: a
# glyph's coordinate set says which strokes it has, because the light stroke's
# edges sit at +/-20 of the crossing and the heavy one's at +/-60, and the
# double-line pair is the only case that has both.
SOURCE_CROSSING = (500.0, 380.0)
SOURCE_LIGHT_HALF = 20.0                # a 40 unit stroke
SOURCE_HEAVY_HALF = 60.0                # a 120 unit stroke
# The targets are what the product has to end up with, so the map is built in
# source units and the y axis is divided by the box scale above - the vertical
# axis is stretched on its way through, and a stroke widened before that
# stretch would come out 25% heavier than the one beside it.
TARGET_LIGHT_HALF = 35.0                # a 70 unit rule
TARGET_HEAVY_HALF = 70.0                # a 140 unit rule
TARGET_DOUBLE_HALF = 105.0              # two 70 unit bars, 70 apart
# The two diagonals and the cross are the three glyphs whose ink is a band
# running corner to corner: no axis of theirs carries a stroke, so the map
# leaves them at the weight the source drew, measured at 20 units across.
# Recorded rather than fixed, because widening a diagonal means offsetting it
# along its own normal and that is a different operation from this one.

# cu2qu tolerance, in source units. The outlines are converted at upem 1000 and
# scaled to 256 afterwards, so this lands at a quarter of a unit in the product:
# far inside the rounding that follows, and the thirty glyphs are not worth
# trading shape for bytes over.
# Half the cell, in source units: the map fixes this offset, so every arm
# still reaches the edge it has to reach.
HALF_CELL = 500.0

# Three shapes in this block carry no stroke on either axis, and the axis
# map cannot reach them. The three diagonals are bands running corner to
# corner - measured on the shipped product at a perpendicular width of 41.8
# units, against the 70 the rules now weigh, because the source draws them at
# its own 40 unit weight and no axis of theirs holds a stroke edge. The
# outlined shapes beside them - the white triangles and the white diamond -
# have a border rather than a stroke: measured at 36.3, 36.3 and 38.0 units,
# which is 0.58 to 0.61 px at 16px and renders as a wash for the same reason
# the rules did.
#
# Both are width fixes, but neither is this module's axis map, so they are done
# directly on the outline: a band is offset along its own normals, and a border
# is widened by shrinking its inner contour about its own centre, which leaves
# the outer silhouette exactly where the source drew it. The targets are in
# source units and are set a little above the 70 the rules end up at, because
# the box map stretches y by 1.25 and `refit` shrinks both axes unevenly on the
# way through; the figure that matters is the one measured on the product.
BAND_SYMBOLS = (0x2571, 0x2572, 0x2573)         # the diagonals and the cross
BORDER_SYMBOLS = (0x25B3, 0x25BD, 0x25C7)       # the outlined triangles and diamond
# The band is measured after the box map and `refit` have both had it, so the
# source figure is calibrated against the product rather than derived: 78 in
# source units came out at 54.4, so 100 is what lands on the 70 the rules
# weigh. The border is not stretched on its way through - those three shapes
# do not tile - so it takes the target directly.
TARGET_BAND_WIDTH = 120.0                       # perpendicular, source units
TARGET_BORDER = 70.0                            # the rule weight, directly
SOURCE_X = (0.0, 1000.0)                        # the cell the source draws into

# cu2qu tolerance, in source units. The outlines are converted at upem 1000 and
# scaled to 256 afterwards, so this lands at a quarter of a unit in the product:
# far inside the rounding that follows, and the thirty glyphs are not worth
# trading shape for bytes over.
MAX_ERR = 1.0


def source_path(root: Path = WORK) -> Path:
    return root / SOURCE


def archive_path(root: Path = WORK) -> Path:
    return root / ARCHIVE


def member_name(root: Path = WORK) -> str:
    """The Regular SC OTF's name inside the release archive."""
    with zipfile.ZipFile(archive_path(root)) as archive:
        found = [n for n in archive.namelist()
                 if Path(n).name == SOURCE and not n.endswith("/")]
    if not found:
        raise FileNotFoundError(f"{SOURCE} not in {ARCHIVE}")
    return sorted(found, key=len)[0]


def fetch(root: Path = WORK) -> Path:
    """Download and unpack the source font if it is not already there."""
    root.mkdir(parents=True, exist_ok=True)
    if not archive_path(root).exists():
        urllib.request.urlretrieve(DOWNLOAD, archive_path(root))
    out = source_path(root)
    if not out.exists():
        with zipfile.ZipFile(archive_path(root)) as archive:
            out.write_bytes(archive.read(member_name(root)))
    return out


def open_source(root: Path = WORK) -> TTFont:
    if not source_path(root).exists():
        fetch(root)
    font = TTFont(str(source_path(root)))
    check_source(font)
    return font


def check_source(font: TTFont) -> None:
    """Fail early if this is not the font the transplant was measured against."""
    upem = font["head"].unitsPerEm
    if upem != EXPECTED_UPEM:
        raise ValueError(f"expected upem {EXPECTED_UPEM}, found {upem}")
    cmap, hmtx = font.getBestCmap(), font["hmtx"]
    name = cmap.get(IDEOGRAPHIC_COMMA)
    if name is None:
        raise ValueError(f"source does not cover U+{IDEOGRAPHIC_COMMA:04X}")
    got = hmtx[name][0]
    if got != HAN_ADVANCE:
        raise ValueError(
            f"U+{IDEOGRAPHIC_COMMA:04X} has advance {got}, expected "
            f"{HAN_ADVANCE}; this does not look like Source Han Sans"
        )


def _wound_clockwise(glyph_set, name: str) -> bool:
    """Whether the glyph's outer contour runs clockwise.

    TrueType winds outers clockwise and CFF counter-clockwise, so the product
    wants clockwise and anything else has to be flipped on the way in. The
    flip cannot be decided once for the whole source, which is what the
    project's own pipeline would normally do: measured over the thirty, Source
    Han Sans's own CFF is not wound consistently - the two spacing accents and
    the emphasis dot arrive clockwise while the en dash, the arrow and the
    quadrant blocks arrive counter-clockwise. So each glyph is measured and
    flipped on its own.

    `AreaPen` gives the exact signed area, holes included. Their areas subtract,
    and a glyph's holes can never outweigh its outer, so the sign of the total
    is the sign of the outer.
    """
    pen = AreaPen(glyph_set)
    glyph_set[name].draw(pen)
    return pen.value < 0


class _PointMap:
    """A transform for `TransformPen`, which only wants `transformPoint`."""

    def __init__(self, mx, my):
        self.mx, self.my = mx, my

    def transformPoint(self, point):
        return (self.mx(point[0]), self.my(point[1]))


def _axis_points(glyph_set, name: str) -> tuple[set, set]:
    """The x and y coordinates the glyph is drawn on, in source units."""
    recorder = RecordingPen()
    glyph_set[name].draw(recorder)
    xs, ys = set(), set()
    for _, args in recorder.value:
        for point in args:
            if isinstance(point, tuple) and len(point) == 2:
                xs.add(round(point[0]))
                ys.add(round(point[1]))
    return xs, ys


def _stroke_kind(values, centre: float) -> str | None:
    """Which stroke this axis carries, read off its own coordinates.

    The source draws the light stroke with its edges at +/-20 of the crossing
    and the heavy one at +/-60, so an axis' coordinate set says which it is
    without anything here having to be told. `double` is the one case that has
    both: the pair's inner edges sit where a light stroke's outer edges do.
    An axis with neither - a block element's divisions, a shade's dither, a
    diagonal's band - carries no stroke and is left alone.
    """
    offsets = {round(abs(v - centre)) for v in values}
    if SOURCE_HEAVY_HALF in offsets and SOURCE_LIGHT_HALF in offsets:
        return "double"
    if SOURCE_HEAVY_HALF in offsets:
        return "heavy"
    if SOURCE_LIGHT_HALF in offsets:
        return "light"
    return None


def _axis_map(values, centre: float, scale: float):
    """A monotone map on one axis that widens the stroke about `centre`.

    Widening is done on the offsets, so the rule keeps its centre line and the
    cell edges stay exactly where they are: the map leaves an offset of half a
    cell alone, which is what keeps every arm reaching the edge it has to
    reach. `scale` is what the axis is about to be multiplied by downstream -
    1 for x, the box scale for y - and the targets are divided by it so that
    both axes come out at the same weight.
    """
    kind = _stroke_kind(values, centre)
    if kind is None:
        return lambda v: v
    if kind == "double":
        pairs = [(SOURCE_LIGHT_HALF, TARGET_LIGHT_HALF / scale),
                 (SOURCE_HEAVY_HALF, TARGET_DOUBLE_HALF / scale)]
    elif kind == "heavy":
        pairs = [(SOURCE_HEAVY_HALF, TARGET_HEAVY_HALF / scale)]
    else:
        pairs = [(SOURCE_LIGHT_HALF, TARGET_LIGHT_HALF / scale)]
    points = [(0.0, 0.0)] + pairs + [(HALF_CELL, HALF_CELL)]

    def mapped(value):
        offset = value - centre
        sign = -1.0 if offset < 0 else 1.0
        size = abs(offset)
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if size <= x1:
                return centre + sign * (y0 + (size - x0) / (x1 - x0) * (y1 - y0))
        return centre + sign * HALF_CELL
    return mapped


def _stroke_map(glyph_set, name: str) -> _PointMap:
    xs, ys = _axis_points(glyph_set, name)
    return _PointMap(_axis_map(xs, SOURCE_CROSSING[0], 1.0),
                     _axis_map(ys, SOURCE_CROSSING[1], TILING_SCALE_Y))


def _polygons(glyph_set, name: str) -> list[list[tuple[float, float]]]:
    """The glyph as closed polygons of on-curve points.

    Straight-edged artwork only, which is what the three diagonals and the
    three outlined shapes are: every segment in them is a line. A curve would
    have to be flattened first and this is not the place to do it.
    """
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
            raise ValueError(f"{name} has curves; it is not straight-edged")
    return out


def _area(points) -> float:
    n = len(points)
    return abs(sum(points[i][0] * points[(i + 1) % n][1]
                   - points[(i + 1) % n][0] * points[i][1]
                   for i in range(n)) / 2)


def _perimeter(points) -> float:
    n = len(points)
    return sum(math.dist(points[i], points[(i + 1) % n]) for i in range(n))


def _band_width(points) -> float:
    """Perpendicular width of a straight band: its area over its length."""
    n = len(points)
    longest = max(math.dist(points[i], points[(i + 1) % n]) for i in range(n))
    return _area(points) / longest if longest else 0.0


def _contour_is_clockwise(contour) -> bool:
    n = len(contour)
    return sum(contour[i][0] * contour[(i + 1) % n][1]
               - contour[(i + 1) % n][0] * contour[i][1]
               for i in range(n)) < 0


def _offset_contour(points, distance: float):
    """Move every vertex outward along its own mitred normal.

    The standard polygon offset: each edge moves out by `distance` along its
    normal, and a vertex lands where its two offset edges meet, which is
    `(n1 + n2) / (1 + n1.n2)` - `distance` along each normal, no more. A vertex
    whose edges double back on themselves has no such meeting point and is left
    where it is.

    This is what widens a band. It also moves the band's ends, which sit on the
    cell boundary; the caller clamps, so the ends stay on the edge and only the
    width changes.
    """
    if not points or distance == 0:
        return points
    clockwise = _contour_is_clockwise(points)
    n = len(points)
    edges = []
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length == 0:
            edges.append((0.0, 0.0))
            continue
        # Outward normal. A counter-clockwise contour keeps its interior on
        # the left of the direction of travel, so its outward normal is to the
        # right of it, and a clockwise one is the other way round.
        nx, ny = (-dy / length, dx / length) if clockwise else (dy / length, -dx / length)
        edges.append((nx, ny))
    out = []
    for i in range(n):
        n1, n2 = edges[i - 1], edges[i]
        dot = n1[0] * n2[0] + n1[1] * n2[1]
        if abs(1.0 + dot) < 1e-6:               # a spike; no mitre exists
            out.append(points[i])
            continue
        mx = (n1[0] + n2[0]) / (1.0 + dot)
        my = (n1[1] + n2[1]) / (1.0 + dot)
        out.append((points[i][0] + distance * mx, points[i][1] + distance * my))
    return out


def _thicken_border(polygons, target: float):
    """Widen the border of an outlined shape, leaving its silhouette alone.

    Both contours are similar and concentric, so the border is the difference
    of their inradii - `2 * area / perimeter` for each - and widening it means
    scaling the inner one down about its own centre by the ratio that makes up
    the difference. The outer contour is not touched, so the shape stays the
    size and in the place the source drew it.
    """
    if len(polygons) < 2 or target <= 0:
        return polygons
    outer = max(polygons, key=_area)
    inner = min(polygons, key=_area)
    r_outer = 2 * _area(outer) / _perimeter(outer)
    r_inner = 2 * _area(inner) / _perimeter(inner)
    if r_outer - r_inner >= target or r_inner <= 0:
        return polygons
    wanted = r_outer - target
    if wanted <= 0:
        return polygons
    scale = wanted / r_inner
    cx = sum(p[0] for p in inner) / len(inner)
    cy = sum(p[1] for p in inner) / len(inner)
    shrunk = [(cx + (x - cx) * scale, cy + (y - cy) * scale) for x, y in inner]
    return [outer, shrunk] if inner is polygons[1] else [shrunk, outer]


def _widen(glyph_set, name: str, codepoint: int):
    """The straightened geometry for a shape the axis map cannot reach."""
    polygons = _polygons(glyph_set, name)
    if codepoint in BAND_SYMBOLS:
        width = min(_band_width(p) for p in polygons)
        distance = max(0.0, (TARGET_BAND_WIDTH - width) / 2)
        return [_offset_contour(p, distance) for p in polygons]
    if codepoint in BORDER_SYMBOLS:
        return _thicken_border(polygons, TARGET_BORDER)
    return polygons


def _draw_polygons(polygons, transform: tuple):
    """A TT glyph from polygons, seated on this project's cell.

    Straight segments only, so no cubic-to-quadratic step is needed and the
    winding is whatever the source's was: the offset keeps it.
    """
    recorder = RecordingPen()
    for points in polygons:
        recorder.moveTo(points[0])
        for point in points[1:]:
            recorder.lineTo(point)
        recorder.closePath()
    pen = TTGlyphPen(None)
    recorder.replay(TransformPen(pen, transform))
    return pen.glyph()


def _draw(glyph_set, name: str, transform: tuple, stroke_map=None):
    """One source glyph, widened, converted to quadratic and transformed.

    The widening is applied first, in the units it was measured in, and the
    affine transform that seats the glyph on this project's cell second.
    """
    pen = TTGlyphPen(None)
    cu2qu = Cu2QuPen(pen, MAX_ERR,
                     reverse_direction=not _wound_clockwise(glyph_set, name))
    inner = TransformPen(cu2qu, transform)
    if stroke_map is None:
        glyph_set[name].draw(inner)
    else:
        glyph_set[name].draw(TransformPen(inner, stroke_map))
    return pen.glyph()


def _ink_span(glyph_set, name: str) -> tuple[float, float]:
    pen = BoundsPen(glyph_set)
    glyph_set[name].draw(pen)
    if pen.bounds is None:
        raise ValueError(f"{name} draws no ink")
    return pen.bounds[0], pen.bounds[2]


def adopt(font: TTFont, codepoints, root: Path = WORK) -> list[int]:
    """Redraw `codepoints` from Source Han Sans, full width and seated.

    Runs on the subsetted source, before the upem rescale, so the outline, the
    advance and the position are all set in the units the rest of `glyf` is
    measured in and every later step treats them like any other glyph.

    Two seatings, decided by `TILING`. An ordinary symbol is centred on its
    ink: the four among the thirty that Source Han Sans draws to latin
    proportions sit at the left edge of a 500 to 600 unit cell and would
    otherwise land a fifth of a cell off centre. A tiling character is not
    centred and has its vertical axis mapped instead - 71 of them are
    deliberately not symmetric in the cell, and all of them have to reach the
    cell edge to join the character beside them. The constants above carry the
    measurements behind both.
    """
    want = sorted(set(codepoints) & set(font.getBestCmap()))
    if not want:
        return []
    source = open_source(root)
    src_upem = source["head"].unitsPerEm
    src_cmap = source.getBestCmap()
    missing = [cp for cp in want if cp not in src_cmap]
    if missing:
        raise ValueError(
            f"source lacks {len(missing)} codepoints, first U+{missing[0]:04X}")

    upem = font["head"].unitsPerEm
    scale = upem / src_upem
    glyph_set = source.getGlyphSet()
    cmap, glyf, hmtx = font.getBestCmap(), font["glyf"], font["hmtx"]
    done = []
    for cp in want:
        name = cmap[cp]
        src_name = src_cmap[cp]
        scale_y, shift_y, shift_x = scale, 0.0, 0.0
        stroke_map = None
        polygons = None
        if cp in BAND_SYMBOLS or cp in BORDER_SYMBOLS:
            # Neither set carries a stroke on an axis, so both are widened on
            # the outline itself. The result is clamped back into the cell the
            # source drew in, which is what keeps a diagonal's ends on the cell
            # boundary after its sides have moved out.
            polygons = []
            for contour in _widen(glyph_set, src_name, cp):
                contour = [(min(max(x, SOURCE_X[0]), SOURCE_X[1]),
                            min(max(y, SOURCE_CELL[0]), SOURCE_CELL[1]))
                           for x, y in contour]
                # The offset keeps the source's winding; the product wants a
                # clockwise outer like every other glyph in it, so anything
                # that arrived counter-clockwise is turned round here.
                if not _contour_is_clockwise(contour):
                    contour = list(reversed(contour))
                polygons.append(contour)
        if cp in TILING:
            # Seated where the source put it, carried out to this project's
            # line box, and widened to this project's stroke weight; the
            # constants above have the measurements.
            scale_y = scale * TILING_SCALE_Y
            shift_y = scale * TILING_SHIFT_Y
            if polygons is None:
                stroke_map = _stroke_map(glyph_set, src_name)
        else:
            lo, hi = _ink_span(glyph_set, src_name)
            lo, hi = lo * scale, hi * scale
            if hi - lo >= upem:
                pass                    # already fills the cell; leave it
            else:
                shift_x = round((upem - (hi - lo)) / 2 - lo)
        transform = (scale, 0, 0, scale_y, shift_x, shift_y)
        if polygons is None:
            glyph = _draw(glyph_set, src_name, transform, stroke_map)
        else:
            glyph = _draw_polygons(polygons, transform)
        glyph.recalcBounds(glyf)
        glyf[name] = glyph
        hmtx[name] = (upem, int(glyph.xMin) if glyph.numberOfContours else 0)
        done.append(cp)
    return done
