#!/usr/bin/env bash
# ==============================================================================
# mp_pi_detect.sh — Raspberry Pi / low-power ARM detection for the launchers.
# ------------------------------------------------------------------------------
# Sourced by the launch scripts that choose a renderer backend. Kept in bash so
# the choice is made before Python starts, and so `mp_*` helpers stay together.
#
#   mp_is_raspberry_pi      0 on Pi hardware, 1 elsewhere
#   mp_is_low_power_gpu     0 on a Pi or when MP_LOW_POWER_GPU=1
#   mp_pi_generation        echoes 5 4 3 2 1 0, or -1
#   mp_renderer_backend     echoes the backend to use: opengl | vulkan | metal
#   mp_pi_apply_defaults    exports the Pi performance environment defaults
#
# Every function is safe to call on a normal PC and changes nothing there.
# ==============================================================================

# shellcheck shell=bash

_mp_pi_model_cached=""
_mp_pi_model_loaded=0

mp_pi_model() {
    if [ "$_mp_pi_model_loaded" -eq 0 ]; then
        _mp_pi_model_loaded=1
        local model=""
        if [ -r /proc/device-tree/model ]; then
            model="$(tr -d '\000' < /proc/device-tree/model 2>/dev/null)"
        fi
        if [ -z "$model" ] && [ -r /proc/cpuinfo ]; then
            model="$(awk -F: '/^[Mm]odel|^[Hh]ardware/ {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit}' /proc/cpuinfo 2>/dev/null)"
        fi
        case "$(printf '%s' "$model" | tr '[:upper:]' '[:lower:]')" in
            *raspberry*pi*|*bcm27*|*compute*module*) _mp_pi_model_cached="$model" ;;
            *) _mp_pi_model_cached="" ;;
        esac
    fi
    printf '%s' "$_mp_pi_model_cached"
}

mp_is_raspberry_pi() {
    case "${MP_FORCE_RASPBERRY_PI:-}" in
        1|true|yes|on)  return 0 ;;
        0|false|no|off) return 1 ;;
    esac
    [ -n "$(mp_pi_model)" ]
}

mp_pi_generation() {
    local model
    model="$(printf '%s' "$(mp_pi_model)" | tr '[:upper:]' '[:lower:]')"
    case "$model" in
        *"raspberry pi 5"*|*"compute module 5"*|*bcm2712*) printf '5' ;;
        *"raspberry pi 4"*|*"compute module 4"*|*bcm2711*) printf '4' ;;
        *"raspberry pi 3"*|*"compute module 3"*|*bcm2837*|*"zero 2"*) printf '3' ;;
        *"raspberry pi 2"*|*bcm2836*) printf '2' ;;
        *"raspberry pi"*|*bcm2835*) printf '0' ;;
        *) printf '%s' '-1' ;;
    esac
}

mp_is_low_power_gpu() {
    case "${MP_LOW_POWER_GPU:-}" in
        1|true|yes|on)  return 0 ;;
        0|false|no|off) return 1 ;;
    esac
    mp_is_raspberry_pi
}

# Prints the backend the Liquid Player should use. A Pi can never run the
# Vulkan host: liquid_glass_vulkan.py refuses every non-NVIDIA device on
# purpose, so a `vulkan` config on a Pi would open a dead window.
# Accepts an optional configured value as $1.
mp_renderer_backend() {
    local configured="${1:-}"
    configured="$(printf '%s' "$configured" | tr '[:upper:]' '[:lower:]')"
    if mp_is_raspberry_pi; then
        # Apple Metal does not exist here, and Vulkan on V3D is refused by the
        # renderer. OpenGL through Mesa V3D is the only supported path.
        printf 'opengl'
        return 0
    fi
    case "$configured" in
        vulkan|vk|moltenvk) printf 'vulkan' ;;
        metal|mtl)          printf 'metal' ;;
        *)                  printf 'opengl' ;;
    esac
}

# Exports the performance defaults mp_raspberry_pi.py would also apply. Doing
# it here means a shell-launched visualizer gets them before Python imports.
mp_pi_apply_defaults() {
    mp_is_low_power_gpu || return 0
    local gen memory
    gen="$(mp_pi_generation)"
    memory="$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
    local long_edge steps
    if [ "$gen" = "5" ] && { [ "$memory" -eq 0 ] || [ "$memory" -ge 8000 ]; }; then
        long_edge=1280; steps=64
    elif [ "$gen" = "5" ]; then
        long_edge=960;  steps=56
    elif [ "$gen" = "4" ]; then
        long_edge=640;  steps=44
    else
        long_edge=480;  steps=32
    fi
    export MP_VISUALIZER_GL_LONG_EDGE="${MP_VISUALIZER_GL_LONG_EDGE:-$long_edge}"
    export MP_VISUALIZER_GL_MARCH_STEPS="${MP_VISUALIZER_GL_MARCH_STEPS:-$steps}"
    export MP_VISUALIZER_SAFE_PRESET="${MP_VISUALIZER_SAFE_PRESET:-1}"
    # The Bluetooth router forks pactl five times a second. Skip it when the
    # kernel exposes no adapter at all.
    if ! compgen -G "/sys/class/bluetooth/hci*" >/dev/null 2>&1; then
        export MP_DISABLE_BLUETOOTH_MONITOR="${MP_DISABLE_BLUETOOTH_MONITOR:-1}"
    fi
    return 0
}

# A one-line description for launch logs.
mp_pi_summary() {
    if mp_is_raspberry_pi; then
        printf '%s | gen %s | %s MB' \
            "$(mp_pi_model)" "$(mp_pi_generation)" \
            "$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo '?')"
    else
        printf 'Not a Raspberry Pi'
    fi
}
