"""Refuse to release a tree that carries the machine it was built on.

This project distributes its sources as they stand, with no staging step to
rewrite anything on the way out. So the scan runs against the working
tree itself and a hit is a failure, not a warning: the point is that nobody has
to remember to look.

The list holds paths, tool names and identifiers, never ordinary English words.
`spec` and `plan` were tried and dropped - they fire on `specify` and `replan`
and turn the scan into noise nobody reads.
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path

# Anything whose presence means the release is carrying local state.
#
# The bare word `wsl` is deliberately not here, though the sibling project bans
# it. WSL is this project's documented build requirement - spec 4.7 prints the
# install commands - and a release that may not say so is a release whose
# pipeline cannot be rebuilt from it. What must not travel is the machine:
# absolute paths, a user directory, a mount point.
PATTERNS: tuple[str, ...] = (
    "C:\\Windows",
    "C:\\Users",
    "D:\\Fonts",
    "D:\\GlowHei",
    "AppData",
    "docs/superpowers",
    "2026-09-13-glowhei",
    "主文档",
)

# The font the metrics were measured against, and an office suite whose quirks
# shaped some of the decisions. Either name belongs in a comment about where a
# number came from, which a release has no use for - but the font name is also
# a family name, and a fontconfig alias has to say it out loud to do its job.
# Hence the split: checked everywhere except in `ALIAS_FILES`.
REFERENCE_FONT: tuple[str, ...] = (
    "simhei",
    "simsun",
    "中易",
    "word 2000",
)

# The files that hold the lists, exempt from everything. A checker cannot
# check itself without naming what it looks for, and neither can the test that
# asserts the checker is wired up.
SELF = re.compile(r"(^|/)(sanitise\.py|test_charset\.py)$")

# Files whose business is naming other font families. The alias rules exist to
# point the old names at this font, so stripping the names would delete the
# feature rather than clean it up.
ALIAS_FILES = re.compile(
    r"(^|/)(65-glowhei\.conf|check-fontconfig\.sh|test_fontconfig\.py"
    r"|README-dist\.md|README\.md)$")

# Git's object database, skipped. Its contents are zlib streams, so the
# printable runs pulled out of them are noise rather than signal - and nothing
# is lost by not looking: every file that went in was scanned on the way.
SKIP_DIRS = frozenset({".git", "work", "out", "__pycache__"})

# Suffixes read as text in full. Everything else is searched for printable runs,
# which is how a compiled binary would give away a build path.
TEXT_SUFFIXES = frozenset({
    ".py", ".c", ".h", ".md", ".txt", ".json", ".sh", ".conf", ".bdf",
    ".cfg", ".ini", ".toml", ".mk", "",
})

# The project's own contact address. Deliberate attribution, not a leak, so it
# is the one address a release may carry.
CONTACT = "lunzima@lunzima.net"

# Cross-references to this project's own design notes. Matched by shape rather
# than by word: the bare word "spec" fires on "specify" and is useless, but
# "spec 9.1" is unmistakably a pointer at a document the reader will not have.
PROSE_REFERENCES = (
    re.compile(r"spec\s+§?\d+(\.\d+)*", re.I),
    re.compile(r"主文档\s*§"),
    # Any address that is not the project's own. A release has one reason to
    # carry an address - saying who maintains it - and every other match is
    # either the builder's machine or a leftover from somewhere local.
    # The lookbehind matters: without it the match simply restarts one
    # character in and reports `unzima@...` as a different address.
    re.compile(rf"(?<![\w.+-])(?!{re.escape(CONTACT)})[\w.+-]+@[\w-]+\.[\w.-]+"),
)

# Traces of the machine that did the build, which are looked for everywhere -
# in prose, and in the printable runs of a compiled artefact. Unlike a document
# reference or an address, a mount point cannot arise by coincidence out of
# glyph data, so there is no reason to hold these back from binaries, and a
# build path baked into one is exactly what this scan exists to catch.
MACHINE_TRACES = (
    # A mounted Windows drive as it appears inside WSL. Matched with the drive
    # letter attached so that the format string the path translator is built
    # from - "/mnt/" followed by a variable - is not mistaken for a path
    # someone's machine leaked.
    re.compile(r"/mnt/[a-z]/"),
)

CROSS_REFERENCES = PROSE_REFERENCES + MACHINE_TRACES

# Upstream licence texts are exempt. They carry the author's address as part of
# the notice that the licence requires be kept intact, so flagging it would
# argue for deleting the very attribution the release exists to preserve.
LICENCE_FILES = re.compile(r"(^|/)(LICENSE|LICENCE|COPYING)", re.I)

_PRINTABLE = re.compile(rb"[\x20-\x7e]{4,}")


@dataclass(frozen=True)
class Hit:
    path: Path
    line: int          # 0 means the file or directory name itself
    pattern: str
    excerpt: str


def _identity() -> list[str]:
    """Names that identify whoever built this, taken from the environment.

    Read at run time rather than written down: hard-coding them would put the
    very strings being hunted into the hunter.
    """
    out = []
    for key in ("USERNAME", "USER", "LOGNAME"):
        value = os.environ.get(key)
        if value and len(value) >= 3:
            out.append(value)
    return out


def _search(text: str, patterns: list[str],
            expressions=CROSS_REFERENCES) -> list[tuple[int, str, str]]:
    lowered = text.lower()
    found = []
    for expression in expressions:
        for match in expression.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            begin = max(0, match.start() - 20)
            found.append((line, match.group(), text[begin:match.end() + 20]))
    for pattern in patterns:
        needle = pattern.lower()
        start = 0
        while True:
            at = lowered.find(needle, start)
            if at < 0:
                break
            line = text.count("\n", 0, at) + 1
            begin = max(0, at - 20)
            found.append((line, pattern, text[begin:at + len(pattern) + 20]))
            start = at + len(needle)
    return found


def scan(root: Path, extra=()) -> list[Hit]:
    """Every occurrence of a forbidden pattern under `root`."""
    always = list(PATTERNS) + _identity() + list(extra)
    patterns = always + list(REFERENCE_FONT)
    root = Path(root)
    hits: list[Hit] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if SKIP_DIRS.intersection(path.relative_to(root).parts):
            continue
        if SELF.search(relative):
            continue
        for _, pattern, excerpt in _search(relative, patterns):
            hits.append(Hit(path, 0, pattern, excerpt))
        if not path.is_file():
            continue
        raw = path.read_bytes()
        is_text = path.suffix.lower() in TEXT_SUFFIXES
        if is_text:
            text = raw.decode("utf-8", errors="replace")
        else:
            text = "\n".join(m.group().decode("ascii")
                             for m in _PRINTABLE.finditer(raw))
        alias = bool(ALIAS_FILES.search(relative))
        # The prose regexes look for a document reference or an address. Run
        # against the printable runs pulled out of a font's glyph data they
        # match by coincidence and report nothing real, so they are for text
        # only. The machine traces run on everything.
        expressions = MACHINE_TRACES
        if is_text and not LICENCE_FILES.search(relative):
            expressions += PROSE_REFERENCES
        for line, pattern, excerpt in _search(
                text, always if alias else patterns, expressions):
            hits.append(Hit(path, line, pattern, excerpt.strip()))
    return hits


def report(hits: list[Hit]) -> str:
    lines = [f"{len(hits)} forbidden string(s) in the staged release:"]
    for hit in hits[:40]:
        where = f"{hit.path}:{hit.line}" if hit.line else f"{hit.path} (name)"
        lines.append(f"  {where}  {hit.pattern!r}  ...{hit.excerpt}...")
    if len(hits) > 40:
        lines.append(f"  ... and {len(hits) - 40} more")
    return "\n".join(lines)
