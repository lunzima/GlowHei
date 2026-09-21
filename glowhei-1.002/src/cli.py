"""Build entry point.

    python -m build.cli                    # slim full ext_a
    python -m build.cli full               # named products only
    python -m build.cli --no-fontforge --no-hinting
"""
import argparse
import sys
import time
from pathlib import Path

from build import assemble, charset, metrics

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
WORK = ROOT / "work"

TARGETS = {
    "slim": ("GlowHei-GB2312.ttc", charset.slim,
             (metrics.SLIM_PROP, metrics.SLIM_MONO)),
    "full": ("GlowHei-GBK.ttc", charset.full,
             (metrics.FULL_PROP, metrics.FULL_MONO)),
    # Ext A is entirely full-width Han, so both faces would be identical and
    # only one is built.
    "ext_a": ("GlowHei-ExtA.ttf", charset.ext_a, (metrics.EXT_A,)),
}

DEFAULT_TARGETS = ("slim", "full", "ext_a")


def build_target(name: str, *, use_fontforge: bool = True,
                 use_hinting: bool = True) -> tuple[Path, int, int]:
    filename, charset_fn, specs = TARGETS[name]
    out = OUT / filename
    options = {"use_fontforge": use_fontforge, "use_hinting": use_hinting,
               "workdir": WORK / f"build_{name}"}

    if len(specs) == 2:
        collection = assemble.build_pair(charset_fn(), specs[0], specs[1],
                                         **options)
        return out, assemble.save_ttc(collection, out), len(collection.fonts)

    font = assemble.build_single(charset_fn(), specs[0], **options)
    out.parent.mkdir(parents=True, exist_ok=True)
    font.save(str(out))
    return out, out.stat().st_size, 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build GlowHei")
    parser.add_argument("targets", nargs="*",
                        help=f"products to build ({'/'.join(TARGETS)}); "
                             f"omit for {' '.join(DEFAULT_TARGETS)}")
    parser.add_argument("--no-fontforge", action="store_true",
                        help="skip simplification (about 20%% larger)")
    parser.add_argument("--no-hinting", action="store_true",
                        help="skip hinting (about half the size, softer small)")
    args = parser.parse_args(argv)
    names = args.targets or list(DEFAULT_TARGETS)

    unknown = [n for n in names if n not in TARGETS]
    if unknown:
        parser.error(f"unknown product {unknown}; choose from {list(TARGETS)}")

    total = 0
    print(f"building {len(names)} product(s)\n", flush=True)
    for name in names:
        started = time.time()
        out, size, faces = build_target(
            name, use_fontforge=not args.no_fontforge,
            use_hinting=not args.no_hinting,
        )
        total += size
        print(f"  {out.name:24s} {size / 1048576:5.2f} MB  {faces} face(s)  "
              f"({time.time() - started:.0f}s)", flush=True)
    print(f"\ntotal {total / 1048576:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
