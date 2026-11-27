#!/usr/bin/env bash
# ==============================================================================
# scripts/build_windows_msi.sh
# Purpose: Build the Windows 10/11 MSI installers (x64 and x86).
#
# The 0.5.4 packages were built with WiX 5.0.2 on a Windows 11 test VM. This
# script builds the same two MSIs on any Windows host, needing only a .NET SDK
# and the WiX 5 dotnet tool. Nothing is installed to C:; point MP_BUILD_ROOT at
# a roomy drive and the SDK, the WiX tool and all scratch output live there.
#
#   MP_BUILD_ROOT=X:/mp-build ./scripts/build_windows_msi.sh
#   MP_BUILD_ROOT=X:/mp-build ./scripts/build_windows_msi.sh --clean
#
# Output: $MP_BUILD_ROOT/out/MP_Mix_Manager_Windows_x64.msi
#         $MP_BUILD_ROOT/out/MP_Mix_Manager_Windows_x86.msi
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -P "$SCRIPT_DIR/.." && pwd)"
BUILD_ROOT="${MP_BUILD_ROOT:-/x/mp-build}"
CLEAN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --clean) CLEAN=1 ;;
        -h|--help) sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

# Git Bash reports X: as /x, so translate the drive-rooted default.
case "$BUILD_ROOT" in
    [A-Za-z]:[\\/]*) BUILD_ROOT="/$(printf '%s' "${BUILD_ROOT%%:*}" | tr 'A-Z' 'a-z')${BUILD_ROOT#?:}" ;;
esac

DOTNET="$BUILD_ROOT/dotnet/dotnet.exe"
WIX="$BUILD_ROOT/tools/wix.exe"
STAGE="$BUILD_ROOT/stage"
OUT="$BUILD_ROOT/out"
WORK="$BUILD_ROOT/wix"

[ -x "$DOTNET" ] || { echo "Missing .NET SDK at $DOTNET (see WINDOWS_MSI_BUILD.md)" >&2; exit 1; }
[ -x "$WIX" ]    || { echo "Missing WiX tool at $WIX (see WINDOWS_MSI_BUILD.md)" >&2; exit 1; }

export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_NOLOGO=1

if [ "$CLEAN" -eq 1 ]; then
    echo "cleaning $STAGE $WORK $OUT"
    rm -rf "$STAGE" "$WORK" "$OUT"
fi
mkdir -p "$STAGE" "$WORK" "$OUT"

PY=""
# Git Bash resolves python3 to the Microsoft Store alias, which is not an
# interpreter. Prefer the project venv, then the Windows launcher helper.
for candidate in \
    "$ROOT/venv/Scripts/python.exe" \
    "$ROOT/.venv/Scripts/python.exe" \
    "$(command -v python3 2>/dev/null || true)" \
    "$(command -v python 2>/dev/null || true)"
do
    [ -n "$candidate" ] || continue
    case "$candidate" in *WindowsApps*) continue ;; esac
    [ -x "$candidate" ] || continue
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
        PY="$candidate"
        break
    fi
done
[ -n "$PY" ] || { echo "Python 3.8+ not found (set one on PATH or create venv/)" >&2; exit 1; }
echo "python: $PY"

echo "== staging payload =="
"$PY" "$ROOT/scripts/stage_windows_payload.py" "$STAGE"

echo "== generating WiX payload =="
"$PY" "$ROOT/scripts/gen_wix_payload.py" "$STAGE" "$WORK/Payload.wxs"

for ARCH in x64 x86; do
    echo "== building $ARCH =="
    "$WIX" build "$ROOT/scripts/wix/Product.wxs" "$WORK/Payload.wxs" \
        -arch "$ARCH" \
        -bindpath "$STAGE" \
        -o "$OUT/MP_Mix_Manager_Windows_$ARCH.msi"
done

echo "== done =="
ls -l "$OUT"
