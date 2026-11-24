#!/usr/bin/env bash
# Konsole profile for the Mix Archive Manager CLI.
#
# The desktop launchers start Konsole on this profile. The manager also asks
# an already-open Konsole session to switch to it. The family is Monospace.
# The point size and the rest of the QFont string stay whatever the user's
# default Konsole profile already uses, so the window does not jump size.

mp_cli_font_profile_name() {
    printf '%s' "MP_Mix_Manager"
}

mp_cli_monospace_font_string() {
    local dir="$HOME/.local/share/konsole" def="" line="" rest=""
    if [ -f "$HOME/.config/konsolerc" ]; then
        def=$(grep -E '^DefaultProfile=' "$HOME/.config/konsolerc" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r')
    fi
    if [ -n "$def" ] && [ -f "$dir/$def" ]; then
        line=$(grep -E '^Font=' "$dir/$def" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r')
        rest="${line#*,}"
        if [ -n "$line" ] && [ "$rest" != "$line" ] && [ -n "$rest" ]; then
            printf 'Monospace,%s' "$rest"
            return 0
        fi
    fi
    printf '%s' 'Monospace,11,-1,5,400,0,0,0,0,0,0,0,0,0,0,1,,0,0'
}

mp_ensure_cli_font_profile() {
    local dir="$HOME/.local/share/konsole" dest="" font="" body=""
    mkdir -p "$dir" 2>/dev/null || return 0
    font="$(mp_cli_monospace_font_string)"
    dest="$dir/MP_Mix_Manager.profile"
    body="[Appearance]
Font=${font}

[General]
Name=MP_Mix_Manager
Parent=FALLBACK/
"
    if [ ! -f "$dest" ] || [ "$(cat "$dest" 2>/dev/null)" != "$body" ]; then
        printf '%s' "$body" > "$dest" 2>/dev/null || return 0
    fi
}
