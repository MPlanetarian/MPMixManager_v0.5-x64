#!/bin/bash

CLI_THEME=""
CLI_EXPORT=""
POSITIONAL_ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --theme=*)
            CLI_THEME="${1#*=}"
            shift
            ;;
        --theme|-t)
            CLI_THEME="${2:-}"
            shift 2 2>/dev/null || shift
            ;;
        --export=*)
            CLI_EXPORT="${1#*=}"
            shift
            ;;
        --export|-x)
            CLI_EXPORT="${2:-}"
            shift 2 2>/dev/null || shift
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

TARGET_FILE="${POSITIONAL_ARGS[0]:-}"
AUDIO_FILE="${POSITIONAL_ARGS[1]:-${MP_TRACKLIST_AUDIO_FILE:-}}"

TRACKLIST_THEMES=(
    "stream_of_frequency"
    "dreamworlds"
    "dreamworlds_ultra"
    "cosmic_supernova"
    "dreamworlds_liquid"
    "dynamic_rainbow_liquid_glass"
    "sensorium"
    "cyberpunk"
    "dracula"
    "nord"
    "matrix"
    "solarized"
    "tokyo"
    "monokai"
    "gruvbox"
    "emerald"
    "classic"
    "retro_terminal"
    "retro_linux_memories"
)

set_theme_colors() {
    local raw_name="${1:-dreamworlds_ultra}"
    local theme_name
    theme_name="$(printf '%s' "$raw_name" | tr '[:upper:]' '[:lower:]' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"

    BOLD='\033[1m'
    DIM='\033[2m'
    ITALIC='\033[3m'
    UNDERLINE='\033[4m'
    WHITE='\033[1;37m'
    NC='\033[0m'

    case "$theme_name" in
        stream_of_frequency|stream|frequency|sof|frequency_stream|"stream of frequency"|"stream-of-frequency"|"stream of frequency mp mix manager")
            RED='\033[38;2;240;32;24m'
            GREEN='\033[38;2;76;217;123m'
            YELLOW='\033[38;2;245;237;106m'
            BLUE='\033[38;2;48;49;131m'
            MAGENTA='\033[38;2;151;79;249m'
            CYAN='\033[38;2;0;242;255m'
            CURRENT_THEME="stream_of_frequency"
            THEME_DISPLAY_NAME="Stream of Frequency"
            ;;
        dreamworlds|mplanetarian_dreamworlds|mplanetarian|"mplanetarian dreamworlds [default]"|"mplanetarian dreamworlds")
            RED='\033[38;2;237;37;78m'
            GREEN='\033[38;2;0;229;255m'
            YELLOW='\033[38;2;255;170;0m'
            BLUE='\033[38;2;155;89;182m'
            MAGENTA='\033[38;2;247;37;133m'
            CYAN='\033[38;2;0;193;228m'
            CURRENT_THEME="dreamworlds"
            THEME_DISPLAY_NAME="MPlanetarian Dreamworlds"
            ;;
        dreamworlds_ultra|ultra|default|dreamworlds_ultra_mp_mix_manager|hotpurple|trafficlight|"dreamworlds ultra mp mix manager"|"dreamworlds ultra"|"dreamworlds-ultra")
            RED='\033[38;2;255;0;0m'
            GREEN='\033[38;2;0;255;0m'
            YELLOW='\033[38;2;255;153;51m'
            BLUE='\033[38;2;102;102;255m'
            MAGENTA='\033[38;2;166;77;255m'
            CYAN='\033[38;2;153;153;255m'
            WHITE='\033[38;2;216;180;254m'
            CURRENT_THEME="dreamworlds_ultra"
            THEME_DISPLAY_NAME="Dreamworlds Ultra"
            ;;
        cosmic_supernova|supernova|cosmic|cosmic_supernova_mp_mix_manager|"cosmic supernova mp mix manager"|"cosmic supernova"|"cosmic-supernova"|"cosmic supernova [special]"|"cosmic supernova [special edition]")
            RED='\033[38;2;255;51;102m'
            GREEN='\033[38;2;0;245;212m'
            YELLOW='\033[38;2;254;228;64m'
            BLUE='\033[38;2;94;96;206m'
            MAGENTA='\033[38;2;224;86;253m'
            CYAN='\033[38;2;76;201;240m'
            CURRENT_THEME="cosmic_supernova"
            THEME_DISPLAY_NAME="Cosmic Supernova"
            ;;
        dreamworlds_liquid|mp_dreamworlds_liquid_transparency|dreamworlds_liquid_transparency|liquid_transparency|liquid|glass|liquid_glass|mp_liquid|"mp dreamworlds liquid transparency"|"mp dreamworlds liquid transparency [special edition]")
            RED='\033[38;2;255;75;130m'
            GREEN='\033[38;2;0;245;212m'
            YELLOW='\033[38;2;255;215;0m'
            BLUE='\033[38;2;72;149;239m'
            MAGENTA='\033[38;2;199;125;255m'
            CYAN='\033[38;2;0;245;255m'
            CURRENT_THEME="dreamworlds_liquid"
            THEME_DISPLAY_NAME="MP Dreamworlds Liquid Transparency"
            ;;
        dynamic_rainbow_liquid_glass|dynamic_liquid_glass_rainbow|dynamic_rainbow|rainbow_liquid_glass|rainbow|dynamic_liquid_glass|"dynamic_liquid_glass_[rainbow_colours]_(new)"|"dynamic_liquid_glass_[rainbow_colours]"|"dynamic liquid glass [rainbow colours] (new)"|"dynamic liquid glass [rainbow colours]"|"dynamic liquid glass")
            RED='\033[38;2;255;45;142m'
            GREEN='\033[38;2;0;242;254m'
            YELLOW='\033[38;2;255;215;0m'
            BLUE='\033[38;2;90;130;255m'
            MAGENTA='\033[38;2;176;88;255m'
            CYAN='\033[38;2;0;242;254m'
            CURRENT_THEME="dynamic_rainbow_liquid_glass"
            THEME_DISPLAY_NAME="Dynamic Liquid Glass [Rainbow Colours]"
            ;;
        sensorium|sensorium_interface|"sensorium"|"sensorium_interface_(liquid_glass_skin)"|"sensorium interface (liquid glass skin)"|"sensorium interface")
            RED='\033[38;2;94;20;15m'
            GREEN='\033[38;2;38;61;0m'
            YELLOW='\033[38;2;74;52;0m'
            BLUE='\033[38;2;11;47;107m'
            MAGENTA='\033[38;2;92;36;0m'
            CYAN='\033[38;2;10;58;72m'
            WHITE='\033[1;38;2;43;10;16m'
            CURRENT_THEME="sensorium"
            THEME_DISPLAY_NAME="Sensorium"
            ;;
        dreamworlds_neon|"dreamworlds neon")
            RED='\033[38;2;247;37;133m'
            GREEN='\033[38;2;0;245;212m'
            YELLOW='\033[38;2;255;215;0m'
            BLUE='\033[38;2;114;9;183m'
            MAGENTA='\033[38;2;247;37;133m'
            CYAN='\033[38;2;0;245;212m'
            CURRENT_THEME="dreamworlds_neon"
            THEME_DISPLAY_NAME="Dreamworlds Neon"
            ;;
        retro_studio|"retro studio")
            RED='\033[38;2;231;76;60m'
            GREEN='\033[38;2;241;196;15m'
            YELLOW='\033[38;2;243;156;18m'
            BLUE='\033[38;2;211;84;0m'
            MAGENTA='\033[38;2;241;196;15m'
            CYAN='\033[38;2;230;126;34m'
            CURRENT_THEME="retro_studio"
            THEME_DISPLAY_NAME="Retro Studio"
            ;;
        cyber_neon|"cyber neon")
            RED='\033[38;2;255;0;85m'
            GREEN='\033[38;2;0;245;212m'
            YELLOW='\033[38;2;254;228;64m'
            BLUE='\033[38;2;58;134;255m'
            MAGENTA='\033[38;2;255;0;85m'
            CYAN='\033[38;2;76;201;240m'
            CURRENT_THEME="cyber_neon"
            THEME_DISPLAY_NAME="Cyber Neon"
            ;;
        phosphor)
            RED='\033[38;2;57;255;20m'
            GREEN='\033[38;2;57;255;20m'
            YELLOW='\033[38;2;166;255;0m'
            BLUE='\033[38;2;0;180;60m'
            MAGENTA='\033[38;2;57;255;20m'
            CYAN='\033[38;2;0;255;102m'
            WHITE='\033[38;2;212;255;212m'
            CURRENT_THEME="phosphor"
            THEME_DISPLAY_NAME="Phosphor"
            ;;
        ice_cold|"ice cold")
            RED='\033[38;2;191;97;106m'
            GREEN='\033[38;2;136;192;208m'
            YELLOW='\033[38;2;235;203;139m'
            BLUE='\033[38;2;94;129;172m'
            MAGENTA='\033[38;2;0;229;255m'
            CYAN='\033[38;2;129;161;193m'
            CURRENT_THEME="ice_cold"
            THEME_DISPLAY_NAME="Ice Cold"
            ;;
        retro_terminal|unix_terminal|retro_unix|old_style_unix|unix_retro|white_on_black|retro|terminal|vt100|"old style unix terminal retro mode (white on black)"|"old style unix terminal retro mode"|"old style unix terminal"|"retro terminal")
            RED='\033[1;37m'
            GREEN='\033[1;37m'
            YELLOW='\033[1;37m'
            BLUE='\033[0;37m'
            MAGENTA='\033[1;37m'
            CYAN='\033[1;37m'
            WHITE='\033[1;37m'
            CURRENT_THEME="retro_terminal"
            THEME_DISPLAY_NAME="Retro Terminal"
            ;;
        retro_linux_memories|retro_linux|linux_memories|retro-linux-memories|retro-linux|linux|veracrypt|retrolinux|"retro linux memories"|"retro linux"|"retro linux memories [gtk / veracrypt dark slate]")
            RED='\033[38;2;217;83;79m'
            GREEN='\033[38;2;92;184;92m'
            YELLOW='\033[38;2;240;173;78m'
            BLUE='\033[38;2;21;83;158m'
            MAGENTA='\033[38;2;145;145;144m'
            CYAN='\033[38;2;91;134;187m'
            WHITE='\033[38;2;255;255;255m'
            CURRENT_THEME="retro_linux_memories"
            THEME_DISPLAY_NAME="Retro Linux Memories"
            ;;
        dracula)
            RED='\033[38;5;212m'
            GREEN='\033[38;5;84m'
            YELLOW='\033[38;5;228m'
            BLUE='\033[38;5;62m'
            MAGENTA='\033[38;5;141m'
            CYAN='\033[38;5;117m'
            CURRENT_THEME="dracula"
            THEME_DISPLAY_NAME="Dracula"
            ;;
        nord)
            RED='\033[38;5;167m'
            GREEN='\033[38;5;108m'
            YELLOW='\033[38;5;221m'
            BLUE='\033[38;5;110m'
            MAGENTA='\033[38;5;139m'
            CYAN='\033[38;5;117m'
            CURRENT_THEME="nord"
            THEME_DISPLAY_NAME="Nord"
            ;;
        matrix|"the matrix")
            RED='\033[38;5;124m'
            GREEN='\033[38;5;46m'
            YELLOW='\033[38;5;190m'
            BLUE='\033[38;5;29m'
            MAGENTA='\033[38;5;35m'
            CYAN='\033[38;5;120m'
            CURRENT_THEME="matrix"
            THEME_DISPLAY_NAME="The Matrix"
            ;;
        solarized|"solarized dark")
            RED='\033[38;5;166m'
            GREEN='\033[38;5;64m'
            YELLOW='\033[38;5;136m'
            BLUE='\033[38;5;33m'
            MAGENTA='\033[38;5;125m'
            CYAN='\033[38;5;37m'
            CURRENT_THEME="solarized"
            THEME_DISPLAY_NAME="Solarized"
            ;;
        tokyo|tokyo_night|tokyo-night|tokyonight|"tokyo night")
            RED='\033[38;5;203m'
            GREEN='\033[38;5;114m'
            YELLOW='\033[38;5;222m'
            BLUE='\033[38;5;111m'
            MAGENTA='\033[38;5;176m'
            CYAN='\033[38;5;73m'
            CURRENT_THEME="tokyo"
            THEME_DISPLAY_NAME="Tokyo Night"
            ;;
        monokai|"monokai pro")
            RED='\033[38;5;197m'
            GREEN='\033[38;5;148m'
            YELLOW='\033[38;5;220m'
            BLUE='\033[38;5;141m'
            MAGENTA='\033[38;5;208m'
            CYAN='\033[38;5;81m'
            CURRENT_THEME="monokai"
            THEME_DISPLAY_NAME="Monokai"
            ;;
        gruvbox|"gruvbox dark")
            RED='\033[38;5;167m'
            GREEN='\033[38;5;142m'
            YELLOW='\033[38;5;214m'
            BLUE='\033[38;5;109m'
            MAGENTA='\033[38;5;175m'
            CYAN='\033[38;5;108m'
            CURRENT_THEME="gruvbox"
            THEME_DISPLAY_NAME="Gruvbox"
            ;;
        emerald|emerald_isle|"emerald isle")
            RED='\033[38;5;204m'
            GREEN='\033[38;5;48m'
            YELLOW='\033[38;5;221m'
            BLUE='\033[38;5;31m'
            MAGENTA='\033[38;5;78m'
            CYAN='\033[38;5;86m'
            CURRENT_THEME="emerald"
            THEME_DISPLAY_NAME="Emerald Isle"
            ;;
        classic|"classic ansi"|monochrome|mono)
            RED='\033[0;31m'
            GREEN='\033[0;32m'
            YELLOW='\033[0;33m'
            BLUE='\033[0;34m'
            MAGENTA='\033[0;35m'
            CYAN='\033[0;36m'
            CURRENT_THEME="classic"
            THEME_DISPLAY_NAME="Classic ANSI"
            ;;
        cyberpunk|cyber)
            RED='\033[38;5;196m'
            GREEN='\033[38;5;48m'
            YELLOW='\033[38;5;226m'
            BLUE='\033[38;5;45m'
            MAGENTA='\033[38;5;198m'
            CYAN='\033[38;5;51m'
            CURRENT_THEME="cyberpunk"
            THEME_DISPLAY_NAME="Cyberpunk"
            ;;
        *)
            RED='\033[38;2;255;0;0m'
            GREEN='\033[38;2;0;255;0m'
            YELLOW='\033[38;2;255;153;51m'
            BLUE='\033[38;2;102;102;255m'
            MAGENTA='\033[38;2;166;77;255m'
            CYAN='\033[38;2;153;153;255m'
            WHITE='\033[38;2;216;180;254m'
            CURRENT_THEME="dreamworlds_ultra"
            THEME_DISPLAY_NAME="Dreamworlds Ultra"
            ;;
    esac
}

_get_theme_file_path() {
    if [ -n "${MP_THEME_FILE:-}" ]; then
        echo "$MP_THEME_FILE"
    else
        echo "$HOME/.config/mix-manager/theme"
    fi
}

_read_saved_theme_file() {
    local tf
    tf="$(_get_theme_file_path)"
    if [ -f "$tf" ]; then
        tr -d '\r\n' < "$tf" 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
    fi
}

_STARTUP_ENV_THEME="${MP_TRACKLIST_THEME:-${CURRENT_THEME:-${MIX_MANAGER_THEME:-}}}"
_STARTUP_FILE_THEME="$(_read_saved_theme_file)"
_LAST_SEEN_FILE_THEME="$_STARTUP_FILE_THEME"
_USER_CYCLED_THEME=""

load_tracklist_theme() {
    local file_theme
    file_theme="$(_read_saved_theme_file)"
    local chosen_theme="dreamworlds_ultra"

    if [ -n "$file_theme" ] && [ "$file_theme" != "$_LAST_SEEN_FILE_THEME" ]; then
        _LAST_SEEN_FILE_THEME="$file_theme"
        _USER_CYCLED_THEME=""
        CLI_THEME=""
        _STARTUP_ENV_THEME=""
        chosen_theme="$file_theme"
    elif [ -n "$_USER_CYCLED_THEME" ]; then
        chosen_theme="$_USER_CYCLED_THEME"
    elif [ -n "$CLI_THEME" ]; then
        chosen_theme="$CLI_THEME"
    elif [ -n "$file_theme" ] && [ "$file_theme" != "$_STARTUP_FILE_THEME" ]; then
        chosen_theme="$file_theme"
    elif [ -n "$_STARTUP_ENV_THEME" ]; then
        chosen_theme="$_STARTUP_ENV_THEME"
    elif [ -n "$file_theme" ]; then
        chosen_theme="$file_theme"
    fi

    set_theme_colors "$chosen_theme"
}

cycle_tracklist_theme() {
    local count="${#TRACKLIST_THEMES[@]}"
    local idx=0
    local i
    for ((i = 0; i < count; i++)); do
        if [ "${TRACKLIST_THEMES[$i]}" = "${CURRENT_THEME:-dreamworlds_ultra}" ]; then
            idx=$(( (i + 1) % count ))
            break
        fi
    done
    local next_theme="${TRACKLIST_THEMES[$idx]}"
    _USER_CYCLED_THEME="$next_theme"
    local tf
    tf="$(_get_theme_file_path)"
    mkdir -p "$(dirname "$tf")" 2>/dev/null || true
    printf '%s\n' "$next_theme" > "$tf" 2>/dev/null || true
    _LAST_SEEN_FILE_THEME="$next_theme"
    set_theme_colors "$next_theme"
}

load_tracklist_theme

SCRIPT_SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SCRIPT_SOURCE" ]; do
    SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_SOURCE")" && pwd)"
    SCRIPT_SOURCE="$(readlink "$SCRIPT_SOURCE")"
    [[ $SCRIPT_SOURCE != /* ]] && SCRIPT_SOURCE="$SCRIPT_DIR/$SCRIPT_SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_SOURCE")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
[ -f "$SCRIPT_DIR/MP_Audio_Player.py" ] && REPO_DIR="$SCRIPT_DIR"

# Exports always land in the MP Mix Manager folder the user is running from.
if [ "$(basename "$SCRIPT_DIR")" = "scripts" ]; then
    PROGRAM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
else
    PROGRAM_DIR="$SCRIPT_DIR"
fi
TRACKLIST_EXPORT_DIR="${MP_TRACKLIST_EXPORT_DIR:-TRACKLIST_EXPORTS}"
case "$TRACKLIST_EXPORT_DIR" in
    /*) TRACKLIST_EXPORT_PATH="$TRACKLIST_EXPORT_DIR" ;;
    *)  TRACKLIST_EXPORT_PATH="$PROGRAM_DIR/$TRACKLIST_EXPORT_DIR" ;;
esac

if [ -z "$TARGET_FILE" ] || [ ! -f "$TARGET_FILE" ]; then
    clear
    echo -e "${RED}Error: Tracklist file not provided or does not exist!${NC}"
    echo -e "${DIM}Usage: $0 <path-to-tracklist.txt> [path-to-audio-file]${NC}"
    echo ""
    read -r -p "Press [Enter] to exit..."
    exit 1
fi

HOST_DESC="Linux"
if [ -f /etc/os-release ]; then
    HOST_DESC=$(grep -E '^PRETTY_NAME=' /etc/os-release | cut -d= -f2 | tr -d '"')
elif [ "$(uname -s)" = "Darwin" ]; then
    HOST_DESC="macOS $(sw_vers -productVersion 2>/dev/null)"
elif [ "$(uname -s)" = "FreeBSD" ]; then
    HOST_DESC="FreeBSD $(uname -r)"
fi

TRACKLIST_VIEW_OFFSET=0
TRACKLIST_TOTAL_LINES=0
TRACKLIST_PAGE_ROWS=20
TRACKLIST_LAST_STATUS=""

# Mouse wheel scrolling. Shift+drag still selects text in the terminal.
_MOUSE_WHEEL_ON=0
_enable_mouse_wheel() {
    [ -t 0 ] && [ -t 1 ] || return 0
    printf '\033[?1000h\033[?1006h' 2>/dev/null || true
    _MOUSE_WHEEL_ON=1
}

_disable_mouse_wheel() {
    [ "$_MOUSE_WHEEL_ON" = "1" ] || return 0
    printf '\033[?1006l\033[?1000l' 2>/dev/null || true
    _MOUSE_WHEEL_ON=0
}

# Full-screen page: the viewer owns the terminal, so the first line is always line 1.
_enter_viewer_screen() {
    [ -t 1 ] || return 0
    printf '\033[?1049h\033[H\033[2J' 2>/dev/null || true
}

_leave_viewer_screen() {
    _disable_mouse_wheel
    [ -t 1 ] || return 0
    printf '\033[?1049l' 2>/dev/null || true
}

_cleanup_viewer() {
    _leave_viewer_screen
}

_VIEWER_NEEDS_REDRAW=0
_redraw_on_resize() {
    _VIEWER_NEEDS_REDRAW=1
}

trap '_cleanup_viewer' EXIT INT TERM
trap '_redraw_on_resize' WINCH

read_viewer_key() {
    local timeout="${1:-1}"
    VIEWER_KEY=""
    if ! read -r -s -n 1 -t "$timeout" VIEWER_KEY; then
        return 1
    fi
    [ "$VIEWER_KEY" = $'\e' ] || return 0

    # ESC begins a sequence: CSI (arrows, PgUp/PgDn, Home/End, SGR mouse) or X10 mouse.
    local next="" guard=0
    while [ "$guard" -lt 32 ] && read -r -s -n 1 -t 0.05 next; do
        VIEWER_KEY="$VIEWER_KEY$next"
        guard=$((guard + 1))
        case "$VIEWER_KEY" in
            $'\e[M'*)
                # X10 mouse report carries exactly three more bytes.
                while [ "${#VIEWER_KEY}" -lt 6 ] && read -r -s -n 1 -t 0.05 next; do
                    VIEWER_KEY="$VIEWER_KEY$next"
                done
                break
                ;;
            $'\e['*M|$'\e['*m|$'\e['*'~'|$'\e['*[A-Za-z])
                break
                ;;
            $'\e'*[A-Za-z])
                break
                ;;
        esac
    done
    return 0
}

_terminal_rows() {
    local rows=""
    if command -v tput >/dev/null 2>&1; then
        rows=$(tput lines 2>/dev/null || true)
    fi
    if ! [[ "$rows" =~ ^[0-9]+$ ]] || [ "$rows" -lt 10 ]; then
        rows="${LINES:-0}"
    fi
    if ! [[ "$rows" =~ ^[0-9]+$ ]] || [ "$rows" -lt 10 ]; then
        rows=35
    fi
    echo "$rows"
}

_terminal_columns() {
    local cols=""
    if command -v tput >/dev/null 2>&1; then
        cols=$(tput cols 2>/dev/null || true)
    fi
    if ! [[ "$cols" =~ ^[0-9]+$ ]] || [ "$cols" -lt 40 ]; then
        cols="${COLUMNS:-0}"
    fi
    if ! [[ "$cols" =~ ^[0-9]+$ ]] || [ "$cols" -lt 40 ]; then
        cols=100
    fi
    echo "$cols"
}

# Terminal cells used by one character, stored in _CHAR_CELLS. Emoji and CJK
# glyphs occupy two columns, combining marks and variation selectors occupy
# none, and everything else occupies one. Centering and clipping have to use
# these widths instead of the raw character count, or emoji-heavy lines drift
# off to one side. This is a plain variable assignment (no subshell) because it
# runs once per character of every drawn line.
_CHAR_CELLS=1
_char_cells() {
    local cp
    if ! printf -v cp '%d' "'$1" 2>/dev/null || [ -z "$cp" ]; then
        _CHAR_CELLS=1
        return
    fi
    if { [ "$cp" -ge 768 ] && [ "$cp" -le 879 ]; } ||
       [ "$cp" -eq 8205 ] || [ "$cp" -eq 65038 ] || [ "$cp" -eq 65039 ]; then
        _CHAR_CELLS=0
    elif { [ "$cp" -ge 4352 ] && [ "$cp" -le 4447 ]; } ||
         { [ "$cp" -ge 11904 ] && [ "$cp" -le 13311 ]; } ||
         { [ "$cp" -ge 13312 ] && [ "$cp" -le 19903 ]; } ||
         { [ "$cp" -ge 19968 ] && [ "$cp" -le 40959 ]; } ||
         { [ "$cp" -ge 40960 ] && [ "$cp" -le 42191 ]; } ||
         { [ "$cp" -ge 44032 ] && [ "$cp" -le 55203 ]; } ||
         { [ "$cp" -ge 63744 ] && [ "$cp" -le 64255 ]; } ||
         { [ "$cp" -ge 65040 ] && [ "$cp" -le 65071 ]; } ||
         { [ "$cp" -ge 65280 ] && [ "$cp" -le 65376 ]; } ||
         { [ "$cp" -ge 65504 ] && [ "$cp" -le 65510 ]; } ||
         { [ "$cp" -ge 126976 ] && [ "$cp" -le 129791 ]; } ||
         { [ "$cp" -ge 129792 ] && [ "$cp" -le 130047 ]; } ||
         { [ "$cp" -ge 130048 ] && [ "$cp" -le 131071 ]; } ||
         { [ "$cp" -ge 131072 ] && [ "$cp" -le 131647 ]; } ||
         { [ "$cp" -ge 131648 ] && [ "$cp" -le 131703 ]; }; then
        _CHAR_CELLS=2
    else
        _CHAR_CELLS=1
    fi
}

_display_width() {
    local text="$1" width=0 i
    for ((i = 0; i < ${#text}; i++)); do
        _char_cells "${text:i:1}"
        width=$((width + _CHAR_CELLS))
    done
    printf '%d' "$width"
}

# Cut a plain string to a cell budget, marking a cut with an ellipsis.
_clip_display() {
    local text="$1" limit="$2" ellipsis="$3"
    local out="" width=0 i
    if [ "$(_display_width "$text")" -le "$limit" ]; then
        printf '%s' "$text"
        return 0
    fi
    for ((i = 0; i < ${#text}; i++)); do
        _char_cells "${text:i:1}"
        if [ $((width + _CHAR_CELLS)) -gt $((limit - 1)) ]; then
            break
        fi
        out+="${text:i:1}"
        width=$((width + _CHAR_CELLS))
    done
    printf '%s%s' "$out" "$ellipsis"
}

# Matches one ANSI escape sequence (colours, cursor moves, resets).
_ANSI_SEQ_RE=$'^\e\\[[0-9;?]*[A-Za-z]'

# Cut styled text (ANSI escapes included) to a cell budget without breaking the
# escape sequences, so a coloured status line can never spill outside the box.
_clip_ansi() {
    local text="$1" limit="$2"
    local out="" rest="$text" width=0
    while [ -n "$rest" ]; do
        # Copy whole escape sequences without measuring them as text.
        if [[ "$rest" =~ $_ANSI_SEQ_RE ]]; then
            out+="${BASH_REMATCH[0]}"
            rest="${rest:${#BASH_REMATCH[0]}}"
            continue
        fi
        _char_cells "${rest:0:1}"
        if [ $((width + _CHAR_CELLS)) -gt "$limit" ]; then
            out+="${NC}…"
            break
        fi
        out+="${rest:0:1}"
        width=$((width + _CHAR_CELLS))
        rest="${rest:1}"
    done
    printf '%s' "$out"
}

_tracklist_line_count() {
    local count
    count=$(wc -l < "$TARGET_FILE" 2>/dev/null || echo 0)
    count="${count//[^0-9]/}"
    [ -z "$count" ] && count=0
    # A file without a trailing newline still holds one more line.
    if [ -s "$TARGET_FILE" ] && [ -n "$(tail -c 1 "$TARGET_FILE" 2>/dev/null)" ]; then
        count=$((count + 1))
    fi
    echo "$count"
}

clamp_view_offset() {
    local max_offset=$((TRACKLIST_TOTAL_LINES - TRACKLIST_PAGE_ROWS))
    [ "$max_offset" -lt 0 ] && max_offset=0
    if [ "$TRACKLIST_VIEW_OFFSET" -gt "$max_offset" ]; then
        TRACKLIST_VIEW_OFFSET="$max_offset"
    fi
    if [ "$TRACKLIST_VIEW_OFFSET" -lt 0 ]; then
        TRACKLIST_VIEW_OFFSET=0
    fi
}

scroll_view() {
    local delta="$1"
    TRACKLIST_VIEW_OFFSET=$((TRACKLIST_VIEW_OFFSET + delta))
    clamp_view_offset
    render_tracklist
}

export_tracklist_file() {
    local fmt="$1"
    local exporter="$PROGRAM_DIR/scripts/tracklist_export.py"
    [ ! -f "$exporter" ] && exporter="$PROGRAM_DIR/tracklist_export.py"
    local out_dir="$TRACKLIST_EXPORT_PATH"
    mkdir -p "$out_dir" 2>/dev/null || true

    if command -v python3 >/dev/null 2>&1 && [ -f "$exporter" ]; then
        local saved
        saved=$(python3 "$exporter" "$TARGET_FILE" --format "$fmt" --base-dir "$PROGRAM_DIR" --output-dir "$out_dir" 2>/dev/null)
        if [ -n "$saved" ] && [ -f "$saved" ]; then
            echo "$saved"
            return 0
        fi
    fi

    # Fallback: save the tracklist as-is so the export never silently fails.
    local base dest
    base="$(basename "$TARGET_FILE")"
    base="${base%.*}"
    if [ "$fmt" = "text" ]; then
        dest="$out_dir/${base}.txt"
    else
        dest="$out_dir/${base}.html"
    fi
    mkdir -p "$out_dir" 2>/dev/null || return 1
    if [ "$fmt" = "text" ]; then
        cp -f "$TARGET_FILE" "$dest" 2>/dev/null || return 1
    else
        {
            printf '%s\n' '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            printf '<title>%s • MP Mix Manager Tracklist</title>\n' "$base"
            printf '%s\n' '<style>body{background:#04060f;color:#e8f6ff;font-family:monospace;padding:2rem;}pre{white-space:pre-wrap;}</style>'
            printf '</head><body><h1>%s</h1><pre>\n' "$base"
            sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' "$TARGET_FILE"
            printf '%s\n' '</pre></body></html>'
        } > "$dest" 2>/dev/null || return 1
    fi
    echo "$dest"
    return 0
}

copy_tracklist_clipboard() {
    local text
    text=$(<"$TARGET_FILE")
    for cmd in wl-copy xclip pbcopy clip.exe; do
        if command -v "$cmd" >/dev/null 2>&1; then
            printf "%s" "$text" | "$cmd" 2>/dev/null
            return 0
        fi
    done
    return 1
}

edit_tracklist_vi() {
    if command -v vi >/dev/null 2>&1; then
        vi "$TARGET_FILE"
        return $?
    elif command -v vim >/dev/null 2>&1; then
        vim "$TARGET_FILE"
        return $?
    elif command -v nvim >/dev/null 2>&1; then
        nvim "$TARGET_FILE"
        return $?
    elif [ -n "${EDITOR:-}" ] && command -v "$EDITOR" >/dev/null 2>&1; then
        "$EDITOR" "$TARGET_FILE"
        return $?
    elif command -v nano >/dev/null 2>&1; then
        nano "$TARGET_FILE"
        return $?
    fi
    return 1
}

open_spek_image() {
    local img="$1"
    [ -z "$img" ] || [ ! -f "$img" ] && return 1
    local os_name
    os_name="$(uname -s)"
    if [ "$os_name" = "Darwin" ]; then
        open -a Preview "$img" >/dev/null 2>&1 &
        return 0
    fi
    for viewer in gwenview loupe eog feh xdg-open; do
        if command -v "$viewer" >/dev/null 2>&1; then
            "$viewer" "$img" >/dev/null 2>&1 &
            return 0
        fi
    done
    return 1
}

_load_archive_config() {
    if [ -z "${MIX_ARCHIVE_DIR:-}" ]; then
        local cfg
        for cfg in "$REPO_DIR/config.env" "$HOME/.config/mix-manager/config.env"; do
            if [ -f "$cfg" ]; then
                local val
                val=$(grep -E '^[[:space:]]*MIX_ARCHIVE_DIR=' "$cfg" 2>/dev/null | tail -n 1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']//' -e 's/["'"'"']$//')
                if [ -n "$val" ] && [ -d "$val" ]; then
                    MIX_ARCHIVE_DIR="$val"
                    break
                fi
            fi
        done
    fi
}

resolve_mix_audio_file() {
    if [ -n "$AUDIO_FILE" ] && [ -f "$AUDIO_FILE" ]; then
        echo "$AUDIO_FILE"
        return 0
    fi

    _load_archive_config

    local hdr_mix hdr_dir
    hdr_mix=$(grep -E '^Mix File:[[:space:]]+' "$TARGET_FILE" 2>/dev/null | head -n 1 | sed -E 's/^Mix File:[[:space:]]+//')
    hdr_dir=$(grep -E '^Directory:[[:space:]]+' "$TARGET_FILE" 2>/dev/null | head -n 1 | sed -E 's/^Directory:[[:space:]]+//')
    if [ -n "$hdr_mix" ] && [ -n "$hdr_dir" ] && [ -f "$hdr_dir/$hdr_mix" ]; then
        echo "$hdr_dir/$hdr_mix"
        return 0
    fi

    local tl_dir tl_base tl_stem
    tl_dir="$(cd "$(dirname "$TARGET_FILE")" 2>/dev/null && pwd || dirname "$TARGET_FILE")"
    tl_base="$(basename "$TARGET_FILE")"
    tl_stem="${tl_base%.*}"
    tl_stem="${tl_stem#Tracklist_}"

    local -a search_dirs=(
        "$tl_dir"
        "$(dirname "$tl_dir")"
        "$tl_dir/FLAC_CONVERTED_OUTPUTS"
        "$(dirname "$tl_dir")/FLAC_CONVERTED_OUTPUTS"
        "$(dirname "$tl_dir")/CONVERTED_WAV_FILES"
    )
    if [ -n "${MIX_ARCHIVE_DIR:-}" ]; then
        search_dirs+=(
            "${MIX_ARCHIVE_DIR%/}/FLAC_CONVERTED_OUTPUTS"
            "${MIX_ARCHIVE_DIR%/}/CONVERTED_WAV_FILES"
            "${MIX_ARCHIVE_DIR%/}"
        )
    fi
    search_dirs+=(
        "$REPO_DIR/MIX_ARCHIVE/FLAC_CONVERTED_OUTPUTS"
        "$REPO_DIR/FLAC_CONVERTED_OUTPUTS"
        "$REPO_DIR"
    )

    local d ext
    for d in "${search_dirs[@]}"; do
        [ -d "$d" ] || continue
        for ext in flac wav mp3 ogg m4a aiff FLAC WAV MP3; do
            if [ -f "$d/${tl_stem}.${ext}" ]; then
                echo "$d/${tl_stem}.${ext}"
                return 0
            fi
        done
    done

    local state_file="$HOME/.config/mix-manager/mp_player_state.json"
    if [ -f "$state_file" ]; then
        local st_path
        st_path=$(grep -o '"path":[[:space:]]*"[^"]*"' "$state_file" 2>/dev/null | head -n 1 | cut -d'"' -f4)
        if [ -n "$st_path" ] && [ -f "$st_path" ]; then
            echo "$st_path"
            return 0
        fi
    fi

    return 1
}

find_existing_spek_file() {
    local audio_ref="${1:-}"
    _load_archive_config

    local tl_base tl_stem clean_tl_stem
    tl_base="$(basename "$TARGET_FILE")"
    tl_stem="${tl_base%.*}"
    tl_stem="${tl_stem#Tracklist_}"
    clean_tl_stem=$(printf '%s' "$tl_stem" | sed 's/[\/\\:*?"<>|]/_/g')

    local -a stems=()
    local s
    if [ -n "$audio_ref" ]; then
        local aud_base aud_stem clean_aud_stem
        aud_base="$(basename "$audio_ref")"
        aud_stem="${aud_base%.*}"
        clean_aud_stem=$(printf '%s' "$aud_stem" | sed 's/[\/\\:*?"<>|]/_/g')
        stems+=("$clean_aud_stem" "$aud_stem")
    fi
    stems+=("$clean_tl_stem" "$tl_stem")

    local tl_dir
    tl_dir="$(cd "$(dirname "$TARGET_FILE")" 2>/dev/null && pwd || dirname "$TARGET_FILE")"
    local -a spek_dirs=()
    if [ -n "$audio_ref" ]; then
        local aud_dir
        aud_dir="$(cd "$(dirname "$audio_ref")" 2>/dev/null && pwd || dirname "$audio_ref")"
        spek_dirs+=("$aud_dir/SPEK_OUTPUTS" "$aud_dir" "$(dirname "$aud_dir")/SPEK_OUTPUTS")
    fi
    spek_dirs+=("$tl_dir/SPEK_OUTPUTS" "$tl_dir" "$(dirname "$tl_dir")/SPEK_OUTPUTS")
    [ -n "${SPEK_DIR:-}" ] && spek_dirs+=("$SPEK_DIR")
    if [ -n "${MIX_ARCHIVE_DIR:-}" ]; then
        spek_dirs+=("${MIX_ARCHIVE_DIR%/}/SPEK_OUTPUTS")
    fi
    spek_dirs+=("$REPO_DIR/SPEK_OUTPUTS" "$HOME/.config/mix-manager/SPEK_OUTPUTS")

    local d cand
    for d in "${spek_dirs[@]}"; do
        [ -n "$d" ] && [ -d "$d" ] || continue
        for s in "${stems[@]}"; do
            [ -n "$s" ] || continue
            for cand in "$d/${s}_spectrogram.png" "$d/${s}.spek.png" "$d/${s}.spek" "$d/${s}_spectrogram.jpg"; do
                if [ -s "$cand" ]; then
                    echo "$cand"
                    return 0
                fi
            done
        done
    done
    return 1
}

generate_or_open_spek() {
    local audio_target existing_spek
    audio_target="$(resolve_mix_audio_file 2>/dev/null || true)"
    existing_spek="$(find_existing_spek_file "$audio_target" 2>/dev/null || true)"

    if [ -n "$existing_spek" ] && [ -s "$existing_spek" ]; then
        open_spek_image "$existing_spek"
        render_tracklist
        echo -e "\n  ${BOLD}${GREEN}✓ Spek already exists! Opened: $(basename "$existing_spek")${NC}"
        return 0
    fi

    if [ -z "$audio_target" ] || [ ! -f "$audio_target" ]; then
        render_tracklist
        echo -e "\n  ${BOLD}${RED}✗ Audio file not found for tracklist to generate Spek.${NC}"
        return 1
    fi

    echo -e "\n  ${BOLD}${YELLOW}⚡ Generating Spek spectrogram for $(basename "$audio_target")...${NC}"

    local generated_spek=""
    if [ -f "$REPO_DIR/MP_Audio_Player.py" ] && command -v python3 >/dev/null 2>&1; then
        generated_spek=$(python3 - "$REPO_DIR/MP_Audio_Player.py" "$audio_target" << 'PY' 2>/dev/null || true
import importlib.util, sys
spec = importlib.util.spec_from_file_location("mp_player", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
ok, res = mod.generate_track_spek(sys.argv[2])
if ok:
    print(res)
PY
)
    fi

    if [ -z "$generated_spek" ] || [ ! -s "$generated_spek" ]; then
        if command -v ffmpeg >/dev/null 2>&1; then
            _load_archive_config
            local out_dir="$REPO_DIR/SPEK_OUTPUTS"
            [ -n "${MIX_ARCHIVE_DIR:-}" ] && [ -d "${MIX_ARCHIVE_DIR}" ] && out_dir="${MIX_ARCHIVE_DIR%/}/SPEK_OUTPUTS"
            mkdir -p "$out_dir" 2>/dev/null || out_dir="$(dirname "$audio_target")"
            local aud_base aud_stem clean_stem
            aud_base="$(basename "$audio_target")"
            aud_stem="${aud_base%.*}"
            clean_stem=$(printf '%s' "$aud_stem" | sed 's/[\/\\:*?"<>|]/_/g')
            local target_png="$out_dir/${clean_stem}_spectrogram.png"
            if ffmpeg -y -i "$audio_target" -lavfi "showspectrumpic=s=1920x1080:mode=combined:color=intensity:scale=log" -update 1 -frames:v 1 "$target_png" >/dev/null 2>&1 && [ -s "$target_png" ]; then
                generated_spek="$target_png"
            fi
        fi
    fi

    if [ -n "$generated_spek" ] && [ -s "$generated_spek" ]; then
        open_spek_image "$generated_spek"
        render_tracklist
        echo -e "\n  ${BOLD}${GREEN}✓ Spek generated and opened: $(basename "$generated_spek")${NC}"
        return 0
    else
        render_tracklist
        echo -e "\n  ${BOLD}${RED}✗ Failed to generate Spek spectrogram.${NC}"
        return 1
    fi
}

extract_and_copy_mix_audio() {
    local audio_target
    audio_target="$(resolve_mix_audio_file 2>/dev/null || true)"

    if [ -z "$audio_target" ] || [ ! -f "$audio_target" ]; then
        render_tracklist
        echo -e "\n  ${BOLD}${RED}✗ Mix audio file not found for this tracklist.${NC}"
        return 1
    fi

    echo ""
    echo -e "  ${BOLD}${CYAN}📂 Mix Audio File:${NC} ${GREEN}$(basename "$audio_target")${NC}"
    echo -ne "  ${BOLD}${YELLOW}Enter destination path to extract and copy mix audio file to:${NC} "
    local dest_path=""
    if [ -t 0 ]; then
        read -e -r dest_path
    else
        read -r dest_path
    fi

    dest_path="$(printf '%s' "$dest_path" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']//' -e 's/["'"'"']$//')"
    if [ -z "$dest_path" ]; then
        render_tracklist
        echo -e "\n  ${YELLOW}Notice: Copy cancelled (no destination path entered).${NC}"
        return 1
    fi

    dest_path="${dest_path/#\~/$HOME}"

    local target_dest=""
    if [ -d "$dest_path" ] || [[ "$dest_path" == */ ]]; then
        mkdir -p "$dest_path" 2>/dev/null || {
            render_tracklist
            echo -e "\n  ${BOLD}${RED}✗ Failed to create destination directory: ${dest_path}${NC}"
            return 1
        }
        target_dest="${dest_path%/}/$(basename "$audio_target")"
    else
        local parent_dir
        parent_dir="$(dirname "$dest_path")"
        if [ -d "$parent_dir" ] && [[ "$(basename "$dest_path")" == *.* ]]; then
            target_dest="$dest_path"
        else
            mkdir -p "$dest_path" 2>/dev/null || {
                render_tracklist
                echo -e "\n  ${BOLD}${RED}✗ Failed to create destination directory: ${dest_path}${NC}"
                return 1
            }
            target_dest="${dest_path%/}/$(basename "$audio_target")"
        fi
    fi

    echo -e "  ${BOLD}${YELLOW}⚡ Copying $(basename "$audio_target") to ${target_dest}...${NC}"
    if cp -f "$audio_target" "$target_dest" 2>/dev/null; then
        render_tracklist
        echo -e "\n  ${BOLD}${GREEN}✓ Mix audio file extracted and copied to: ${target_dest}${NC}"
        return 0
    else
        render_tracklist
        echo -e "\n  ${BOLD}${RED}✗ Failed to copy mix audio file to: ${target_dest}${NC}"
        return 1
    fi
}

render_tracklist() {
    load_tracklist_theme
    TRACKLIST_TOTAL_LINES="$(_tracklist_line_count)"

    local term_rows term_cols
    term_rows="$(_terminal_rows)"
    term_cols="$(_terminal_columns)"
    [ "$term_cols" -gt 160 ] && term_cols=160

    # The window frame and its rounded corners sit on top of the terminal text
    # area, so every line is drawn inside a content box with a margin on both
    # sides. Nothing is ever written into that margin, so text cannot spill
    # over the window border.
    local pad_x=3
    [ "$term_cols" -lt 52 ] && pad_x=2
    [ "$term_cols" -lt 30 ] && pad_x=1
    local content_cols=$((term_cols - pad_x * 2))
    [ "$content_cols" -lt 20 ] && content_cols=20
    local indent
    printf -v indent '%*s' "$pad_x" ''

    # Row budget: 8 header rows (three title/rule rows, four info rows and the
    # rule under them), the body, the rule under it, the viewport indicator,
    # the help rows, the closing rule and the status row.
    local help_rows=4
    [ "$term_rows" -lt 24 ] && help_rows=2
    [ "$term_rows" -lt 17 ] && help_rows=0
    local chrome=$((8 + 1 + 1 + help_rows + 1 + 1))
    TRACKLIST_PAGE_ROWS=$((term_rows - chrome))
    [ "$TRACKLIST_PAGE_ROWS" -lt 2 ] && TRACKLIST_PAGE_ROWS=2
    [ "$TRACKLIST_PAGE_ROWS" -gt 200 ] && TRACKLIST_PAGE_ROWS=200
    clamp_view_offset

    # Every line is clipped to the content box so it can never wrap, spill over
    # the window frame or push the first tracklist line out of the window.
    local piped=0
    [ -t 1 ] || piped=1

    # Piped output is not shown in a window, so it is not clipped.
    local box_limit="$content_cols"
    [ "$piped" = "1" ] && box_limit=100000

    # A rule spanning exactly the content box, never the frame.
    local rule
    if [ "$piped" = "1" ]; then
        rule=$(printf '%*s' "$term_cols" '' | tr ' ' '-')
    else
        rule=$(printf '%*s' "$content_cols" '' | tr ' ' '-')
    fi

    # Text inside the body is indented two cells inside the content box.
    local body_indent="${indent}  "
    local body_limit=$((box_limit - 2))
    [ "$body_limit" -lt 10 ] && body_limit=10

    footer_fit() {
        local text="$1"
        local limit="$body_limit" out="" rest
        text="$(_clip_display "$text" "$limit" "…")"
        rest="$text"
        while [[ "$rest" =~ ^([^\[]*)(\[[^]]*\]) ]]; do
            out+="${BASH_REMATCH[1]}${CYAN}${BASH_REMATCH[2]}${NC}"
            rest="${rest:${#BASH_REMATCH[0]}}"
        done
        out+="$rest"
        printf '%b%b\n' "$body_indent" "$out"
    }

    # The title is centred on the content box using real terminal cell widths,
    # so the emoji and the bullet do not shift it off centre.
    local title_full="🎶 STREAM OF FREQUENCY • DEDICATED CONSOLE TRACKLIST VIEWER 🎶"
    local title_mid="🎶 STREAM OF FREQUENCY • TRACKLIST VIEWER 🎶"
    local title_short="🎶 TRACKLIST VIEWER 🎶"
    local title_line
    if [ "$(_display_width "$title_full")" -le "$box_limit" ]; then
        title_line="$title_full"
    elif [ "$(_display_width "$title_mid")" -le "$box_limit" ]; then
        title_line="$title_mid"
    else
        title_line="$(_clip_display "$title_short" "$box_limit" "🎶")"
    fi
    local title_width=$(_display_width "$title_line")
    local title_left=$((pad_x + (content_cols - title_width) / 2))
    [ "$title_left" -lt "$pad_x" ] && title_left="$pad_x"
    local title_indent
    printf -v title_indent '%*s' "$title_left" ''

    # "  • Label:  value (dim suffix)" with the label in a fixed field so the
    # four header rows line up, and every value clipped to what is left.
    local label_field=12
    header_row() {
        local label="$1" color="$2" value="$3" suffix="$4"
        local avail=$((box_limit - 4 - label_field))
        [ "$avail" -lt 10 ] && avail=10
        local vcells scells pad
        vcells="$(_display_width "$value")"
        scells="$(_display_width "$suffix")"
        # Keep the value readable: only trim the dim suffix to make room for a
        # value that already fits on its own, otherwise drop the suffix.
        if [ "$vcells" -gt "$avail" ]; then
            value="$(_clip_display "$value" "$avail" "…")"
            suffix=""
        elif [ $((vcells + scells)) -gt "$avail" ]; then
            suffix="$(_clip_display "$suffix" $((avail - vcells)) "…")"
        fi
        local pad_width=$((label_field - ${#label}))
        [ "$pad_width" -lt 1 ] && pad_width=1
        printf -v pad '%*s' "$pad_width" ''
        printf '%s  • %b%s%b%b%s%b%b%s%b\n' \
            "$indent" "$BOLD" "$label$pad" "$NC" \
            "$color" "$value" "$NC" "$DIM" "$suffix" "$NC"
    }

    clear
    local filename=$(basename "$TARGET_FILE")
    local filesize=$(ls -lh "$TARGET_FILE" 2>/dev/null | awk '{print $5}')
    local first_line=$((TRACKLIST_VIEW_OFFSET + 1))
    local last_line=$((TRACKLIST_VIEW_OFFSET + TRACKLIST_PAGE_ROWS))
    [ "$last_line" -gt "$TRACKLIST_TOTAL_LINES" ] && last_line="$TRACKLIST_TOTAL_LINES"
    [ "$last_line" -lt "$first_line" ] && last_line="$first_line"

    echo -e "${indent}${BOLD}${MAGENTA}${rule}${NC}"
    echo -e "${title_indent}${BOLD}${CYAN}${title_line}${NC}"
    echo -e "${indent}${BOLD}${MAGENTA}${rule}${NC}"
    header_row "File:" "${GREEN}" "${filename} (${filesize}, ${TRACKLIST_TOTAL_LINES} lines)" ""
    header_row "Location:" "${DIM}" "$(dirname "$TARGET_FILE")" ""
    header_row "Platform:" "${YELLOW}" "${HOST_DESC}" " (Borderless Console Mode)"
    header_row "Theme:" "${MAGENTA}" "${THEME_DISPLAY_NAME}" " (${CURRENT_THEME})"
    echo -e "${indent}${BOLD}${MAGENTA}${rule}${NC}"

    # One body row: indent, the text, and nothing beyond the content box.
    body_line() {
        local text="$1" color="$2"
        printf '%b%b%s%b\n' "$body_indent" "$color" \
            "$(_clip_display "$text" "$body_limit" "…")" "$NC"
    }

    # Track rows keep their own colouring but are clipped as a whole, so the
    # remix in parentheses can never poke out of the right edge.
    track_row() {
        local num="$1" rest="$2"
        local num_str out
        printf -v num_str '%02d.' "$((10#$num))"
        local avail=$((body_limit - ${#num_str} - 1))
        [ "$avail" -lt 5 ] && avail=5
        rest="$(_clip_display "$rest" "$avail" "…")"
        if [[ "$rest" =~ ^([^-]+)[[:space:]]-[[:space:]](.+)$ ]]; then
            local artist="${BASH_REMATCH[1]}"
            local title="${BASH_REMATCH[2]}"
            if [[ "$title" =~ (.*)(\([^\)]+\))(.*) ]]; then
                local tmain="${BASH_REMATCH[1]}"
                local remix="${BASH_REMATCH[2]}"
                local tend="${BASH_REMATCH[3]}"
                out="${body_indent}${BOLD}${CYAN}${num_str}${NC} ${WHITE}${artist}${NC} - ${GREEN}${tmain}${NC}${YELLOW}${remix}${NC}${tend}"
            else
                out="${body_indent}${BOLD}${CYAN}${num_str}${NC} ${WHITE}${artist}${NC} - ${GREEN}${title}${NC}"
            fi
        else
            out="${body_indent}${BOLD}${CYAN}${num_str}${NC} ${WHITE}${rest}${NC}"
        fi
        printf '%b\n' "$out"
    }

    # Only the visible window of the file is drawn, so line 1 can never scroll away.
    local index=0
    local drawn=0
    local offset="$TRACKLIST_VIEW_OFFSET"
    while IFS= read -r line || [ -n "$line" ]; do
        if [ "$index" -lt "$offset" ]; then
            index=$((index + 1))
            continue
        fi
        if [ "$drawn" -ge "$TRACKLIST_PAGE_ROWS" ]; then
            break
        fi
        index=$((index + 1))
        drawn=$((drawn + 1))

        if [[ "$line" =~ ^={10,}$ || "$line" =~ ^-{10,}$ ]]; then
            printf '%b%b%s%b\n' "$body_indent" "$DIM" \
                "$(_clip_display "$line" "$body_limit" "")" "$NC"
        elif [[ "$line" =~ (RELEASE\ INFO|TRACKLIST|SOURCE\ MANIFEST|SOURCE\ FILES) ]]; then
            body_line "$line" "$BOLD$YELLOW"
        elif [[ "$line" =~ ^([A-Za-z0-9\ ]+):([[:space:]]+)(.*)$ ]]; then
            local key="${BASH_REMATCH[1]}"
            local val="${BASH_REMATCH[3]}"
            local key_limit=$((body_limit - ${#key} - 3))
            if [ "$key_limit" -lt 5 ]; then key_limit=5; fi
            val="$(_clip_display "$val" "$key_limit" "…")"
            printf '%b%b%s:%b %b%s%b\n' "$body_indent" "$BOLD$BLUE" "$key" "$NC" "$WHITE" "$val" "$NC"
        elif [[ "$line" =~ ^([0-9]{1,3})[\.\)][[:space:]]+(.*)$ ]]; then
            track_row "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
        elif [ -z "$line" ]; then
            printf '\n'
        else
            body_line "$line" "$WHITE"
        fi
    done < "$TARGET_FILE"

    local padding=$((TRACKLIST_PAGE_ROWS - drawn))
    local p
    for ((p = 0; p < padding; p++)); do printf '\n'; done

    echo -e "${indent}${BOLD}${MAGENTA}${rule}${NC}"
    if [ "$TRACKLIST_VIEW_OFFSET" -gt 0 ] || [ "$last_line" -lt "$TRACKLIST_TOTAL_LINES" ]; then
        footer_fit "Lines ${first_line}-${last_line} of ${TRACKLIST_TOTAL_LINES} • ${TRACKLIST_PAGE_ROWS} rows/page"
    else
        footer_fit "Lines 1-${last_line} of ${TRACKLIST_TOTAL_LINES} • all visible"
    fi
    if [ "$help_rows" -ge 2 ]; then
        footer_fit "[↑/↓] Scroll  [PgUp/PgDn] Page  [Home/End] Jump  [Wheel] Scroll  [R] Reload"
        footer_fit "[Q] Close Window  [C] Copy to Clipboard  [S] Full Scroll Mode (less)  [T] Theme"
    fi
    if [ "$help_rows" -ge 4 ]; then
        footer_fit "[E] Edit Tracklist in VI  [G] Generate & Open Spek  [D] Extract & Copy Mix Audio"
        # The path is only shown when the whole hint fits, so a narrow window
        # gets "saved to TRACKLIST_EXPORTS/" instead of a half-cut directory.
        if [ "$(_display_width "[X] Export HTML  [Y] Export TEXT  (saved to ${TRACKLIST_EXPORT_PATH}/)")" -le "$body_limit" ]; then
            footer_fit "[X] Export HTML  [Y] Export TEXT  (saved to ${TRACKLIST_EXPORT_PATH}/)"
        elif [ "$(_display_width "[X] Export HTML  [Y] Export TEXT")" -le "$body_limit" ]; then
            footer_fit "[X] Export HTML  [Y] Export TEXT"
        else
            footer_fit "[X] HTML  [Y] TEXT"
        fi
    fi
    echo -e "${indent}${BOLD}${MAGENTA}${rule}${NC}"
    # The status row is the last row of the frame. In a terminal it is written
    # without a trailing newline: drawing exactly one row per terminal row and
    # then emitting a newline scrolls the frame up by one, which is what used to
    # cut the top rule off the window.
    local status_row=""
    if [ -n "$TRACKLIST_LAST_STATUS" ]; then
        status_row="${body_indent}$(_clip_ansi "$TRACKLIST_LAST_STATUS" "$body_limit")${NC}"
    fi
    if [ "$piped" = "1" ]; then
        printf '%b\n' "$status_row"
    else
        printf '%b' "$status_row"
    fi
}

if [ -t 1 ]; then
    _enter_viewer_screen
fi

# --export html|text writes the file and exits without opening the viewer.
if [ -n "$CLI_EXPORT" ]; then
    if saved_path=$(export_tracklist_file "$CLI_EXPORT"); then
        echo "✔ Tracklist exported (${CLI_EXPORT}): ${saved_path}"
        exit 0
    fi
    echo "✖ Tracklist export failed (${CLI_EXPORT})." >&2
    exit 1
fi

_enable_mouse_wheel
render_tracklist

while true; do
    if [ "$_VIEWER_NEEDS_REDRAW" = "1" ]; then
        _VIEWER_NEEDS_REDRAW=0
        render_tracklist
    fi
    if [ -t 0 ]; then
        if ! read_viewer_key 1; then
            _cur_file_th="$(_read_saved_theme_file)"
            if [ -n "$_cur_file_th" ] && [ "$_cur_file_th" != "$_LAST_SEEN_FILE_THEME" ]; then
                render_tracklist
            fi
            continue
        fi
        key="$VIEWER_KEY"
    elif ! read -r -s -n 1 key; then
        sleep 3600
        exit 0
    fi

    # Mouse wheel reports: SGR (ESC [ < b ; x ; y M) and X10 (ESC [ M cb cx cy).
    case "$key" in
        $'\e[<64;'*|$'\e[M\x60'*)
            TRACKLIST_LAST_STATUS=""
            scroll_view -3
            continue
            ;;
        $'\e[<65;'*|$'\e[M\x61'*)
            TRACKLIST_LAST_STATUS=""
            scroll_view 3
            continue
            ;;
        $'\e[<'*|$'\e[M'*)
            continue
            ;;
    esac

    case "$key" in
        [qQ])
            exit 0
            ;;
        $'\e')
            exit 0
            ;;
        $'\e[A'|$'\eOA')
            TRACKLIST_LAST_STATUS=""
            scroll_view -1
            ;;
        $'\e[B'|$'\eOB')
            TRACKLIST_LAST_STATUS=""
            scroll_view 1
            ;;
        $'\e[5~')
            TRACKLIST_LAST_STATUS=""
            scroll_view -"$TRACKLIST_PAGE_ROWS"
            ;;
        $'\e[6~'|" ")
            TRACKLIST_LAST_STATUS=""
            scroll_view "$TRACKLIST_PAGE_ROWS"
            ;;
        $'\e[H'|$'\e[1~'|$'\e[7~'|$'\eOH')
            TRACKLIST_LAST_STATUS=""
            TRACKLIST_VIEW_OFFSET=0
            render_tracklist
            ;;
        $'\e[F'|$'\e[4~'|$'\e[8~'|$'\eOF')
            TRACKLIST_LAST_STATUS=""
            TRACKLIST_VIEW_OFFSET=$((TRACKLIST_TOTAL_LINES - TRACKLIST_PAGE_ROWS))
            clamp_view_offset
            render_tracklist
            ;;
        [cC])
            if copy_tracklist_clipboard; then
                TRACKLIST_LAST_STATUS="${BOLD}${GREEN}✓ Tracklist copied to system clipboard!${NC}"
            else
                TRACKLIST_LAST_STATUS="${YELLOW}Notice: No clipboard utility found.${NC}"
            fi
            render_tracklist
            ;;
        [rR])
            TRACKLIST_LAST_STATUS="${BOLD}${CYAN}✓ Tracklist reloaded from disk.${NC}"
            render_tracklist
            ;;
        [sS])
            if command -v less >/dev/null 2>&1; then
                less -RFX "$TARGET_FILE"
                render_tracklist
            else
                TRACKLIST_LAST_STATUS="${YELLOW}Notice: 'less' is not installed. Use ↑/↓ to scroll instead.${NC}"
                render_tracklist
            fi
            ;;
        [eE])
            if edit_tracklist_vi; then
                TRACKLIST_LAST_STATUS="${BOLD}${GREEN}✓ Tracklist edited in VI and reloaded from disk.${NC}"
            else
                TRACKLIST_LAST_STATUS="${YELLOW}Notice: VI editor not found.${NC}"
            fi
            TRACKLIST_VIEW_OFFSET=0
            render_tracklist
            ;;
        [gG])
            generate_or_open_spek
            ;;
        [dD])
            extract_and_copy_mix_audio
            ;;
        [xX])
            if saved_path=$(export_tracklist_file html); then
                TRACKLIST_LAST_STATUS="${BOLD}${GREEN}✓ Tracklist exported (HTML): ${saved_path}${NC}"
            else
                TRACKLIST_LAST_STATUS="${BOLD}${RED}✗ HTML export failed (could not write to ${TRACKLIST_EXPORT_PATH}).${NC}"
            fi
            render_tracklist
            ;;
        [yY])
            if saved_path=$(export_tracklist_file text); then
                TRACKLIST_LAST_STATUS="${BOLD}${GREEN}✓ Tracklist exported (TEXT): ${saved_path}${NC}"
            else
                TRACKLIST_LAST_STATUS="${BOLD}${RED}✗ TEXT export failed (could not write to ${TRACKLIST_EXPORT_PATH}).${NC}"
            fi
            render_tracklist
            ;;
        [tT])
            cycle_tracklist_theme
            TRACKLIST_LAST_STATUS=$'\033[1;48;2;20;20;28;38;2;255;255;255m'"✓ Theme switched to: ${THEME_DISPLAY_NAME} (${CURRENT_THEME})"$'\033[0m'
            render_tracklist
            ;;
    esac
done
