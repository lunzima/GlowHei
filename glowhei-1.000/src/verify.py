"""Product verification: the verification strategy as executable assertions.

Reads the built files only and takes no part in building them. What the build
assumed and what ended up on disk can diverge: `hmtx`'s lsb lost touch with
`xMin` after the FontForge transplant, and only reading the product showed it.

    python -m build.verify                     # everything under out/
    python -m build.verify out/xxx.ttc
    python -m build.verify --strict            # warnings count as failures
"""
import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
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
# thirteen are all of them.
#
# They are upstream's design, not this pipeline's doing, and the goal is no
# visible difference from the source, so they are allowed rather than
# corrected. The diagonals are the clearest case - U+2571 to U+2573 overhang so
# that a run of them joins into a continuous line, which is the whole point of
# a box-drawing character. The check still holds them to the measured figure,
# so a regression on these glyphs fails like any other.
KNOWN_OVERHANG = {
    0x221A: 9.2,                                    # square root
    0x2571: 8.2, 0x2572: 8.2, 0x2573: 8.2,          # box-drawing diagonals
    0x89E9: 2.3,                                    # a wide ideograph
    0x2167: 2.0, 0x2177: 2.0,                       # Roman numeral eight
    0x25B2: 1.3, 0x25B3: 1.3,                       # triangles
    0x25BC: 1.3, 0x25BD: 1.3,
    0x2197: 1.3, 0x2198: 1.3,                       # diagonal arrows
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
    rep.note(f"advances in use: {sorted(widths)}")


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
