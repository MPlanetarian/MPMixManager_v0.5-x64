#!/usr/bin/env bash
# ==============================================================================
# scripts/display_loader_video.sh
# Purpose: Display MP_loader.mp4 while MP Mix Archive Manager is loading in console.
#          Skips playback if an instance of the manager is already open.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "${VERSION:-}" ]; then
    if [ -f "$SCRIPT_DIR/VERSION" ]; then
        VERSION="$(head -n 1 "$SCRIPT_DIR/VERSION" | tr -d '\r\n')"
    else
        VERSION="0.5.4 Stable (Sensorium)"
    fi
fi
export VERSION

if [ -z "${OS_TYPE:-}" ] || [ "${OS_TYPE:-}" = "linux" -a -n "${WINDIR:-}" ] || [[ "${OS_TYPE:-}" = "linux" && "$(uname -s 2>/dev/null)" =~ (MINGW|MSYS|CYGWIN) ]]; then
    case "$(uname -s 2>/dev/null)" in
        Darwin*) OS_TYPE="macos" ;;
        CYGWIN*|MINGW*|MSYS*) OS_TYPE="windows" ;;
        FreeBSD*) OS_TYPE="freebsd" ;;
        *)
            if [[ "${OSTYPE:-}" =~ (msys|cygwin|win32) ]] || [ -n "${WINDIR:-}" ] || [ -n "${SYSTEMROOT:-}" ]; then
                OS_TYPE="windows"
            else
                OS_TYPE="linux"
            fi
            ;;
    esac
fi
export OS_TYPE

# Ensure Homebrew and standard bins are in PATH for macOS
if [ "$OS_TYPE" = "macos" ] || [ "$(uname -s)" = "Darwin" ]; then
    export PATH="/opt/homebrew/bin:/usr/local/bin:$SCRIPT_DIR/bin:$SCRIPT_DIR:$HOME/.local/bin:$PATH"
fi

# Check if another instance of Mix Archive Manager is already running
is_manager_already_open() {
    local my_pid="${1:-$$}"
    local p

    # 1. Check bridge status JSON for active manager session
    local bridge_file="${HOME}/.config/mix-manager/bridge_status.json"
    if [ -f "$bridge_file" ]; then
        local saved_pid
        saved_pid=$(grep -o '"pid": *[0-9]*' "$bridge_file" 2>/dev/null | grep -o '[0-9]*' || true)
        if [[ -n "$saved_pid" && "$saved_pid" != "$my_pid" && "$saved_pid" != "${PPID:-0}" ]]; then
            if kill -0 "$saved_pid" 2>/dev/null; then
                if [ -r "/proc/$saved_pid/cmdline" ]; then
                    if grep -qa -E "Mix_Archive_Manager|manager\.sh" "/proc/$saved_pid/cmdline" 2>/dev/null; then
                        return 0
                    fi
                else
                    local proc_args
                    proc_args="$(ps -p "$saved_pid" -o args= 2>/dev/null || ps -p "$saved_pid" -o comm= 2>/dev/null || true)"
                    if [[ "$proc_args" =~ (Mix_Archive_Manager|manager\.sh|manager_macos) ]]; then
                        return 0
                    fi
                fi
            fi
        fi
    fi

    # 2. Check process table for running Mix_Archive_Manager.sh
    if command -v pgrep &>/dev/null; then
        local pids
        pids=$(pgrep -f "Mix_Archive_Manager\.sh" 2>/dev/null || true)
        for p in $pids; do
            [[ -z "$p" ]] && continue
            [[ "$p" == "$my_pid" || "$p" == "${PPID:-0}" ]] && continue
            local p_ppid=""
            if [ -d "/proc/$p" ]; then
                p_ppid=$(awk '{print $4}' "/proc/$p/stat" 2>/dev/null || true)
            else
                p_ppid=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ' || true)
            fi
            [[ "$p_ppid" == "$my_pid" ]] && continue

            if kill -0 "$p" 2>/dev/null; then
                return 0
            fi
        done
    fi

    return 1
}

# Locate MP_loader.mp4
find_loader_video_path() {
    local cand
    local cand_list=()
    if [ -n "${LOADER_VIDEO_FILE:-}" ]; then
        cand_list+=("${LOADER_VIDEO_FILE}" "$SCRIPT_DIR/${LOADER_VIDEO_FILE}")
    fi
    cand_list+=(
        "$SCRIPT_DIR/MP_loader.mp4"
        "$SCRIPT_DIR/assets/MP_loader.mp4"
        "$SCRIPT_DIR/gemini_generated_video_BEB1D608.mp4"
        "$HOME/GoogleDrive/gemini_generated_video_BEB1D608.mp4"
    )
    for cand in "${cand_list[@]}"; do
        if [ -n "$cand" ] && [ -f "$cand" ] && [ -s "$cand" ]; then
            printf '%s\n' "$cand"
            return 0
        fi
    done
    return 1
}

# Locate Exit_MP_Video.mp4, the movie played when the last MP window closes.
# It ships as MP4 next to MP_loader.mp4 so a host without Matroska support can
# still play it.
find_exit_video_path() {
    local cand
    local cand_list=()
    if [ -n "${EXIT_VIDEO_FILE:-}" ]; then
        cand_list+=("${EXIT_VIDEO_FILE}" "$SCRIPT_DIR/${EXIT_VIDEO_FILE}")
    fi
    cand_list+=(
        "$SCRIPT_DIR/Exit_MP_Video.mp4"
        "$SCRIPT_DIR/assets/Exit_MP_Video.mp4"
        "$HOME/Videos/Exit_MP_Video.mp4"
    )
    for cand in "${cand_list[@]}"; do
        if [ -n "$cand" ] && [ -f "$cand" ] && [ -s "$cand" ]; then
            printf '%s\n' "$cand"
            return 0
        fi
    done
    return 1
}

# How long a movie actually runs, in whole seconds (rounded up).
# ffplay's -autoexit is not reliable on every PipeWire/Bluetooth sink: it can
# sit on a finished movie indefinitely, or drop out after a second. Both cases
# used to hold the window back until the full 90 s timeout, so the movie is
# bounded by its own length instead. ffprobe is optional; a movie we cannot
# measure gets a generous fallback.
_video_duration_seconds() {
    local vid="$1" dur=""
    if command -v ffprobe >/dev/null 2>&1; then
        dur="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$vid" 2>/dev/null | head -n 1)"
    fi
    case "$dur" in
        ''|*[!0-9.]*) dur="" ;;
    esac
    if [ -z "$dur" ]; then
        printf '%s\n' "${MP_VIDEO_FALLBACK_SECONDS:-45}"
        return 0
    fi
    # Bash arithmetic cannot round a float, so do it in awk.
    awk -v d="$dur" 'BEGIN { printf "%d\n", (d == int(d) ? d : int(d) + 1) }'
    return 0
}

# Seconds to wait for one movie before the caller gives up on it.
# This is the movie's own length plus a small grace for the window to close,
# never more than the user's configured ceiling. A player that hangs is
# therefore stopped at the end of the movie rather than at the ceiling.
_video_wait_limit() {
    local vid="$1" ceiling="${2:-}"
    local dur grace="${MP_VIDEO_CLOSE_GRACE_SECONDS:-8}" limit
    dur="$(_video_duration_seconds "$vid")"
    limit=$(( dur + grace ))
    case "$ceiling" in
        ''|*[!0-9]*) ;;
        *) [ "$ceiling" -gt 0 ] && [ "$ceiling" -lt "$limit" ] && limit="$ceiling" ;;
    esac
    printf '%s\n' "$limit"
    return 0
}

# Console skip during the loader. Empty input is Enter.
# Bash 3.2 (stock macOS) rejects a fractional read timeout, which made the
# wait loop finish immediately and close the movie before it appeared.
_loader_skip_requested() {
    local key="" timeout="0.1" rc=0
    if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
        timeout="1"
    fi
    # A launcher with no controlling terminal cannot read a skip key. Probing
    # /dev/tty every tick printed hundreds of "No such device" lines into the
    # user's launch log, so check once whether there is a terminal at all.
    if [ "${_MP_SKIP_HAS_TTY:-}" = "0" ]; then
        return 1
    fi
    if [ -z "${_MP_SKIP_HAS_TTY:-}" ]; then
        if ( : </dev/tty ) 2>/dev/null; then
            _MP_SKIP_HAS_TTY=1
        else
            _MP_SKIP_HAS_TTY=0
            return 1
        fi
    fi
    if IFS= read -rsn1 -t "$timeout" key </dev/tty 2>/dev/null; then
        case "$key" in
            g|G)
                # G opens MP Gui from the startup logo. During the exit movie the
                # app is closing, so the shortcut would just reopen what the user
                # closed.
                if [ "${_MP_SKIP_ALLOWS_GUI:-1}" = "1" ] && [ -f "${SCRIPT_DIR}/MP_Gui.sh" ]; then
                    mkdir -p "${HOME}/.config/mix-manager" 2>/dev/null || true
                    if command -v setsid >/dev/null 2>&1; then
                        setsid bash "${SCRIPT_DIR}/MP_Gui.sh" >>"${HOME}/.config/mix-manager/mp_gui.log" 2>&1 </dev/null &
                    else
                        nohup bash "${SCRIPT_DIR}/MP_Gui.sh" >>"${HOME}/.config/mix-manager/mp_gui.log" 2>&1 </dev/null &
                    fi
                    disown 2>/dev/null || true
                fi
                return 0
                ;;
            $'\e'|[qQ]|""|" ") return 0 ;;
        esac
        return 1
    else
        rc=$?
    fi
    # A rejected timeout or a missing tty returns at once. Pace the loop.
    if [ "$rc" -le 128 ]; then
        sleep 1
    fi
    return 1
}

# Paint the manager console black with no text while the loader video plays.
# clear alone keeps the terminal theme color and any status line already drawn.
_blank_loader_console() {
    local rows="" cols="" sz="" i="" line=""
    sz="$(stty size </dev/tty 2>/dev/null || stty size 2>/dev/null || true)"
    rows="${sz%% *}"
    cols="${sz#* }"
    [[ "$rows" =~ ^[0-9]+$ ]] || rows="${LINES:-40}"
    [[ "$cols" =~ ^[0-9]+$ ]] || cols="${COLUMNS:-120}"
    (( rows < 1 )) && rows=24
    (( cols < 1 )) && cols=80
    (( rows > 200 )) && rows=200
    (( cols > 400 )) && cols=400
    printf -v line '%*s' "$cols" ''
    # Hide the cursor, force black on black, drop scrollback, then fill
    # every cell so a theme-colored clear cannot leave the loading card.
    printf '\033[?25l\033[30;40m\033[H\033[2J\033[3J'
    for (( i=0; i<rows; i++ )); do
        printf '\033[30;40m%s\n' "$line"
    done
    printf '\033[H'
}

# Borderless always-on-top logo window. Sets player_pid in the caller.
# Do not launch this player inside $(...): that subshell sends SIGHUP
# as soon as the substitution returns, and the window disappears.
play_macos_borderless_loader() {
    local vid="$1"
    local loop="${2:-0}"
    local mute="${3:-0}"
    local src="" cache_dir="" bin="" swiftc_bin="" log=""
    [ -n "$vid" ] && [ -f "$vid" ] || return 1

    src="$SCRIPT_DIR/scripts/macos_borderless_loader.swift"
    [ -f "$src" ] || return 1
    cache_dir="${HOME}/.cache/mix-manager"
    bin="$cache_dir/macos_borderless_loader"
    log="$cache_dir/macos_borderless_loader.log"

    if command -v swiftc >/dev/null 2>&1; then
        swiftc_bin="$(command -v swiftc)"
    elif command -v xcrun >/dev/null 2>&1; then
        swiftc_bin="$(xcrun --find swiftc 2>/dev/null || true)"
    fi

    if [ -n "$swiftc_bin" ] && [ -x "$swiftc_bin" ]; then
        mkdir -p "$cache_dir" 2>/dev/null || true
        # swiftc of this AppKit player can take well over 20 seconds.
        # Waiting here leaves the manager on the loading card, so a missing
        # or stale binary is rebuilt in the background and this launch
        # uses the binary that is already on disk.
        if [ "$loop" = "1" ] && { [ ! -x "$bin" ] || [ "$src" -nt "$bin" ]; }; then
            # The uninstaller needs the binary that understands --loop. A
            # background compile would leave this launch on the old player,
            # which closes when the movie ends.
            local tmp="${bin}.new.$$"
            if "$swiftc_bin" -o "$tmp" "$src" >"$log" 2>&1; then
                chmod +x "$tmp" 2>/dev/null || true
                mv -f "$tmp" "$bin"
            else
                rm -f "$tmp"
            fi
        elif [ ! -x "$bin" ] || [ "$src" -nt "$bin" ]; then
            local pidf="$cache_dir/macos_borderless_loader.compile.pid"
            local oldp="" compiling=0
            if [ -f "$pidf" ]; then
                IFS= read -r oldp < "$pidf" 2>/dev/null || oldp=""
                if [[ "$oldp" =~ ^[0-9]+$ ]] && kill -0 "$oldp" 2>/dev/null; then
                    compiling=1
                fi
            fi
            if [ "$compiling" -eq 0 ]; then
                (
                    tmp="${bin}.new.$$"
                    if "$swiftc_bin" -o "$tmp" "$src" >"$log" 2>&1; then
                        chmod +x "$tmp" 2>/dev/null || true
                        mv -f "$tmp" "$bin"
                    else
                        rm -f "$tmp"
                    fi
                    rm -f "$pidf"
                ) >/dev/null 2>&1 &
                echo $! > "$pidf"
                disown 2>/dev/null || true
            fi
        fi
        if [ -x "$bin" ]; then
            # Path first, so a cached binary from before --loop still opens the
            # movie. The new binary sees --loop and repeats until it is killed.
            if [ "$loop" = "1" ]; then
                # Path first, so a cached binary from before these flags still
                # opens the movie. --silent keeps the theme track as the only
                # audio while the installer is running.
                if [ "$mute" = "1" ]; then
                    "$bin" "$vid" --loop --silent </dev/null &>/dev/null &
                else
                    "$bin" "$vid" --loop </dev/null &>/dev/null &
                fi
            else
                "$bin" "$vid" </dev/null &>/dev/null &
            fi
            player_pid=$!
            # A bad cached binary exits at once. Leave player_pid empty so
            # the caller can try ffplay, mpv, or VLC instead.
            sleep 0.2
            if kill -0 "$player_pid" 2>/dev/null; then
                return 0
            fi
            wait "$player_pid" 2>/dev/null || true
            player_pid=""
        fi
    fi
    return 1
}

# Locate every media player the loader and the exit video can use. Sets the
# caller's ffplay_bin / mpv_bin / vlc_bin / ps_bin / is_gui.
_find_video_players() {
    is_gui=0
    if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ] || [ "${OS_TYPE:-}" = "macos" ] || [ "${OS_TYPE:-}" = "windows" ] || [ "${OS_TYPE:-}" = "wsl" ] || [ "$(uname -s 2>/dev/null)" = "Darwin" ] || [[ "$(uname -s 2>/dev/null)" =~ (MINGW|MSYS|CYGWIN) ]]; then
        is_gui=1
    fi

    ffplay_bin=""
    if command -v ffplay &>/dev/null; then
        ffplay_bin="$(command -v ffplay)"
    elif [ -n "${LOCALAPPDATA:-}" ] && [ -x "$LOCALAPPDATA/Microsoft/WinGet/Links/ffplay.exe" ]; then
        ffplay_bin="$LOCALAPPDATA/Microsoft/WinGet/Links/ffplay.exe"
    elif [ -x "/c/Users/${USER:-Mathe}/AppData/Local/Microsoft/WinGet/Links/ffplay.exe" ]; then
        ffplay_bin="/c/Users/${USER:-Mathe}/AppData/Local/Microsoft/WinGet/Links/ffplay.exe"
    elif [ -x "/c/ffmpeg/bin/ffplay.exe" ]; then
        ffplay_bin="/c/ffmpeg/bin/ffplay.exe"
    elif [ -x "/c/ProgramData/chocolatey/bin/ffplay.exe" ]; then
        ffplay_bin="/c/ProgramData/chocolatey/bin/ffplay.exe"
    elif [ -x "/opt/homebrew/bin/ffplay" ]; then
        ffplay_bin="/opt/homebrew/bin/ffplay"
    elif [ -x "/usr/local/bin/ffplay" ]; then
        ffplay_bin="/usr/local/bin/ffplay"
    fi

    mpv_bin=""
    if command -v mpv &>/dev/null; then
        mpv_bin="$(command -v mpv)"
    elif [ -x "/c/Program Files/mpv/mpv.exe" ]; then
        mpv_bin="/c/Program Files/mpv/mpv.exe"
    elif [ -n "${LOCALAPPDATA:-}" ] && [ -x "$LOCALAPPDATA/Programs/mpv/mpv.exe" ]; then
        mpv_bin="$LOCALAPPDATA/Programs/mpv/mpv.exe"
    elif [ -x "/c/ProgramData/chocolatey/bin/mpv.exe" ]; then
        mpv_bin="/c/ProgramData/chocolatey/bin/mpv.exe"
    elif [ -x "/opt/homebrew/bin/mpv" ]; then
        mpv_bin="/opt/homebrew/bin/mpv"
    elif [ -x "/usr/local/bin/mpv" ]; then
        mpv_bin="/usr/local/bin/mpv"
    elif [ -x "/Applications/mpv.app/Contents/MacOS/mpv" ]; then
        mpv_bin="/Applications/mpv.app/Contents/MacOS/mpv"
    fi

    vlc_bin=""
    if command -v cvlc &>/dev/null; then
        vlc_bin="$(command -v cvlc)"
    elif command -v vlc &>/dev/null; then
        vlc_bin="$(command -v vlc)"
    elif [ -x "/Applications/VLC.app/Contents/MacOS/VLC" ]; then
        vlc_bin="/Applications/VLC.app/Contents/MacOS/VLC"
    elif [ -x "/c/Program Files/VideoLAN/VLC/vlc.exe" ]; then
        vlc_bin="/c/Program Files/VideoLAN/VLC/vlc.exe"
    elif [ -x "/c/Program Files (x86)/VideoLAN/VLC/vlc.exe" ]; then
        vlc_bin="/c/Program Files (x86)/VideoLAN/VLC/vlc.exe"
    fi

    ps_bin=""
    if [ "${OS_TYPE:-}" = "windows" ] || [[ "$(uname -s 2>/dev/null)" =~ (MINGW|MSYS|CYGWIN) ]]; then
        if command -v powershell.exe &>/dev/null; then
            ps_bin="powershell.exe"
        elif [ -x "/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" ]; then
            ps_bin="/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        fi
    fi
}

# ffplay window shared by the logo, the exit movie, and the installer loop.
# mute=1 uses -volume 0. Do not pass -an: on Exit_MP_Video.mp4 it fails the
# filter graph, then ffplay sits there with no window and never exits.
_spawn_ffplay_movie() {
    local bin="$1" loop="$2" mute="$3" title="$4" vid="$5"
    if [ "$loop" = "1" ] && [ "$mute" = "1" ]; then
        "$bin" -volume 0 -loop 0 -noborder -alwaysontop -window_title "$title" -loglevel quiet "$vid" </dev/null &>/dev/null &
    elif [ "$loop" = "1" ]; then
        "$bin" -loop 0 -noborder -alwaysontop -window_title "$title" -loglevel quiet "$vid" </dev/null &>/dev/null &
    else
        "$bin" -autoexit -noborder -alwaysontop -window_title "$title" -loglevel quiet "$vid" </dev/null &>/dev/null &
    fi
    player_pid=$!
}

# Open a borderless, always-on-top window playing one movie. Sets the caller's
# player_pid. The startup logo and the exit movie share this path, so both
# look and behave the same on every platform.
# loop=1 repeats until the process is killed. mute=1 keeps the picture silent
# so the installer's theme track is the only audio.
_launch_borderless_video() {
    local vid="$1"
    local title="${2:-MP Mix Archive Manager}"
    local loop="${3:-0}"
    local mute="${4:-0}"
    # Empty unless the installer asked for a silent picture. Unquoted on the
    # mpv and vlc command lines so a normal playback does not gain an argument.
    local mpv_mute="" vlc_mute=""
    if [ "$mute" = "1" ]; then
        mpv_mute="--no-audio"
        vlc_mute="--no-audio"
    fi
    [ -n "$vid" ] && [ -f "$vid" ] || return 1
    player_pid=""

    local is_gui ffplay_bin mpv_bin vlc_bin ps_bin
    _find_video_players

    local win_vid="$vid"
    if [ "${OS_TYPE:-}" = "windows" ] || [[ "$(uname -s 2>/dev/null)" =~ (MINGW|MSYS|CYGWIN) ]]; then
        if command -v cygpath &>/dev/null; then
            win_vid="$(cygpath -w "$vid" 2>/dev/null || echo "$vid")"
        fi
    fi

    [ "$is_gui" -eq 1 ] || return 1

    if [ "${OS_TYPE:-}" = "macos" ] || [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then
        # Borderless window on macOS. Never open QuickTime Player.
        if ! play_macos_borderless_loader "$vid" "$loop" "$mute"; then
            if [ -n "$ffplay_bin" ]; then
                _spawn_ffplay_movie "$ffplay_bin" "$loop" "$mute" "$title" "$vid"
            elif [ -n "$mpv_bin" ]; then
                if [ "$loop" = "1" ]; then
                    # shellcheck disable=SC2086
                    "$mpv_bin" --ontop --no-border --title="$title" --no-terminal --loop-file=inf ${mpv_mute:-} "$vid" </dev/null &>/dev/null &
                else
                    "$mpv_bin" --ontop --no-border --title="$title" --no-terminal "$vid" </dev/null &>/dev/null &
                fi
                player_pid=$!
            elif [ -n "$vlc_bin" ]; then
                if [ "$loop" = "1" ]; then
                    # shellcheck disable=SC2086
                    "$vlc_bin" --loop ${vlc_mute:-} --video-on-top --no-video-title-show --no-video-deco "$vid" </dev/null &>/dev/null &
                else
                    "$vlc_bin" --play-and-exit --video-on-top --no-video-title-show --no-video-deco "$vid" </dev/null &>/dev/null &
                fi
                player_pid=$!
            fi
        fi
    elif [ -n "$ffplay_bin" ]; then
        _spawn_ffplay_movie "$ffplay_bin" "$loop" "$mute" "$title" "$vid"
    elif [ -n "$mpv_bin" ]; then
        if [ "$loop" = "1" ]; then
            # shellcheck disable=SC2086
            "$mpv_bin" --ontop --no-border --title="$title" --no-terminal --loop-file=inf ${mpv_mute:-} "$vid" </dev/null &>/dev/null &
        else
            "$mpv_bin" --ontop --no-border --title="$title" --no-terminal "$vid" </dev/null &>/dev/null &
        fi
        player_pid=$!
    elif [ -n "$vlc_bin" ]; then
        if [ "$loop" = "1" ]; then
            # shellcheck disable=SC2086
            "$vlc_bin" --loop ${vlc_mute:-} --video-on-top --no-video-title-show "$vid" </dev/null &>/dev/null &
        else
            "$vlc_bin" --play-and-exit --video-on-top --no-video-title-show "$vid" </dev/null &>/dev/null &
        fi
        player_pid=$!
    elif [ -n "$ps_bin" ]; then
        local win_esc_vid="${win_vid//\'/\'\'}"
        local win_esc_title="${title//\'/\'\'}"
        # Single quotes keep the PowerShell `$` signs. The double-quoted
        # -Command string below inserts these lines without expanding them.
        local win_ended='$m.Add_MediaEnded({ $w.Close() });'
        local win_dismiss='$w.Add_KeyDown({ param($s,$e) $w.Close() }); $w.Add_MouseDown({ param($s,$e) $w.Close() });'
        local win_volume=""
        if [ "$mute" = "1" ]; then
            win_volume='$m.Volume = 0;'
        fi
        if [ "$loop" = "1" ]; then
            # Repeat until the uninstaller kills this process. A click must
            # not take the window down while the removal is still running.
            win_ended='$m.Add_MediaEnded({ $m.Position = [TimeSpan]::Zero; $m.Play() });'
            win_dismiss=''
        fi
        "$ps_bin" -NoProfile -Command "
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase;
\$w = New-Object Windows.Window;
\$w.Title = '$win_esc_title';
\$w.WindowStyle = 'None';
\$w.AllowsTransparency = \$true;
\$w.Background = [Windows.Media.Brushes]::Black;
\$w.Topmost = \$true;
\$w.WindowStartupLocation = 'CenterScreen';
\$w.Width = 800;
\$w.Height = 450;
\$m = New-Object Windows.Controls.MediaElement;
\$m.LoadedBehavior = 'Play';
\$m.Source = [Uri]'$win_esc_vid';
$win_volume
$win_ended
$win_dismiss
\$w.Content = \$m;
\$w.ShowDialog() | Out-Null
" </dev/null &>/dev/null &
        player_pid=$!
    elif [ "$loop" = "1" ]; then
        # cmd.exe start returns at once, so a looping uninstaller would open a
        # new window on every restart. With no real player there is no window.
        player_pid=""
        return 1
    elif [ "${OS_TYPE:-}" = "windows" ] || [[ "$(uname -s 2>/dev/null)" =~ (MINGW|MSYS|CYGWIN) ]]; then
        cmd.exe /c start "" "$win_vid" &>/dev/null &
        player_pid=$!
    fi

    [ -n "$player_pid" ]
}

# Wait for one movie to finish. Esc, Space, Enter and q skip it, exactly like
# the startup logo. limit is a wall-clock cap in seconds, not a tick count.
_wait_for_video_player() {
    local pid="$1" limit="${2:-90}"
    [ -n "$pid" ] || return 0
    local start="${SECONDS:-0}"
    while kill -0 "$pid" 2>/dev/null; do
        if _loader_skip_requested; then
            kill -9 "$pid" 2>/dev/null || true
            break
        fi
        if [ $(( ${SECONDS:-0} - start )) -ge "$limit" ]; then
            kill -9 "$pid" 2>/dev/null || true
            break
        fi
        sleep 0.1 2>/dev/null || sleep 1
    done
    wait "$pid" 2>/dev/null || true
    return 0
}

# Give a finished movie a moment to take its window down, then stop it.
# ffplay on a stalled PipeWire sink leaves its window behind after the movie
# has ended; the next window must not appear underneath it.
_close_video_player() {
    local pid="$1"
    [ -n "$pid" ] || return 0
    kill -0 "$pid" 2>/dev/null || { wait "$pid" 2>/dev/null || true; return 0; }
    local waited=0
    while [ "$waited" -lt "${MP_VIDEO_CLOSE_WAIT_SECONDS:-2}" ] && kill -0 "$pid" 2>/dev/null; do
        sleep 0.25 2>/dev/null || sleep 1
        waited=$((waited + 1))
    done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    return 0
}

# Key that changes when the graphical session changes. The startup logo is
# shown once per session, so an idle desktop that is already logged in does not
# replay ten seconds of movie every time the manager is reopened. The boot id
# plus the login session id covers a reboot, a logout, and a switch between
# Wayland and X11 in one value.
_loader_session_key() {
    local _boot="" _sess="" _f=""
    for _f in /proc/sys/kernel/random/boot_id /proc/stat; do
        [ -r "$_f" ] || continue
        if [ "$_f" = "/proc/stat" ]; then
            _boot="$(awk '/^btime /{print $2; exit}' "$_f" 2>/dev/null || true)"
        else
            _boot="$(tr -d '\-\r\n' < "$_f" 2>/dev/null || true)"
        fi
        [ -n "$_boot" ] && break
    done
    _sess="${XDG_SESSION_ID:-}"
    if [ -z "$_sess" ] && command -v loginctl >/dev/null 2>&1; then
        _sess="$(loginctl --no-legend list-sessions 2>/dev/null | awk -v u="${USER:-}" '$3 == u {print $1; exit}' || true)"
    fi
    printf '%s-%s-%s' "${_boot:-none}" "${_sess:-none}" "${XDG_SESSION_TYPE:-none}"
}

# Returns 0 when this session has already shown the startup logo.
_loader_already_played_this_session() {
    local _dir="${HOME}/.config/mix-manager" _marker=""
    _marker="${_dir}/loader_video_session"
    [ -f "$_marker" ] || return 1
    [ "$(cat "$_marker" 2>/dev/null || true)" = "$(_loader_session_key)" ]
}

_loader_mark_session_played() {
    local _dir="${HOME}/.config/mix-manager" _tmp=""
    mkdir -p "$_dir" 2>/dev/null || return 0
    _tmp="${_dir}/loader_video_session.$$.tmp"
    if printf '%s\n' "$(_loader_session_key)" > "$_tmp" 2>/dev/null; then
        mv -f "$_tmp" "${_dir}/loader_video_session" 2>/dev/null || rm -f "$_tmp" 2>/dev/null || true
    else
        rm -f "$_tmp" 2>/dev/null || true
    fi
    return 0
}

# Display loader video whilst manager is loading
display_startup_loader_video() {
    local force="${1:-0}"
    local my_pid="$$"

    # Respect user configuration disable switch
    if [ "$force" -ne 1 ] && [ "${SHOW_LOADER_VIDEO:-true}" = "false" ]; then
        return 0
    fi

    # The logo is a first-open-of-the-session flourish. Reopening the manager
    # on an already logged-in desktop must not wait for the whole clip again,
    # which is what made a second launch feel like it had hung.
    if [ "$force" -ne 1 ] && _loader_already_played_this_session; then
        return 0
    fi

    # Do not display for fast non-interactive CLI actions
    if [ "$force" -ne 1 ] && [ -n "${CLI_INITIAL_ACTION:-}" ]; then
        return 0
    fi

    # MP Gui plays Exit_MP_Video.mp4 itself, once, before its window exists,
    # and only for the first instance. The menu inside that window must not
    # also play MP_loader.mp4, and a second MP Gui must not show a loader.
    if [ "$force" -ne 1 ] && [ "${MP_GUI_SESSION:-0}" = "1" ]; then
        return 0
    fi

    # If manager is already open, do not display it!
    if [ "$force" -ne 1 ] && is_manager_already_open "$my_pid"; then
        return 0
    fi

    # Locate video file
    local loader_vid
    loader_vid="$(find_loader_video_path)" || return 0
    [ -z "$loader_vid" ] && return 0

    # Leave the console black while the loader video plays.
    # Esc, Space, Enter, and q still skip the intro.
    _blank_loader_console

    # Launch the borderless always-on-top window.
    local player_pid=""
    _launch_borderless_video "$loader_vid" "MP Mix Archive Manager" || true

    # Remember that this session has had its logo, whether the movie finishes,
    # times out, or is skipped. Later launches go straight to the menu.
    [ -n "$player_pid" ] && _loader_mark_session_played

    # Warm up caches in parallel while video is loading
    if command -v load_header_cache &>/dev/null; then
        load_header_cache >/dev/null 2>&1 || true
    fi

    # Wait for player completion or interactive skip keypress in console
    if [ -n "$player_pid" ]; then
        local waited=0
        local wait_limit=105
        # Stock macOS bash 3.2 can only wait in whole seconds.
        if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
            wait_limit=11
        fi
        while kill -0 "$player_pid" 2>/dev/null; do
            if _loader_skip_requested; then
                kill -9 "$player_pid" 2>/dev/null || true
                break
            fi
            waited=$((waited + 1))
            # Safety timeout of about 10.5 seconds (video is 10.0s).
            # Kill before wait, or a stuck player keeps the menu from appearing.
            if [ "$waited" -ge "$wait_limit" ]; then
                kill -9 "$player_pid" 2>/dev/null || true
                break
            fi
        done
        wait "$player_pid" 2>/dev/null || true
    else
        # In pure headless / no-display environments, provide a brief 1.2s loading beat
        sleep 1.2
    fi

    printf '\033[0m'
    clear 2>/dev/null || true
    printf '\033[?25h' 2>/dev/null || true
    return 0
}

# Display Exit_MP_Video.mp4 before the first MP Gui or MP Liquid Player window
# appears. The host decides whether this launch is the first one of its kind
# (see scripts/exit_video.py). A second, third, or fourth window never calls
# this. The movie plays once and its window is closed before the caller
# continues. Skippable with Esc, Space, Enter or q where a terminal is attached.
display_player_intro_video() {
    local force="${1:-0}"
    if [ "${MP_NO_PLAYER_INTRO:-0}" = "1" ]; then
        return 0
    fi
    if [ "$force" -ne 1 ] && [ "${SHOW_PLAYER_INTRO_VIDEO:-true}" = "false" ]; then
        return 0
    fi

    local intro_vid
    intro_vid="$(find_exit_video_path)" || return 0
    [ -z "$intro_vid" ] && return 0

    local player_pid=""
    _launch_borderless_video "$intro_vid" "MP Liquid Player" || return 0
    [ -n "$player_pid" ] || return 0
    # Bound the wait by the movie itself, not by the 90 s ceiling. A player
    # that hangs on a stalled sink would otherwise hold the window back long
    # after the movie has ended.
    _wait_for_video_player "$player_pid" \
        "$(_video_wait_limit "$intro_vid" "${PLAYER_INTRO_VIDEO_MAX_SECONDS:-90}")"
    _close_video_player "$player_pid"
    return 0
}

# Display the exit movie when the last MP Gui or MP Liquid Player window closes.
# The caller decides whether this window is the last one; this function only
# plays the movie and returns once it has finished.
display_exit_video() {
    local force="${1:-0}"
    if [ "$force" -ne 1 ] && [ "${SHOW_EXIT_VIDEO:-true}" = "false" ]; then
        return 0
    fi

    local exit_vid
    exit_vid="$(find_exit_video_path)" || return 0
    [ -z "$exit_vid" ] && return 0

    local player_pid=""
    _launch_borderless_video "$exit_vid" "MP Mix Manager" || return 0
    if [ -n "$player_pid" ]; then
        _wait_for_video_player "$player_pid" \
            "$(_video_wait_limit "$exit_vid" "${EXIT_VIDEO_MAX_SECONDS:-90}")"
        _close_video_player "$player_pid"
    fi
    return 0
}

# Open the exit movie in a loop and return at once. Sets UNINSTALL_VIDEO_PID.
# The uninstaller calls this while it works, then stop_uninstall_exit_video
# when it finishes. A missing display, a missing movie, or SHOW_EXIT_VIDEO=false
# leaves the pid empty and returns 0 so the removal still runs.
start_uninstall_exit_video() {
    UNINSTALL_VIDEO_PID=""
    player_pid=""
    if [ "${MP_NO_UNINSTALL_VIDEO:-0}" = "1" ]; then
        return 0
    fi
    if [ "${SHOW_EXIT_VIDEO:-true}" = "false" ]; then
        return 0
    fi
    local exit_vid=""
    exit_vid="$(find_exit_video_path)" || return 0
    [ -n "$exit_vid" ] || return 0
    _launch_borderless_video "$exit_vid" "MP Mix Manager" 1 || return 0
    UNINSTALL_VIDEO_PID="${player_pid:-}"
    return 0
}

# Close the looping exit-movie window. Safe when the pid is empty or already gone.
stop_uninstall_exit_video() {
    local pid="${1:-${UNINSTALL_VIDEO_PID:-}}"
    [ -n "$pid" ] || return 0
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        kill -- "-$pid" 2>/dev/null || true
    fi
    local waited=0
    while [ "$waited" -lt 8 ] && kill -0 "$pid" 2>/dev/null; do
        sleep 0.25 2>/dev/null || sleep 1
        waited=$((waited + 1))
    done
    kill -9 "$pid" 2>/dev/null || true
    kill -9 -- "-$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    if [ "${UNINSTALL_VIDEO_PID:-}" = "$pid" ]; then
        UNINSTALL_VIDEO_PID=""
    fi
    return 0
}

# Standalone execution
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-}" in
        --check-only)
            if is_manager_already_open "$$"; then
                echo "ALREADY_OPEN"
                exit 0
            else
                echo "NOT_OPEN"
                exit 1
            fi
            ;;
        --path)
            find_loader_video_path
            exit 0
            ;;
        --exit-path)
            find_exit_video_path
            exit 0
            ;;
        --intro)
            # The first MP Gui or MP Liquid Player window plays
            # Exit_MP_Video.mp4 before its window appears. Instances two to
            # four never call this. G would open another window behind the
            # movie the user is waiting on.
            _MP_SKIP_ALLOWS_GUI=0
            display_player_intro_video 0
            exit 0
            ;;
        --intro-force)
            _MP_SKIP_ALLOWS_GUI=0
            display_player_intro_video 1
            exit 0
            ;;
        --force|-f)
            display_startup_loader_video 1
            exit 0
            ;;
        --exit-video|--exit)
            # The exit movie must not answer "G" by reopening MP Gui, and a
            # closing host has no terminal to read a skip key from.
            _MP_SKIP_ALLOWS_GUI=0
            if [ -n "${MP_EXIT_VIDEO_NO_TTY:-}" ]; then
                _loader_skip_requested() { return 1; }
            fi
            display_exit_video 0
            exit 0
            ;;
        --exit-force)
            _MP_SKIP_ALLOWS_GUI=0
            if [ -n "${MP_EXIT_VIDEO_NO_TTY:-}" ]; then
                _loader_skip_requested() { return 1; }
            fi
            display_exit_video 1
            exit 0
            ;;
        *)
            display_startup_loader_video 0
            exit 0
            ;;
    esac
fi
