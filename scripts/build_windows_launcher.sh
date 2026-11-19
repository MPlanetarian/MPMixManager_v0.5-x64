#!/usr/bin/env bash
# ==============================================================================
# scripts/build_windows_launcher.sh
# Purpose: Rebuild MP_Mix_Manager.exe and manager.exe from source.
#
# The launcher is a native PE32+ (x86-64) Windows console binary, built with
# the MinGW-w64 cross toolchain. On an immutable distro (Bazzite/Fedora
# Silverblue) use the default container build; --native uses a host toolchain.
#
#   ./scripts/build_windows_launcher.sh                 # container build
#   ./scripts/build_windows_launcher.sh --native        # host toolchain
#   ./scripts/build_windows_launcher.sh --image fedora:44
#
# Version info comes from scripts/windows_launcher.rc — bump that (and VERSION)
# before a release. The window icon is assets/mp_liquid_glass_icon.ico, the
# same Dreamworld Aurora artwork the MP desktop shortcuts and tray use.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -P "$SCRIPT_DIR/.." && pwd)"

IMAGE="registry.fedoraproject.org/fedora:44"
MODE="container"

while [ $# -gt 0 ]; do
    case "$1" in
        --native) MODE="native" ;;
        --image)  IMAGE="${2:-}"; shift ;;
        -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

for f in \
    "$ROOT/scripts/windows_launcher.c" \
    "$ROOT/scripts/windows_launcher.rc" \
    "$ROOT/assets/mp_liquid_glass_icon.ico"
do
    [ -f "$f" ] || { echo "Missing required file: $f" >&2; exit 1; }
done

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

# Recent windres versions refuse a bare .ico argument, so reference the icon
# from an .rc. The resource name matches what earlier releases shipped.
make_icon_rc() {
    local dest="$1" ico="$2"
    printf 'MP_MIX_MANAGER_WINDOWS_ICON ICON "%s"\n' "$ico" > "$dest"
}

if [ "$MODE" = "native" ]; then
    for c in x86_64-w64-mingw32-gcc x86_64-w64-mingw32-windres; do
        command -v "$c" >/dev/null || {
            echo "Missing $c — install mingw64-gcc and mingw64-binutils" >&2
            exit 1
        }
    done
    make_icon_rc "$OUT/icon.rc" "$ROOT/assets/mp_liquid_glass_icon.ico"
    x86_64-w64-mingw32-windres -O coff -i "$OUT/icon.rc" -I "$ROOT" -o "$OUT/icon.o"
    x86_64-w64-mingw32-windres -O coff -i "$ROOT/scripts/windows_launcher.rc" -I "$ROOT" -o "$OUT/version.o"
    x86_64-w64-mingw32-gcc -O2 -s -municode -DUNICODE -D_UNICODE -mconsole \
        -o "$OUT/launcher.exe" \
        "$ROOT/scripts/windows_launcher.c" "$OUT/version.o" "$OUT/icon.o"
else
    command -v podman >/dev/null || { echo "podman not found; try --native" >&2; exit 1; }
    podman run --rm \
        -v "$ROOT":/src:ro,Z \
        -v "$OUT":/out:rw,Z \
        "$IMAGE" \
        bash -c '
            set -euo pipefail
            dnf -y install --setopt=install_weak_deps=False \
                mingw64-gcc mingw64-binutils >/dev/null 2>&1
            cd /src
            printf "MP_MIX_MANAGER_WINDOWS_ICON ICON \"assets/mp_liquid_glass_icon.ico\"\n" \
                > /out/icon.rc
            x86_64-w64-mingw32-windres -O coff -i /out/icon.rc -I /src -o /out/icon.o
            x86_64-w64-mingw32-windres -O coff -i scripts/windows_launcher.rc -I /src -o /out/version.o
            x86_64-w64-mingw32-gcc -O2 -s -municode -DUNICODE -D_UNICODE -mconsole \
                -o /out/launcher.exe \
                scripts/windows_launcher.c /out/version.o /out/icon.o
        '
fi

[ -s "$OUT/launcher.exe" ] || { echo "Build produced no binary" >&2; exit 1; }

install -m 644 "$OUT/launcher.exe" "$ROOT/manager.exe"
install -m 644 "$OUT/launcher.exe" "$ROOT/MP_Mix_Manager.exe"

echo "Installed manager.exe and MP_Mix_Manager.exe"
file "$ROOT/manager.exe" 2>/dev/null || true
