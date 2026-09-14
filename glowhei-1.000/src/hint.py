"""TrueType hinting, through Chlorophytum.

**Why Chlorophytum.** It is what the source font's own hinted release uses, and
measured against the shape the outline describes - the same glyph rendered at
eight times the size and box-reduced - it is the most faithful of the options
tried, at every size, and more faithful than leaving the font unhinted.
ttfautohint is the least faithful and wrecks two to four times as many glyphs:
counters fill in, stacked strokes swallow the white between them, strokes
shift.

Blur is the price and it is worth paying. A blurred glyph with the right
structure can be read; a crisp glyph with the wrong structure misleads.

**Two tools, split by codepoint.** Chlorophytum's passes cover ideographs,
hangul and kana; the letters go to ttfautohint. Neither tool can do both.
Putting Latin through Chlorophytum's ideograph analyser takes a fifth of `g`'s
ink away, because that analyser has an ideograph's em box and no blue zones for
a baseline, an x-height or a descender. Putting ideographs through ttfautohint
is worse still.

**The split is upstream's, and so is the reason it works.** The two cannot
share a font by stacking: run one after the other and the ideographs come out
unrecognisable. What works is hinting each side in its own font and merging -
and the merge needs no renumbering, because a single `instruct` invocation
given several fonts lays out one function and CVT numbering across all of its
outputs. Measured, ideographs hinted that way render identically to a run over
them alone, and the letters identically to plain ttfautohint.

This was nearly abandoned on a false result: an early test appeared to show the
joint invocation breaking the ideographs too. It had a stale configuration
installed beside the toolchain, and was comparing outputs of two different
configurations.

**The upstream configuration is used unmodified**, byte for byte, and the
sibling project uses the same file. Tuning `EmBox` to a font's own design
measures better against a contrast metric, and that metric turned out to be the
wrong one.

Needs Node and a set of npm packages inside WSL.
Without them the step is skipped and the font ships unhinted, which is correct
but softer at small sizes.
"""
import io
import subprocess
import tempfile
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

from build import ffsimplify

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "build" / "hcfg.json"
WORK = ROOT / "work"

# Where the toolchain lives inside WSL. The configuration has to sit beside its
# node_modules: plugin names in it resolve relative to the configuration's own
# directory, and one kept in this repository gets MODULE_NOT_FOUND.
PROJECT = "$HOME/chlorophytum"
CLI = "node_modules/@chlorophytum/cli/bin/_startup"
JOBS = 8
# The analysis pass is the slow one: an hour and fifty minutes for the GBK
# charset on eight threads, measured. An early estimate of half an hour came
# from extrapolating a sample taken from the start of the Unified Ideographs
# block, where the characters are unusually simple; cost per glyph rises
# steeply with stroke count.
#
# Two and a half hours was not enough for the sibling project's largest target,
# which ran past it. Six leaves room for the largest charset on a slower
# machine, and the cache makes a rebuild far cheaper than the first run.
TIMEOUT = 21600

# Names the working directory when the caller does not supply one, so a
# stray one is traceable to the project that left it.
TEMP_PREFIX = "glowhei_hint_"


def wsl_path(path: Path) -> str:
    """Translate a Windows path to its WSL mount point."""
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    rest = resolved.as_posix()[len(resolved.drive):]
    return f"/mnt/{drive}{rest}"


def _wsl(script: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["wsl", "-e", "bash", "-lc", script],
                          capture_output=True, timeout=timeout)


def available() -> bool:
    """Whether Chlorophytum is usable inside WSL."""
    try:
        return _wsl(f"test -f {PROJECT}/{CLI}").returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _place_config() -> None:
    """Copy this repository's configuration next to the toolchain. See PROJECT."""
    result = _wsl(f"cp {wsl_path(CONFIG)} {PROJECT}/hcfg.json")
    if result.returncode != 0:
        raise RuntimeError(f"could not place hcfg.json: {result.stderr[-200:]!r}")


# The alphabets ttfautohint takes, by codepoint. Everything else - ideographs,
# kana, punctuation, box drawing, full-width forms - goes to Chlorophytum's
# side, where its passes hint what they cover and the rest stays plain.
#
# Split by codepoint rather than by which glyphs Chlorophytum would hint: that
# would mean predicting the configuration's ranges here and drifting from them
# silently. This way the division states what each tool is for.
LETTER_RANGES = (
    (0x0020, 0x007E),   # Basic Latin, digits and ASCII punctuation with it
    (0x00A0, 0x024F),   # Latin-1 Supplement, Extended-A, Extended-B
    (0x0250, 0x02AF),   # IPA Extensions
    (0x0370, 0x03FF),   # Greek and Coptic
    (0x0400, 0x04FF),   # Cyrillic
)


def _is_letter(codepoint: int) -> bool:
    return any(lo <= codepoint <= hi for lo, hi in LETTER_RANGES)


def ttfautohint_available() -> bool:
    """Whether ttfautohint is usable inside WSL."""
    try:
        return _wsl("command -v ttfautohint").returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _cut(font: TTFont, codepoints, path: Path) -> None:
    """Write a subset of `font` covering `codepoints`, outlines untouched.

    Through a copy, because subsetting is destructive and the caller's font is
    the one the instructions come back to.
    """
    buffer = io.BytesIO()
    font.save(buffer)
    buffer.seek(0)
    piece = TTFont(buffer)
    options = subset.Options()
    options.hinting = False
    options.notdef_outline = True
    options.layout_features = []
    options.glyph_names = False
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=sorted(codepoints))
    subsetter.subset(piece)
    piece.save(str(path))


def _programs(source: TTFont) -> dict:
    """Every glyph program in `source`, keyed by codepoint."""
    glyf = source["glyf"]
    found = {}
    for codepoint, name in source.getBestCmap().items():
        glyph = glyf[name]
        glyph.expand(glyf)
        program = getattr(glyph, "program", None)
        if program is not None and program.getBytecode():
            found[codepoint] = program
    return found


def _merge(font: TTFont, carrier: TTFont, sources: list) -> TTFont:
    """Put the hinted pieces back on a copy of `font`.

    `carrier` supplies `fpgm`, `prep` and `cvt `; the glyph programs come from
    `sources`, matched by codepoint. This is a straight transplant with no
    renumbering, and it is sound only because one `instruct` produced every
    piece: given several fonts at once, Chlorophytum lays out one function and
    CVT numbering and writes that same one into all of them. Measured, the
    ideographs come back rendering identically to a run over them alone.
    """
    buffer = io.BytesIO()
    font.save(buffer)
    buffer.seek(0)
    merged = TTFont(buffer)

    for tag in ("fpgm", "prep", "cvt "):
        if tag in carrier:
            merged[tag] = carrier[tag]

    glyf, cmap = merged["glyf"], merged.getBestCmap()
    for source in sources:
        for codepoint, program in _programs(source).items():
            name = cmap.get(codepoint)
            if name is None:
                continue
            glyph = glyf[name]
            glyph.expand(glyf)
            glyph.program = program

    limits = merged["maxp"]
    for field in ("maxZones", "maxTwilightPoints", "maxStorage",
                  "maxFunctionDefs", "maxInstructionDefs", "maxStackElements",
                  "maxSizeOfInstructions"):
        highest = max(getattr(source["maxp"], field) for source in sources)
        setattr(limits, field, max(getattr(limits, field), highest))
    return merged


def _run(script: str, what: str) -> None:
    result = _wsl(script, timeout=TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(f"{what} failed (rc={result.returncode}): "
                           f"{result.stderr[-400:]!r}")


def apply(font: TTFont, workdir: Path | None = None,
          cache: Path | None = None, jobs: int = JOBS) -> TTFont:
    """Return a hinted copy of `font`. The input is left alone.

    The font is split by codepoint, hinted by two tools and put back together.
    `LETTER_RANGES` states the division; the module docstring says why there
    has to be one.

    **The two `instruct` runs must be one invocation.** That is what makes the
    pieces mergeable: it lays out a single function and CVT numbering across
    every output, so an ideograph's program reads the same CVT entries in the
    merged font that it read on its own. Run separately, each piece numbers
    from zero and the merge is nonsense.

    Without ttfautohint the split is skipped and the whole font goes to
    Chlorophytum, which is what the font shipped as before: correct, with the
    letters carrying no instructions.
    """
    work = Path(workdir or tempfile.mkdtemp(prefix=TEMP_PREFIX))
    work.mkdir(parents=True, exist_ok=True)
    # Keyed by glyph, so the three products share one cache: whatever the GBK
    # build analysed, the GB2312 build gets for free.
    cache = Path(cache) if cache else WORK / "chlorophytum-cache.gz"
    cache.parent.mkdir(parents=True, exist_ok=True)
    _place_config()

    covered = set(font.getBestCmap())
    letters = {cp for cp in covered if _is_letter(cp)}
    rest = covered - letters
    if not letters or not rest or not ttfautohint_available():
        letters, rest = set(), covered

    pieces = []
    if letters:
        plain = work / "hint_letters_plain.ttf"
        _cut(font, letters, plain)
        tuned = work / "hint_letters.ttf"
        _run(f"ttfautohint {wsl_path(plain)} {wsl_path(tuned)}", "ttfautohint")
        pieces.append((tuned, work / "hint_letters.gz",
                       work / "hint_letters_out.ttf"))

    body = work / "hint_body.ttf"
    _cut(font, rest, body)
    pieces.append((body, work / "hint_body.gz", work / "hint_body_out.ttf"))

    pairs = " ".join(f"{wsl_path(i)} {wsl_path(h)}" for i, h, _ in pieces)
    triples = " ".join(f"{wsl_path(i)} {wsl_path(h)} {wsl_path(o)}"
                       for i, h, o in pieces)
    _run(
        f"cd {PROJECT} && "
        f"node {CLI} hint -c hcfg.json -h {wsl_path(cache)} -j {jobs} {pairs} && "
        f"node {CLI} instruct -c hcfg.json {triples}",
        "Chlorophytum",
    )
    missing = [str(o) for _, _, o in pieces if not o.exists()]
    if missing:
        raise RuntimeError(f"Chlorophytum wrote no output for {missing}")

    outputs = [TTFont(str(o)) for _, _, o in pieces]
    # The letters' piece carries both tools' tables, so it is the one to take
    # `fpgm`, `prep` and `cvt ` from; without it there is only the one piece.
    hinted = _merge(font, outputs[0], outputs)
    # ot-builder sets OVERLAP_SIMPLE on nearly every glyph it writes, and
    # ots-sanitize rejects the whole `glyf` table over it. The same cleanup
    # runs after the simplify pass, for whatever FontForge leaves behind.
    ffsimplify.clear_overlap_flags(hinted)
    return hinted


def is_hinted(font: TTFont) -> bool:
    """Whether a font carries hinting, which decides its gasp ladder."""
    return "fpgm" in font
