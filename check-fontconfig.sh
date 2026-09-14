#!/bin/sh
# SPDX-License-Identifier: CC0-1.0
#
# Check that 65-glowhei.conf does what it claims, without touching anything.
#
# To the extent possible under law, The GlowHei Authors have waived all
# copyright and related or neighbouring rights to this file. See LICENSE-CC0.
#
#   sh check-fontconfig.sh [font directory]
#
# The directory defaults to the one holding this script; pass another to test
# fonts that live elsewhere. Nothing is installed and no user configuration is
# read or written: FONTCONFIG_FILE points at a throwaway config for the run, so
# a failure cannot leave anything behind.
#
# Needs a real fontconfig. Some shells on Windows resolve `fc-match` to a
# bundled build that has no /etc/fonts and reports a screenful of failures that
# are not real; if every check fails at once, that is why.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
FONTS=${1:-$HERE}
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/fonts" "$T/conf.d"

found=$(find "$FONTS" -maxdepth 2 \( -name '*.ttc' -o -name '*.ttf' \) 2>/dev/null)
[ -n "$found" ] || { echo "no fonts under $FONTS" >&2; exit 1; }
echo "$found" | while read -r f; do cp "$f" "$T/fonts/"; done
cp "$HERE/65-glowhei.conf" "$T/conf.d/"
cat > "$T/fonts.conf" <<EOF
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <dir>$T/fonts</dir>
  <!-- The system font directories are required. With only this project's fonts
       present, serif has nothing to resolve to and fontconfig falls back on a
       sans, which reads as a failure that is not real. -->
  <dir>/usr/share/fonts</dir>
  <dir>/usr/local/share/fonts</dir>
  <cachedir>$T/cache</cachedir>
  <include ignore_missing="no">/etc/fonts/conf.d</include>
  <include ignore_missing="no">$T/conf.d</include>
</fontconfig>
EOF
export FONTCONFIG_FILE="$T/fonts.conf"
fc-cache -f "$T/fonts" >/dev/null 2>&1 || true
fail=0

check() {  # check <label> <expected substring> <actual>
  if printf '%s' "$3" | grep -qF "$2"; then
    printf '  ok   %-34s %s\n' "$1" "$3"
  else
    printf '  FAIL %-34s expected %s, got %s\n' "$1" "$2" "$3"
    fail=$((fail + 1))
  fi
}

m() { fc-match -f '%{family[0]}' "$1" 2>/dev/null; }

echo "== every face is found =="
# Installing this font does not decide what the desktop's default sans is.
# What has to work is that all five faces can be found and are classified, so
# that whatever the desktop offers for choosing a font can offer these.
for fam in "GlowHei" "GlowHei GB" "GlowHei ExtA" "GlowHei Mono" "GlowHei GB Mono"; do
  if fc-list -f '%{family[0]}\n' 2>/dev/null | grep -qxF "$fam"; then
    printf '  ok   %-34s listed\n' "$fam"
  else
    printf '  FAIL %-34s not found\n' "$fam"
    fail=$((fail + 1))
  fi
done

echo "== the generic families are not claimed =="
# Reported, not asserted. This file carries no <prefer> and no strong prepend
# for a generic, which is the whole of the promise; whether this font still
# scores highest for one depends on fontconfig and on what else is installed.
#
# Matched on the opening pair rather than the bare word, so that the comment
# explaining the absence is not mistaken for the thing itself.
if grep -q "<prefer><family>" "$HERE/65-glowhei.conf"; then
  printf '  FAIL %-34s the configuration claims a generic\n' "no <prefer>"
  fail=$((fail + 1))
else
  printf '  ok   %-34s no <prefer> in the configuration\n' "generics not claimed"
fi
for generic in serif sans-serif monospace; do
  printf '  note %-34s resolves to %s\n' "$generic" "$(m "$generic")"
done

echo "== old family names are substituted =="
check SimHei         "GlowHei"            "$(m SimHei)"
check "SimHei (zh)"  "GlowHei"            "$(m 黑体)"

echo "== monospace classification =="
# fontconfig works spacing out from the advances it finds, and a CJK monospace
# face has two of them - half width and full width, exactly double. That reads
# as FC_DUAL (90), so without the scan rule the face is missing from every
# list a terminal or font picker builds by asking for spacing=100.
for fam in "GlowHei Mono" "GlowHei GB Mono"; do
  if fc-list -f '%{family[0]}\n' :spacing=100 2>/dev/null | grep -qxF "$fam"; then
    printf '  ok   %-34s listed as monospace\n' "$fam"
  elif fc-list -f '%{family[0]}\n' 2>/dev/null | grep -qxF "$fam"; then
    printf '  FAIL %-34s present but not spacing=100\n' "$fam"
    fail=$((fail + 1))
  else
    printf '  skip %-34s not installed\n' "$fam"
  fi
done
check "GlowHei Mono spacing" "100" \
  "$(fc-match -f '%{spacing}' 'GlowHei Mono' 2>/dev/null)"

echo "== hinting must use the font's own instructions =="
# hintslight runs FreeType's autohinter and ignores the bytecode this font
# carries, which is most of its file size.
# fc-match prints the constant, not its name: hintfull is 3.
check "hintstyle is hintfull (3)" "3" \
  "$(fc-match -f '%{hintstyle}' GlowHei 2>/dev/null)"
check "hinting on" "True" \
  "$(fc-match -f '%{hinting}' GlowHei 2>/dev/null)"

echo "== Ext A fallback =="
check "U+3400 falls to ExtA" "GlowHei ExtA" "$(m 'GlowHei:charset=3400')"
check "U+4E2D stays put"     "GlowHei"      "$(m 'GlowHei:charset=4e2d')"

echo
if [ "$fail" -eq 0 ]; then echo "all passed"; else echo "$fail check(s) failed"; fi
exit "$fail"
