#!/usr/bin/env bash
# Plays the release theme track and loops Exit_MP_Video.mp4 for the whole
# install.sh run. When the installer process exits, both stop and the movie
# window is closed.
#
#   scripts/install_accompaniment.sh --watch INSTALLER_PID --pidfile PATH
#   scripts/install_accompaniment.sh --stop PATH
#
# install.sh sends this process SIGTERM from its EXIT trap, which closes the
# window at once. This process also watches the installer pid, so the window
# still closes when that trap does not run (the installer was killed, or it
# exited before the trap could signal us). A player that dies early is started
# again until the installer is gone, so the track and the movie last the
# whole run. A player that exits immediately is given up after a few tries
# so a missing sound server cannot fork-bomb the machine.

# The sourced loader is not written for `set -u`. Keep this script lenient
# and treat a missing player as "skip that part", never as a failed install.
set +e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
THEME_NAME="MPlanetarian - The Orbits of Planets (Cosmic Dreamworlds Remake).flac"

MODE=""
INSTALLER_PID=""
PIDFILE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --watch)
            MODE="watch"
            INSTALLER_PID="${2:-}"
            if [ $# -ge 2 ]; then shift 2; else shift; fi
            ;;
        --pidfile)
            PIDFILE="${2:-}"
            if [ $# -ge 2 ]; then shift 2; else shift; fi
            ;;
        --stop)
            MODE="stop"
            PIDFILE="${2:-}"
            if [ $# -ge 2 ]; then shift 2; else shift; fi
            ;;
        *)
            shift
            ;;
    esac
done

_mp_pid_token() {
    local pid="$1"
    if [ -r "/proc/${pid}/stat" ]; then
        # starttime is the 20th field after the ") " that closes comm.
        sed -n 's/.*) //p' "/proc/${pid}/stat" 2>/dev/null | awk '{ print $20 }'
        return 0
    fi
    ps -o lstart= -p "$pid" 2>/dev/null | tr -d '[:space:]'
}

_mp_child_pids() {
    local parent="$1" pid="" ppid=""
    ps -ax -o pid= -o ppid= 2>/dev/null | while read -r pid ppid; do
        if [ "$ppid" = "$parent" ]; then
            printf '%s\n' "$pid"
        fi
    done
}

# TERM the process and every child, then KILL anything still up. ffplay
# usually dies on TERM and takes its window with it. A player that ignores
# TERM (or a shell loop around paplay) is removed by KILL, which drops the
# window because the client connection dies with the process.
_mp_signal_tree() {
    local sig="$1" pid="$2" child=""
    case "$pid" in
        ''|*[!0-9]*) return 0 ;;
    esac
    [ "$pid" -gt 1 ] || return 0
    for child in $(_mp_child_pids "$pid"); do
        _mp_signal_tree "$sig" "$child"
    done
    kill "-${sig}" "$pid" 2>/dev/null || true
}

_mp_kill_tree() {
    local pid="$1" i=0
    case "$pid" in
        ''|*[!0-9]*) return 0 ;;
    esac
    _mp_signal_tree TERM "$pid"
    while [ "$i" -lt 10 ] && kill -0 "$pid" 2>/dev/null; do
        sleep 0.05
        i=$((i + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
        _mp_signal_tree KILL "$pid"
    fi
    wait "$pid" 2>/dev/null || true
}

# Refuse a reused pid. A recorded start token that no longer matches belongs
# to a different process.
_mp_kill_recorded() {
    local pid="$1" token="$2" now=""
    case "$pid" in
        ''|*[!0-9]*) return 0 ;;
    esac
    kill -0 "$pid" 2>/dev/null || return 0
    if [ -n "$token" ]; then
        now="$(_mp_pid_token "$pid")"
        if [ -n "$now" ] && [ "$now" != "$token" ]; then
            return 0
        fi
    fi
    _mp_kill_tree "$pid"
}

_mp_pidfile_field() {
    local key="$1" file="$2"
    [ -f "$file" ] || return 0
    sed -n "s/^${key}=//p" "$file" 2>/dev/null | head -n 1
}

_mp_stop_file() {
    local file="$1"
    local supervisor="" video="" audio="" vtoken="" atoken="" i=0
    [ -n "$file" ] && [ -f "$file" ] || return 0
    supervisor="$(_mp_pidfile_field supervisor "$file")"
    video="$(_mp_pidfile_field video "$file")"
    audio="$(_mp_pidfile_field audio "$file")"
    vtoken="$(_mp_pidfile_field video_token "$file")"
    atoken="$(_mp_pidfile_field audio_token "$file")"
    if [ -n "$supervisor" ] && [ "$supervisor" != "$$" ]; then
        kill -TERM "$supervisor" 2>/dev/null || true
        while [ "$i" -lt 20 ] && kill -0 "$supervisor" 2>/dev/null; do
            sleep 0.05
            i=$((i + 1))
        done
        kill -KILL "$supervisor" 2>/dev/null || true
    fi
    _mp_kill_recorded "$video" "$vtoken"
    _mp_kill_recorded "$audio" "$atoken"
    rm -f "$file"
    return 0
}

if [ "$MODE" = "stop" ]; then
    _mp_stop_file "$PIDFILE"
    exit 0
fi

if [ "$MODE" != "watch" ] || [ -z "$INSTALLER_PID" ] || [ -z "$PIDFILE" ]; then
    exit 0
fi

# shellcheck disable=SC1091
source "$ROOT/scripts/display_loader_video.sh" || exit 0

if [ -n "${MP_INSTALL_PLAYER_DIR:-}" ]; then
    PATH="${MP_INSTALL_PLAYER_DIR}:${PATH}"
    export PATH
fi

_mp_installer_alive() {
    local pid="$1" state="" token=""
    case "$pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    kill -0 "$pid" 2>/dev/null || return 1
    state="$(ps -o state= -p "$pid" 2>/dev/null | awk 'NR==1 { print $1 }')"
    case "$state" in
        Z*) return 1 ;;
    esac
    if [ -n "${INSTALLER_TOKEN:-}" ]; then
        token="$(_mp_pid_token "$pid")"
        if [ -n "$token" ] && [ "$token" != "$INSTALLER_TOKEN" ]; then
            return 1
        fi
    fi
    return 0
}

_mp_theme_track() {
    if [ -n "${MP_INSTALL_THEME_TRACK:-}" ] && [ -f "${MP_INSTALL_THEME_TRACK}" ]; then
        printf '%s\n' "$MP_INSTALL_THEME_TRACK"
        return 0
    fi
    if [ -f "$ROOT/MP_SAMPLES/$THEME_NAME" ]; then
        printf '%s\n' "$ROOT/MP_SAMPLES/$THEME_NAME"
        return 0
    fi
    if [ -f "${SCRIPT_DIR:-}/MP_SAMPLES/$THEME_NAME" ]; then
        printf '%s\n' "${SCRIPT_DIR}/MP_SAMPLES/$THEME_NAME"
        return 0
    fi
    return 1
}

_mp_write_pidfile() {
    [ -n "$PIDFILE" ] || return 0
    local tmp="${PIDFILE}.tmp.$$"
    {
        printf 'supervisor=%s\n' "$$"
        printf 'installer=%s\n' "$INSTALLER_PID"
        printf 'video=%s\n' "${VIDEO_PID:-}"
        printf 'audio=%s\n' "${AUDIO_PID:-}"
        printf 'video_token=%s\n' "${VIDEO_TOKEN:-}"
        printf 'audio_token=%s\n' "${AUDIO_TOKEN:-}"
    } > "$tmp"
    mv -f "$tmp" "$PIDFILE"
}

_mp_launch_video() {
    VIDEO_PID=""
    VIDEO_TOKEN=""
    player_pid=""
    [ -n "${VIDEO_FILE:-}" ] || return 0
    _launch_borderless_video "$VIDEO_FILE" "MP Mix Manager" 1 1 || true
    VIDEO_PID="${player_pid:-}"
    if [ -n "$VIDEO_PID" ]; then
        VIDEO_TOKEN="$(_mp_pid_token "$VIDEO_PID")"
        VIDEO_BORN=$SECONDS
    fi
}

_mp_launch_audio() {
    AUDIO_PID=""
    AUDIO_TOKEN=""
    local bin=""
    [ -n "${AUDIO_FILE:-}" ] || return 0
    if command -v ffplay >/dev/null 2>&1; then
        bin="$(command -v ffplay)"
        # -vn stops the embedded cover art from opening its own window.
        # No -volume: this process is the audible theme track.
        "$bin" -nodisp -vn -loop 0 -loglevel quiet "$AUDIO_FILE" </dev/null >/dev/null 2>&1 &
        AUDIO_PID=$!
    elif command -v mpv >/dev/null 2>&1; then
        mpv --no-video --loop-file=inf --no-terminal "$AUDIO_FILE" </dev/null >/dev/null 2>&1 &
        AUDIO_PID=$!
    elif command -v cvlc >/dev/null 2>&1; then
        cvlc --intf dummy --no-video --loop "$AUDIO_FILE" </dev/null >/dev/null 2>&1 &
        AUDIO_PID=$!
    elif command -v paplay >/dev/null 2>&1; then
        bash -c 'while true; do paplay "$1" || exit 1; done' _ "$AUDIO_FILE" </dev/null >/dev/null 2>&1 &
        AUDIO_PID=$!
    elif command -v pw-play >/dev/null 2>&1; then
        bash -c 'while true; do pw-play "$1" || exit 1; done' _ "$AUDIO_FILE" </dev/null >/dev/null 2>&1 &
        AUDIO_PID=$!
    elif command -v afplay >/dev/null 2>&1; then
        bash -c 'while true; do afplay "$1" || exit 1; done' _ "$AUDIO_FILE" </dev/null >/dev/null 2>&1 &
        AUDIO_PID=$!
    fi
    if [ -n "$AUDIO_PID" ]; then
        AUDIO_TOKEN="$(_mp_pid_token "$AUDIO_PID")"
        AUDIO_BORN=$SECONDS
    fi
}

_mp_ensure_video() {
    [ "$STOPPING" = "1" ] && return 0
    [ -n "${VIDEO_FILE:-}" ] || return 0
    [ "${VIDEO_GIVE_UP:-0}" = "1" ] && return 0
    if [ -n "${VIDEO_PID:-}" ] && kill -0 "$VIDEO_PID" 2>/dev/null; then
        if [ $((SECONDS - ${VIDEO_BORN:-0})) -ge 2 ]; then
            VIDEO_FAILS=0
        fi
        return 0
    fi
    if [ "${VIDEO_FAILS:-0}" -ge 8 ]; then
        VIDEO_GIVE_UP=1
        VIDEO_PID=""
        return 0
    fi
    _mp_launch_video
    VIDEO_FAILS=$(( ${VIDEO_FAILS:-0} + 1 ))
    _mp_write_pidfile
}

_mp_ensure_audio() {
    [ "$STOPPING" = "1" ] && return 0
    [ -n "${AUDIO_FILE:-}" ] || return 0
    [ "${AUDIO_GIVE_UP:-0}" = "1" ] && return 0
    if [ -n "${AUDIO_PID:-}" ] && kill -0 "$AUDIO_PID" 2>/dev/null; then
        if [ $((SECONDS - ${AUDIO_BORN:-0})) -ge 2 ]; then
            AUDIO_FAILS=0
        fi
        return 0
    fi
    if [ "${AUDIO_FAILS:-0}" -ge 8 ]; then
        AUDIO_GIVE_UP=1
        AUDIO_PID=""
        return 0
    fi
    _mp_launch_audio
    AUDIO_FAILS=$(( ${AUDIO_FAILS:-0} + 1 ))
    _mp_write_pidfile
}

STOPPING=0
_mp_shutdown() {
    [ "$STOPPING" = "1" ] && return 0
    STOPPING=1
    # player_pid is set by the movie launcher before it is copied into
    # VIDEO_PID. Killing both closes the window if the signal arrives in
    # between those two assignments.
    _mp_kill_tree "${VIDEO_PID:-}"
    _mp_kill_tree "${player_pid:-}"
    _mp_kill_tree "${AUDIO_PID:-}"
    VIDEO_PID=""
    AUDIO_PID=""
    rm -f "$PIDFILE"
    return 0
}

# Drop accompaniment left behind by an installer that no longer exists.
# Done before INSTALLER_TOKEN is set, so a live installer's pid is not
# compared against this run's start time.
_mp_sweep_stale() {
    local f="" old="" dir="${TMPDIR:-/tmp}"
    for f in "$dir"/mp-install-accompaniment-*.pid; do
        [ -f "$f" ] || continue
        [ "$f" = "$PIDFILE" ] && continue
        old="$(sed -n 's/^installer=//p' "$f" 2>/dev/null | head -n 1)"
        if [ -n "$old" ] && ! _mp_installer_alive "$old"; then
            bash "$ROOT/scripts/install_accompaniment.sh" --stop "$f" || true
        fi
    done
}

_mp_sweep_stale

VIDEO_PID=""
AUDIO_PID=""
VIDEO_TOKEN=""
AUDIO_TOKEN=""
VIDEO_FAILS=0
AUDIO_FAILS=0
VIDEO_GIVE_UP=0
AUDIO_GIVE_UP=0
VIDEO_BORN=0
AUDIO_BORN=0
VIDEO_FILE=""
AUDIO_FILE=""

INSTALLER_TOKEN="$(_mp_pid_token "$INSTALLER_PID")"
_mp_write_pidfile
trap '_mp_shutdown; exit 0' TERM INT HUP
trap '_mp_shutdown' EXIT

_mp_installer_alive "$INSTALLER_PID" || exit 0

VIDEO_FILE="$(find_exit_video_path 2>/dev/null || true)"
AUDIO_FILE="$(_mp_theme_track 2>/dev/null || true)"

while [ "$STOPPING" = "0" ]; do
    _mp_installer_alive "$INSTALLER_PID" || break
    _mp_ensure_video
    _mp_installer_alive "$INSTALLER_PID" || break
    _mp_ensure_audio
    _mp_installer_alive "$INSTALLER_PID" || break
    sleep 0.2
done
_mp_shutdown
exit 0
