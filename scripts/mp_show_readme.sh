#!/usr/bin/env bash
# ==============================================================================
# mp_show_readme.sh — offer to open README.md during or after installation.
# ------------------------------------------------------------------------------
# The installer spends several minutes fetching packages, and the person
# watching has nothing to read. This offers the README instead:
#
#   * On a desktop session it opens with the user's own default handler
#     (xdg-open / gio open / open / cmd start), so it appears in whichever text
#     editor or browser they have registered for Markdown.
#   * On a TTY-only session, or when no graphical session exists, it prints the
#     absolute path of the README and the commands that would open it. Nothing
#     is launched, because there is nothing to launch it on.
#
# Usage:
#   mp_show_readme.sh [--install]      offer to open it (asks first)
#   mp_show_readme.sh --open           open it without asking (desktop only)
#   mp_show_readme.sh --path           print the resolved path and exit
#
# Environment:
#   MP_SHOW_README=always|ask|never    decides whether to offer at all
#   MP_README_FILE=<path>              override the README location
#
# Exit status is always 0 unless the file is missing; this helper must never
# fail an installation.
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -P -- "$SCRIPT_DIR/.." && pwd -P)"

BOLD='\033[1m'; DIM='\033[2m'; GREEN='\033[0;32m'
YELLOW='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'

# Locate README.md. The install tree keeps it at the top level; a packaged tree
# may carry it under a doc directory, so check both before giving up.
find_readme() {
    local candidate
    for candidate in \
        "${MP_README_FILE:-}" \
        "$ROOT_DIR/README.md" \
        "$ROOT_DIR/docs/README.md" \
        "$ROOT_DIR/../README.md"; do
        [ -n "$candidate" ] || continue
        [ -f "$candidate" ] && { printf '%s' "$candidate"; return 0; }
    done
    return 1
}

# Is there a graphical session we could open a window on? SSH and cron runs
# have DISPLAY set only when X forwarding was requested, so also require a
# reachable display socket rather than trusting the variable alone.
has_desktop_session() {
    if [ -n "${WAYLAND_DISPLAY:-}" ]; then
        return 0
    fi
    if [ -n "${DISPLAY:-}" ]; then
        return 0
    fi
    # macOS and Windows always have a session when a user is logged in.
    case "$(uname -s 2>/dev/null)" in
        Darwin) return 0 ;;
        MINGW*|MSYS*|CYGWIN*) return 0 ;;
    esac
    return 1
}

# Open the file with the platform's default handler. Each candidate is only
# used when it exists, and a failure falls through to the next one.
open_readme() {
    local file="$1"
    case "$(uname -s 2>/dev/null)" in
        Darwin)
            command -v open >/dev/null 2>&1 && { open "$file" >/dev/null 2>&1 && return 0; }
            ;;
        MINGW*|MSYS*|CYGWIN*)
            # cmd.exe start resolves the shell association for .md.
            #
            # ``//c`` is deliberate. Git Bash / MSYS rewrites a lone ``/c``
            # into a Windows path, so ``cmd.exe /c start ...`` actually
            # launches an *interactive* cmd.exe. That prompt opens over the
            # installer's window, swallows the rest of the log, and returns 0
            # even though nothing was opened. The doubled slash survives the
            # rewrite and reaches cmd.exe as the real switch.
            if command -v cmd.exe >/dev/null 2>&1; then
                cmd.exe //c start "" "$(cygpath -w "$file" 2>/dev/null || printf '%s' "$file")" >/dev/null 2>&1 && return 0
            fi
            ;;
    esac
    # Desktop Linux and anything else with a freedesktop handler.
    command -v xdg-open  >/dev/null 2>&1 && { xdg-open  "$file" >/dev/null 2>&1 && return 0; }
    command -v gio       >/dev/null 2>&1 && { gio open   "$file" >/dev/null 2>&1 && return 0; }
    command -v kde-open  >/dev/null 2>&1 && { kde-open  "$file" >/dev/null 2>&1 && return 0; }
    command -v kde-open5 >/dev/null 2>&1 && { kde-open5 "$file" >/dev/null 2>&1 && return 0; }
    command -v gnome-open >/dev/null 2>&1 && { gnome-open "$file" >/dev/null 2>&1 && return 0; }
    return 1
}

# Print the path plus a copy-pasteable way to open it, for a TTY session.
print_path_hint() {
    local file="$1"
    echo ""
    echo -e "  ${BOLD}${CYAN}The manual is here:${NC}"
    echo -e "    ${BOLD}$file${NC}"
    echo ""
    echo -e "  ${DIM}Open it with any of these:${NC}"
    echo -e "    ${DIM}less \"$file\"${NC}"
    case "$(uname -s 2>/dev/null)" in
        Darwin) echo -e "    ${DIM}open \"$file\"${NC}" ;;
        MINGW*|MSYS*|CYGWIN*)
            echo -e "    ${DIM}cmd.exe //c start \"\" \"$(cygpath -w "$file" 2>/dev/null || printf '%s' "$file")\"${NC}"
            ;;
        *)      echo -e "    ${DIM}xdg-open \"$file\"${NC}" ;;
    esac
    echo ""
}

MODE="ask"
case "${1:-}" in
    --open)  MODE="open" ;;
    --path)  MODE="path" ;;
    --install) MODE="ask" ;;
    ""|--offer) MODE="ask" ;;
    *) MODE="ask" ;;
esac

README_FILE="$(find_readme)" || {
    [ "$MODE" = "path" ] && exit 1
    printf '%s\n' "${DIM}  (README.md was not found beside the installation.)${NC}"
    exit 0
}

if [ "$MODE" = "path" ]; then
    printf '%s\n' "$README_FILE"
    exit 0
fi

# MP_SHOW_README=never silences the offer entirely; =always opens it without
# asking, which is useful for unattended provisioning.
CHOICE="$(printf '%s' "${MP_SHOW_README:-ask}" | tr '[:upper:]' '[:lower:]')"
case "$CHOICE" in
    never|no|0|false) exit 0 ;;
    always|yes|1|true) MODE="open" ;;
esac

if [ "$MODE" = "open" ]; then
    if has_desktop_session && open_readme "$README_FILE"; then
        echo -e "  ${GREEN}✓ Opened the manual.${NC}"
        echo -e "  ${DIM}The installation log continues in this window.${NC}"
    else
        print_path_hint "$README_FILE"
    fi
    exit 0
fi

# --ask: only prompt when there is a terminal to answer on.
if [ ! -t 0 ]; then
    # An unattended install cannot answer. Say where the file is and move on;
    # printing a prompt that nobody will see would only confuse the log.
    print_path_hint "$README_FILE"
    exit 0
fi

if has_desktop_session; then
    echo -e "  ${BOLD}Would you like to read the manual while the rest installs?${NC}"
    echo -e "  ${DIM}It opens in your default text editor.${NC}"
    read -r -p "  Open README.md now? [Y/n] " _mp_readme_ans || _mp_readme_ans=""
    case "${_mp_readme_ans:-y}" in
        n|N|no|NO)
            echo -e "  ${DIM}Skipped. The manual is at: $README_FILE${NC}"
            ;;
        *)
            if open_readme "$README_FILE"; then
                echo -e "  ${GREEN}✓ Opened the manual.${NC}"
                echo -e "  ${DIM}The installation log continues in this window.${NC}"
            else
                echo -e "  ${YELLOW}⚠ No handler would open it.${NC}"
                print_path_hint "$README_FILE"
            fi
            ;;
    esac
    unset _mp_readme_ans
else
    # TTY-only: there is no window to draw in, so give the path instead of
    # pretending to offer a prompt.
    echo -e "  ${DIM}No desktop session detected, so the manual is not being opened.${NC}"
    print_path_hint "$README_FILE"
fi

exit 0
