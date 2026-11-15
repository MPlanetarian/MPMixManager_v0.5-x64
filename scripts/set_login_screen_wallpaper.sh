#!/usr/bin/env bash
# ==============================================================================
# Script: set_login_screen_wallpaper.sh
# Description: Puts the MPlanetarian Dreamworlds picture on the login / lock
#              screen of every greeter on this machine and positions it
#              "Centered // Scaled" (FillMode=1, whole picture, proportions
#              kept) instead of Tiled (FillMode=3) or Scaled-and-cropped (2).
#
#              Greeters handled:
#                - GDM        : the patched gnome-shell-theme.gresource
#                               (#lockDialogGroup rule), rebuilt through
#                               /usr/local/bin/mp-gdm-theme-rebuild
#                - Plasma Login: /etc/plasmalogin.conf
#                - KDE lock screen (/etc/xdg fallback + ~/.config/kscreenlockerrc)
#
#              Run without arguments to fix this machine. Pass --other to also
#              fix the Bazzite install on the bazzite_raid volume, which is a
#              separate ostree system and carries its own greeter configs.
#
# Usage:  ./set_login_screen_wallpaper.sh [--other] [--dry-run] [--status]
# ==============================================================================
set -euo pipefail

IMAGE="${MP_LOGIN_WALLPAPER:-}"
if [[ -z $IMAGE ]]; then
    # Resolve the *invoking* user's home, not root's: this script re-execs
    # itself through sudo, where $HOME is /root.
    OWNER="${SUDO_USER:-$(id -un)}"
    OWNER_HOME=$(getent passwd "$OWNER" | cut -d: -f6)
    IMAGE="${OWNER_HOME:-$HOME}/Pictures/MPlanetarian_Dreamworlds_Lockscreen.png"
fi
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
GDM_IMAGE="${MP_GDM_WALLPAPER:-$IMAGE}"
PLASMALOGIN_WALLPAPERS="/var/lib/plasmalogin/wallpapers"
PLASMALOGIN_IMAGE="${PLASMALOGIN_WALLPAPERS}/$(basename "$IMAGE")"
GDM_REBUILD="/usr/local/bin/mp-gdm-theme-rebuild"
OTHER_MOUNT="${MP_OTHER_BAZZITE:-/run/media/system/bazzite_raid}"
FILLMODE=1          # 1 = PreserveAspectFit -> "Centered // Scaled"
HELPER="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/set_login_screen_wallpaper.py"

DO_OTHER=0
DRY_RUN=0
DRY_FLAG=""      # empty or --dry-run, never the string "0"
STATUS_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --other) DO_OTHER=1 ;;
        --dry-run) DRY_RUN=1; DRY_FLAG=--dry-run ;;
        --status) STATUS_ONLY=1 ;;
        -h|--help) sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

[[ -r $HELPER ]] || { echo "helper missing: $HELPER" >&2; exit 1; }

say() { printf '  %s\n' "$*"; }

# --- status -------------------------------------------------------------------
if (( STATUS_ONLY )); then
    echo "== GDM (#lockDialogGroup)"
    if [[ -r /var/lib/mp-gdm-theme/gnome-shell-theme.gresource ]]; then
        strings /var/lib/mp-gdm-theme/gnome-shell-theme.gresource 2>/dev/null \
            | grep -A4 '#lockDialogGroup {' | sed 's/^/  /' | head -8
    else
        say "no patched gresource"
    fi
    echo "== Plasma Login"
    grep -A8 -E '^\[Greeter' /etc/plasmalogin.conf 2>/dev/null | sed 's/^/  /' || say "(no wallpaper set)"
    echo "== KDE lock screen (~/.config)"
    grep -E 'FillMode|Image=' "$TARGET_HOME/.config/kscreenlockerrc" 2>/dev/null | sed 's/^/  /' || say "(none)"
    echo "== KDE lock screen (/etc/xdg)"
    grep -E 'FillMode|Image=' /etc/xdg/kscreenlockerrc 2>/dev/null | sed 's/^/  /' || say "(none)"
    if (( DO_OTHER )) && [[ -d $OTHER_MOUNT ]]; then
        echo "== Other install: $OTHER_MOUNT"
        grep -E 'FillMode|Image=' "$OTHER_MOUNT/home/$TARGET_USER/.config/kscreenlockerrc" 2>/dev/null | sed 's/^/  /' || true
        for d in "$OTHER_MOUNT"/root/ostree/deploy/*/deploy/*.0; do
            [[ -e $d/etc/plasmalogin.conf ]] || continue
            printf '  -- %s\n' "$(basename "$d")"
            grep -E 'FillMode|Image=' "$d/etc/plasmalogin.conf" 2>/dev/null | sed 's/^/     /' || true
        done
    fi
    exit 0
fi

(( EUID == 0 )) || { echo "Root is required. Re-running with sudo..." >&2; exec sudo -- "$0" "$@"; }

echo "=============================================================="
echo "  MPlanetarian Dreamworlds - login screen wallpaper"
echo "  Positioning: Centered // Scaled (FillMode=${FILLMODE})"
echo "=============================================================="

# --- 1. GDM -------------------------------------------------------------------
if [[ -x $GDM_REBUILD ]]; then
    echo "[1/4] GDM login screen"
    if (( DRY_RUN )); then
        say "would rebuild the patched gresource from $GDM_IMAGE"
    else
        IMG="$GDM_IMAGE" "$GDM_REBUILD" | sed 's/^/  /'
        systemctl restart mp-gdm-theme.service 2>/dev/null || true
        say "mp-gdm-theme.service: $(systemctl is-active mp-gdm-theme.service 2>/dev/null || echo unknown)"
    fi
else
    echo "[1/4] GDM login screen: skipped (no ${GDM_REBUILD})"
fi

# --- 2. Plasma Login greeter --------------------------------------------------
echo "[2/4] Plasma Login greeter"
if [[ -f $IMAGE ]]; then
    if (( DRY_RUN )); then
        say "would install $IMAGE -> $PLASMALOGIN_IMAGE"
    else
        install -o plasmalogin -g plasmalogin -m 644 "$IMAGE" "$PLASMALOGIN_IMAGE"
        say "installed $(basename "$PLASMALOGIN_IMAGE")"
    fi
    python3 "$HELPER" $DRY_FLAG /etc/plasmalogin.conf \
        --image "file://${PLASMALOGIN_IMAGE}" | sed 's/^/  /'
else
    say "wallpaper missing: $IMAGE (leaving the current picture alone)"
    python3 "$HELPER" $DRY_FLAG /etc/plasmalogin.conf | sed 's/^/  /'
fi

# --- 3. KDE lock screen -------------------------------------------------------
echo "[3/4] KDE lock screen"
# The /etc/xdg file is the fallback the greeter reads when the per-user file has
# no picture, so pin the Dreamworlds image in both places. Without this the
# system-wide file keeps handing back the stock Fedora wallpaper.
for f in /etc/xdg/kscreenlockerrc "$TARGET_HOME/.config/kscreenlockerrc"; do
    [[ -e $f ]] || continue
    python3 "$HELPER" $DRY_FLAG "$f" --image "$IMAGE" | sed 's/^/  /'
done

# --- 4. Other install ---------------------------------------------------------
if (( DO_OTHER )); then
    echo "[4/4] Other Bazzite install: $OTHER_MOUNT"
    if [[ ! -d $OTHER_MOUNT ]]; then
        say "not mounted, skipping"
    else
        home="$OTHER_MOUNT/home/$TARGET_USER"
        [[ -e $home/.config/kscreenlockerrc ]] && python3 "$HELPER" $DRY_FLAG "$home/.config/kscreenlockerrc" | sed 's/^/  /'
        shared="file://${PLASMALOGIN_IMAGE}"
        for d in "$OTHER_MOUNT"/root/ostree/deploy/*/deploy/*.0; do
            [[ -e $d/etc/plasmalogin.conf ]] || continue
            python3 "$HELPER" $DRY_FLAG "$d/etc/plasmalogin.conf" --image "$shared" | sed 's/^/  /'
            # The deployment's system-wide lock-screen fallback must name the same
            # picture, otherwise it hands back the stock Fedora wallpaper.
            [[ -e $d/etc/xdg/kscreenlockerrc ]] && \
                python3 "$HELPER" $DRY_FLAG "$d/etc/xdg/kscreenlockerrc" --image "$shared" | sed 's/^/  /'
        done
    fi
else
    echo "[4/4] Other Bazzite install: skipped (pass --other)"
fi

echo
echo "Done. The GDM login screen updates on the next greeter restart;"
echo "Plasma Login and the KDE lock screen pick this up next time they start."
