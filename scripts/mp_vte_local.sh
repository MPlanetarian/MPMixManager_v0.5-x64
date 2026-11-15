#!/usr/bin/env bash
# ==============================================================================
# mp_vte_local.sh - provide Vte-2.91 for MP Liquid Glass without root
# ------------------------------------------------------------------------------
# MP Liquid Glass embeds a live terminal (Vte) inside the glass window. Bazzite,
# Silverblue and other immutable images do not ship vte291, and a user cannot
# layer a package onto them without a reboot. So this script downloads the
# distribution's own vte291 RPM, unpacks it, and installs the two files the app
# needs into a private directory:
#
#     ~/.local/lib/mp-liquid-glass/lib/libvte-2.91.so.0
#     ~/.local/lib/mp-liquid-glass/girepository-1.0/Vte-2.91.typelib
#
# scripts/launch_liquid_glass.sh and MP_Gui.sh put that directory on
# LD_LIBRARY_PATH and GI_TYPELIB_PATH before starting Python, and
# scripts/local_vte.py does the same for a direct `python3` launch.
#
# Usage
#   scripts/mp_vte_local.sh --check      # 0 when the bundle is already present
#   scripts/mp_vte_local.sh --install    # download and unpack (default)
#   scripts/mp_vte_local.sh --print-env  # eval-able exports for a caller
#   scripts/mp_vte_local.sh --status     # one line describing the state
#
# Environment
#   MP_VTE_FORCE=1        re-download even when a bundle is present
#   MP_VTE_NO_NETWORK=1   never touch the network (CI, offline installs)
#   MP_VTE_SILENT=1       suppress progress lines (still prints warnings)
#   MP_VTE_BASE           override the destination directory
#
# Exit codes
#   0  the bundle is present and usable
#   1  the bundle could not be provided (network, tool or package failure)
#   2  unsupported platform for this mechanism (not an error in the app)
# ==============================================================================
set -uo pipefail

BASE="${MP_VTE_BASE:-$HOME/.local/lib/mp-liquid-glass}"
LIB_DIR="$BASE/lib"
TY_DIR="$BASE/girepository-1.0"
MARKER="$BASE/.installed-by"
LIB_SO="libvte-2.91.so.0"
TYPELIB="Vte-2.91.typelib"

# Progress goes to stderr so a caller can capture a function's stdout (the
# fetch_* helpers print only the resolved package path there).
say()  { [ "${MP_VTE_SILENT:-0}" = "1" ] || printf '%s\n' "$*" >&2; }
warn() { printf 'mp_vte_local: %s\n' "$*" >&2; }

bundle_ok() {
    [ -f "$TY_DIR/$TYPELIB" ] && [ -e "$LIB_DIR/$LIB_SO" ]
}

print_env() {
    if bundle_ok; then
        printf 'export LD_LIBRARY_PATH="%s${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n' "$LIB_DIR"
        printf 'export GI_TYPELIB_PATH="%s${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"\n' "$TY_DIR"
    fi
}

have() { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------------------------
# Distro detection: which package manager, which package name, which arch
# ------------------------------------------------------------------------------
PKG_MGR=""
PKG_NAME=""
DISTRO=""

detect_distro() {
    local id="" like=""
    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        id="$( . /etc/os-release 2>/dev/null; printf '%s' "${ID:-}" )"
        like="$( . /etc/os-release 2>/dev/null; printf '%s' "${ID_LIKE:-}" )"
    fi
    case " $id $like " in
        *" fedora "*|*" rhel "*|*" centos "*|*" nobara "*)
            DISTRO="${id:-fedora}"; PKG_MGR="dnf"; PKG_NAME="vte291" ;;
        *" bazzite "*|*" silverblue "*|*" kinoite "*|*" ostree "*)
            # ID is often bazzite or fedora on these; dnf5 can still download.
            DISTRO="${id:-bazzite}"; PKG_MGR="dnf"; PKG_NAME="vte291" ;;
        *" debian "*|*" ubuntu "*|*" mint "*|*" pop "*)
            DISTRO="${id:-debian}"; PKG_MGR="apt"; PKG_NAME="libvte-2.91-0" ;;
        *" arch "*|*" manjaro "*|*" endeavouros "*)
            DISTRO="${id:-arch}"; PKG_MGR="pacman"; PKG_NAME="vte3" ;;
        *" opensuse "*|*" suse "*)
            DISTRO="${id:-opensuse}"; PKG_MGR="zypper"; PKG_NAME="libvte-2_91-0" ;;
        *" freebsd "*)
            DISTRO="freebsd"; PKG_MGR="pkg"; PKG_NAME="vte3" ;;
        *)
            DISTRO="${id:-unknown}"; PKG_MGR=""; PKG_NAME="" ;;
    esac
}

# ------------------------------------------------------------------------------
# Fetch: get a package file we can unpack, into $1
# ------------------------------------------------------------------------------
# Packages that carry a SONAME the VTE library links against. Recent Fedora
# vte291 builds link libsimdutf; it is not part of vte291 itself, so the bundle
# is incomplete without it and `gi.require_version` fails at run time.
EXTRA_PKGS_DNF="simdutf"
EXTRA_PKGS_APT=""
EXTRA_PKGS_PACMAN="simdutf"
EXTRA_PKGS_PKG="simdutf"

fetch_dnf() {  # fetch_dnf <workdir>  -> prints the primary RPM path
    local dir="$1"
    if ! have dnf5 && ! have dnf; then
        warn "dnf/dnf5 not found"
        return 1
    fi
    local dnfbin; dnfbin="$(command -v dnf5 || command -v dnf)"
    say "  Downloading $PKG_NAME with $dnfbin ..."
    # --destdir keeps the RPM in the work dir; no root, no install, no reboot.
    # dnf writes its progress to stderr, so both streams go to the log and only
    # the resolved path is printed on stdout for the caller to capture.
    if "$dnfbin" download --destdir "$dir" "$PKG_NAME" >"$dir/dnf.log" 2>&1; then
        :
    else
        warn "dnf download failed:"; sed -n '1,8p' "$dir/dnf.log" >&2
        return 1
    fi
    # Supporting runtime packages. A failure here is not fatal on its own; the
    # verify step reports any SONAME that is still unresolved.
    if [ -n "$EXTRA_PKGS_DNF" ]; then
        say "  Downloading runtime support: $EXTRA_PKGS_DNF ..."
        # shellcheck disable=SC2086
        "$dnfbin" download --destdir "$dir" $EXTRA_PKGS_DNF >>"$dir/dnf.log" 2>&1 || \
            warn "could not download $EXTRA_PKGS_DNF (the verify step will say what is missing)"
    fi
    # dnf may pull both arches; prefer the host's.
    local arch; arch="$(uname -m)"
    local rpm
    rpm="$(find "$dir" -maxdepth 1 -name "${PKG_NAME}-*.${arch}.rpm" | head -n 1)"
    if [ -z "$rpm" ]; then
        rpm="$(find "$dir" -maxdepth 1 -name "*.rpm" ! -name '*.i686.rpm' ! -name '*.i386.rpm' | head -n 1)"
    fi
    [ -n "$rpm" ] || { warn "no RPM was downloaded"; return 1; }
    printf '%s' "$rpm"
}

fetch_apt() {  # fetch_apt <workdir>
    local dir="$1"
    if ! have apt-get && ! have apt; then
        warn "apt not found"
        return 1
    fi
    say "  Downloading $PKG_NAME with apt-get download ..."
    # apt-get download needs no root and drops a .deb in the cwd.
    if ( cd "$dir" && apt-get download "$PKG_NAME" >"$dir/apt.log" 2>&1 ); then
        :
    else
        warn "apt-get download failed:"; sed -n '1,8p' "$dir/apt.log" >&2
        return 1
    fi
    local deb; deb="$(find "$dir" -maxdepth 1 -name '*.deb' | head -n 1)"
    [ -n "$deb" ] || { warn "no .deb was downloaded"; return 1; }
    printf '%s' "$deb"
}

fetch_pacman() {  # fetch_pacman <workdir>
    local dir="$1"
    local cached
    cached="$(ls /var/cache/pacman/pkg/${PKG_NAME}-*.pkg.tar.* 2>/dev/null | tail -n 1)"
    if [ -n "$cached" ]; then
        say "  Using cached ${cached##*/}"
        printf '%s' "$cached"; return 0
    fi
    if ! have pacman; then warn "pacman not found"; return 1; fi
    say "  Downloading $PKG_NAME with pacman -Sw ..."
    local pkgs="$PKG_NAME"
    [ -n "$EXTRA_PKGS_PACMAN" ] && pkgs="$pkgs $EXTRA_PKGS_PACMAN"
    # shellcheck disable=SC2086
    if ( cd "$dir" && pacman -Sw --noconfirm --cachedir "$dir" $pkgs >"$dir/pac.log" 2>&1 ); then
        local pkg; pkg="$(find "$dir" -maxdepth 1 -name "${PKG_NAME}-*.pkg.tar.*" | head -n 1)"
        [ -n "$pkg" ] || { warn "no package downloaded"; return 1; }
        printf '%s' "$pkg"; return 0
    fi
    warn "pacman -Sw failed:"; sed -n '1,8p' "$dir/pac.log" >&2
    return 1
}

fetch_pkg() {  # fetch_pkg <workdir>   (FreeBSD)
    local dir="$1"
    if ! have pkg; then warn "pkg not found"; return 1; fi
    say "  Downloading $PKG_NAME with pkg fetch ..."
    local pkgs="$PKG_NAME"
    [ -n "$EXTRA_PKGS_PKG" ] && pkgs="$pkgs $EXTRA_PKGS_PKG"
    # shellcheck disable=SC2086
    if pkg fetch -y -o "$dir" $pkgs >"$dir/pkg.log" 2>&1; then
        local tb; tb="$(find "$dir" -name "${PKG_NAME}-*.pkg" -o -name "${PKG_NAME}-*.txz" 2>/dev/null | head -n 1)"
        [ -n "$tb" ] || { warn "no package downloaded"; return 1; }
        printf '%s' "$tb"; return 0
    fi
    warn "pkg fetch failed:"; sed -n '1,8p' "$dir/pkg.log" >&2
    return 1
}

# ------------------------------------------------------------------------------
# Unpack: extract the two files we need out of whatever we downloaded
# ------------------------------------------------------------------------------
extract_from_dnf_rpm() {  # <rpm> <stage>
    local rpm="$1" stage="$2"
    if ! have rpm2cpio || ! have cpio; then
        warn "rpm2cpio and cpio are required to unpack the RPM"
        return 1
    fi
    # cpio reads the archive on stdin. Passing `.` as an operand makes it
    # filter for a member literally named "." and silently extract nothing, so
    # the archive goes in on stdin with no pattern at all.
    if ( cd "$stage" && rpm2cpio "$rpm" | cpio -idmu --quiet ) >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

extract_from_deb() {  # <deb> <stage>
    local deb="$1" stage="$2"
    if ! have dpkg-deb; then
        warn "dpkg-deb is required to unpack the .deb"
        return 1
    fi
    ( cd "$stage" && dpkg-deb -x "$deb" . ) >/dev/null 2>&1 || return 1
}

extract_from_pkg_tar() {  # <archive> <stage>   pacman .pkg.tar.zst / FreeBSD .pkg
    local arc="$1" stage="$2"
    case "$arc" in
        *.zst)  have zstd && ( cd "$stage" && tar --zstd -xf "$arc" ) || return 1 ;;
        *.xz)   ( cd "$stage" && tar -xJf "$arc" ) || return 1 ;;
        *.gz)   ( cd "$stage" && tar -xzf "$arc" ) || return 1 ;;
        *.pkg)  ( cd "$stage" && tar -xf "$arc" --include './usr/local/*' ) || return 1 ;;
        *)      return 1 ;;
    esac
}

# ------------------------------------------------------------------------------
# Place: copy from the staging tree into the private bundle
# ------------------------------------------------------------------------------
place_bundle() {  # <stage>
    local stage="$1"
    mkdir -p "$LIB_DIR" "$TY_DIR" || return 1
    # A previous run (or a hand-made bundle) may have left a stale symlink like
    # bare `libsimdutf` that shadows nothing useful but confuses a reader. Clear
    # only the symlinks we create, never a real file.
    for stale in libsimdutf libsimdutf.so.24; do
        [ -L "$LIB_DIR/$stale" ] && rm -f "$LIB_DIR/$stale"
    done

    local typelib
    typelib="$(find "$stage" \( -path '*/girepository-1.0/Vte-2.91.typelib' \) -type f 2>/dev/null | head -n 1)"
    if [ -z "$typelib" ]; then
        warn "the package did not contain Vte-2.91.typelib"
        return 1
    fi
    cp -f "$typelib" "$TY_DIR/$TYPELIB" || return 1

    # The real library plus any sibling it needs (libvte links libsimdutf on
    # recent Fedora builds). Symlinks are rebuilt in the bundle so the SONAME
    # resolves there and not only in the system prefix.
    local found=0 lib
    for lib in "$(find "$stage" -name 'libvte-2.91.so.*.*' -type f 2>/dev/null | head -n 1)" \
               "$(find "$stage" -name "libvte-2.91.so.0" -type f 2>/dev/null | head -n 1)"; do
        if [ -n "$lib" ] && [ -f "$lib" ]; then
            cp -f "$lib" "$LIB_DIR/" || return 1
            found=1
            break
        fi
    done
    if [ "$found" -eq 0 ]; then
        warn "the package did not contain libvte-2.91.so.0"
        return 1
    fi

    # Copy every supporting library the staging tree provides (libsimdutf and
    # any future sibling). They are small and the verify step names anything
    # still missing, so taking them all beats a hard-coded allowlist.
    local sibling
    for sibling in $(find "$stage" -type f \( -name 'libsimdutf.so.*.*' -o -name 'libvte-*.so.*.*' \) 2>/dev/null | sort -u); do
        cp -f "$sibling" "$LIB_DIR/" 2>/dev/null || true
    done

    # Recreate the SONAME symlinks inside the bundle. A library file is named
    # libfoo.so.X.Y.Z, but the dynamic loader asks for the SONAME libfoo.so.X —
    # without that symlink the dependency reports "not found".
    local f base soname
    for f in "$LIB_DIR"/lib*.so.*.*; do
        [ -f "$f" ] || continue
        base="$(basename "$f")"
        soname="$(printf '%s' "$base" | sed -E 's/^(lib[^.]+(\.[0-9]+)?\.so\.[0-9]+)\..*$/\1/')"
        [ -n "$soname" ] || continue
        [ "$soname" = "$base" ] && continue
        ln -sfn "$base" "$LIB_DIR/$soname" 2>/dev/null || true
    done
    return 0
}

# ------------------------------------------------------------------------------
# Verify: the bundle is loadable, not merely present
# ------------------------------------------------------------------------------
verify_bundle() {
    bundle_ok || { warn "the bundle is incomplete at $BASE"; return 1; }

    # The library must resolve its own dependencies with only the bundle on the
    # path, or Python will fail to import Vte at run time.
    local missing=""
    if have ldd; then
        missing="$(LD_LIBRARY_PATH="$LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
                   ldd "$LIB_DIR/$(basename "$(readlink -f "$LIB_DIR/$LIB_SO" 2>/dev/null || echo "$LIB_DIR/$LIB_SO")")" 2>/dev/null \
                   | grep 'not found' | awk '{print $1}' | tr '\n' ' ')"
    fi
    if [ -n "$missing" ]; then
        warn "the downloaded library still needs: $missing"
        warn "install the matching runtime packages for your distribution, then re-run."
        return 1
    fi

    # And the typelib must parse. This is the check that catches an ABI break
    # between the downloaded typelib and the system libgobject.
    if have python3; then
        if GI_TYPELIB_PATH="$TY_DIR${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}" \
           LD_LIBRARY_PATH="$LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
           python3 - <<'PY' >/dev/null 2>&1
import gi
gi.require_version("Vte", "2.91")
from gi.repository import Vte  # noqa: F401
PY
        then
            return 0
        fi
        # A parse failure here is only fatal when the system does not already
        # provide Vte; the caller (install.sh) decides what a failure means.
        warn "the bundle is present but gi cannot import Vte from it"
        return 1
    fi
    return 0
}

# ------------------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------------------
cmd_status() {
    if bundle_ok; then
        printf 'present  %s\n' "$BASE"
        [ -f "$MARKER" ] && printf 'source   %s\n' "$(cat "$MARKER" 2>/dev/null)"
        printf 'lib      %s\n' "$(readlink -f "$LIB_DIR/$LIB_SO" 2>/dev/null || echo "$LIB_DIR/$LIB_SO")"
        printf 'typelib  %s\n' "$TY_DIR/$TYPELIB"
        return 0
    fi
    printf 'absent   %s\n' "$BASE"
    return 1
}

cmd_install() {
    detect_distro

    if [ "${MP_VTE_NO_NETWORK:-0}" = "1" ]; then
        say "  Offline mode: skipping the VTE download."
        return 1
    fi

    case "$(uname -s)" in
        Linux|FreeBSD) ;;
        *)  say "  VTE bundling does not apply on $(uname -s)."; return 2 ;;
    esac

    if [ -z "$PKG_MGR" ]; then
        warn "unknown distribution '$DISTRO'; cannot name the VTE package"
        return 1
    fi

    local work; work="$(mktemp -d "${TMPDIR:-/tmp}/mp-vte.XXXXXX")" || return 1
    local stage="$work/stage"
    mkdir -p "$stage"
    trap 'rm -rf "$work"' RETURN 2>/dev/null || true

    say "  Fetching $PKG_NAME for $DISTRO ($PKG_MGR) ..."
    local pkg="" rc=0
    case "$PKG_MGR" in
        dnf)    pkg="$(fetch_dnf "$work")";    rc=$? ;;
        apt)    pkg="$(fetch_apt "$work")";    rc=$? ;;
        pacman) pkg="$(fetch_pacman "$work")"; rc=$? ;;
        pkg)    pkg="$(fetch_pkg "$work")";    rc=$? ;;
        *)      rc=1 ;;
    esac
    if [ "$rc" -ne 0 ] || [ -z "$pkg" ]; then
        warn "could not download $PKG_NAME"
        rm -rf "$work"
        return 1
    fi

    say "  Unpacking ${pkg##*/} ..."
    case "$pkg" in
        *.rpm)            extract_from_dnf_rpm "$pkg" "$stage" ;;
        *.deb)            extract_from_deb "$pkg" "$stage" ;;
        *.pkg.tar.*|*.pkg|*.txz) extract_from_pkg_tar "$pkg" "$stage" ;;
        *) warn "do not know how to unpack ${pkg##*/}"; rm -rf "$work"; return 1 ;;
    esac

    # Supporting packages land in the same directory; unpack them too so their
    # SONAMEs are inside the bundle rather than resolved from the system. Only
    # this machine's architecture is unpacked: dnf happily downloads both, and
    # an i686 copy extracted second would overwrite the x86_64 library with a
    # 32-bit ELF that the 64-bit process cannot load.
    local host_arch; host_arch="$(uname -m)"
    local extra_pkg
    for extra_pkg in $(find "$work" -maxdepth 1 -type f \( -name '*.rpm' -o -name '*.deb' -o -name '*.pkg.tar.*' \) 2>/dev/null); do
        [ "$extra_pkg" = "$pkg" ] && continue
        case "$extra_pkg" in
            *.i686.rpm|*.i386.rpm|*.noarch.rpm)
                [ "$host_arch" = "i686" ] || continue ;;
            *.aarch64.rpm)
                [ "$host_arch" = "aarch64" ] || continue ;;
        esac
        say "  Unpacking ${extra_pkg##*/} ..."
        case "$extra_pkg" in
            *.rpm) extract_from_dnf_rpm "$extra_pkg" "$stage" >/dev/null 2>&1 || true ;;
            *.deb) extract_from_deb "$extra_pkg" "$stage" >/dev/null 2>&1 || true ;;
            *.pkg.tar.*) extract_from_pkg_tar "$extra_pkg" "$stage" >/dev/null 2>&1 || true ;;
        esac
    done

    if ! place_bundle "$stage"; then
        rm -rf "$work"
        return 1
    fi

    printf '%s %s\n' "$DISTRO" "$(basename "$pkg")" > "$MARKER" 2>/dev/null || true

    if verify_bundle; then
        say "  ✓ VTE 2.91 installed to $BASE"
        rm -rf "$work"
        return 0
    fi

    rm -rf "$work"
    return 1
}

case "${1:---install}" in
    --check)     bundle_ok; exit $? ;;
    --print-env) print_env; exit 0 ;;
    --status)    cmd_status; exit $? ;;
    --install)   ;;
    -h|--help)
        sed -n '2,38p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | sed '/^====*$/d'
        exit 0 ;;
    *)  warn "unknown option: $1 (try --help)"; exit 2 ;;
esac

if bundle_ok && [ "${MP_VTE_FORCE:-0}" != "1" ]; then
    say "  VTE 2.91 already present at $BASE"
    exit 0
fi

cmd_install
