#!/usr/bin/env bash
# ==============================================================================
# mp_perf_platform.sh - Per-OS / per-architecture performance run
# ------------------------------------------------------------------------------
# Runs the MP performance benchmark on THIS machine and stores the result with
# an OS + architecture tag, so runs from different hosts never overwrite each
# other and always know what they were measured on.
#
# Each family gets its own wrapper (see mp_perf_linux.sh, mp_perf_macos.sh,
# mp_perf_freebsd.sh, mp_perf_windows.ps1) because the interpreter, the
# process sampler and the display detection differ:
#
#   Linux   x86_64 / aarch64   /proc sampling, DISPLAY or WAYLAND_DISPLAY
#   macOS   arm64  / x86_64    ps sampling, WindowServer always present
#   FreeBSD amd64              ps sampling, DISPLAY only
#   Windows amd64/arm64        ps via PowerShell, always interactive
#
# Usage:
#   ./mp_perf_platform.sh                     # full run, auto-tagged
#   ./mp_perf_platform.sh --quick
#   ./mp_perf_platform.sh --tag mylabel
#   ./mp_perf_platform.sh --compare
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"

BENCH="${SCRIPT_DIR}/mp_perf_bench.py"
[ ! -f "$BENCH" ] && BENCH="${PROJECT_ROOT}/scripts/mp_perf_bench.py"

if [ ! -f "$BENCH" ]; then
    echo "mp_perf_platform.sh: mp_perf_bench.py not found" >&2
    exit 1
fi

# ------------------------------------------------------------------------------
# Resolve an interpreter. These shortcuts already have a resolver that picks a
# Python able to import their dependencies; reuse it so the benchmark runs on
# the same interpreter the product uses.
# ------------------------------------------------------------------------------
RESOLVER="${SCRIPT_DIR}/resolve_mp_python.sh"
[ ! -f "$RESOLVER" ] && RESOLVER="${PROJECT_ROOT}/scripts/resolve_mp_python.sh"

PY=""
if [ -f "$RESOLVER" ]; then
    # shellcheck disable=SC1090
    . "$RESOLVER"
    if command -v mp_gui_python_bin >/dev/null 2>&1; then
        PY="$(mp_gui_python_bin 2>/dev/null || true)"
    fi
fi
if [ -z "$PY" ]; then
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
    done
fi
if [ -z "$PY" ]; then
    echo "mp_perf_platform.sh: no python3 found" >&2
    exit 1
fi

# ------------------------------------------------------------------------------
# OS + architecture tag. Kept stable so the orchestrator can match a host to
# the wrapper it should run.
# ------------------------------------------------------------------------------
detect_os() {
    case "$(uname -s 2>/dev/null)" in
        Darwin)               echo "macos" ;;
        Linux)                echo "linux" ;;
        FreeBSD|OpenBSD|NetBSD|DragonFly) echo "freebsd" ;;
        MINGW*|MSYS*|CYGWIN*) echo "windows" ;;
        *)                    echo "unknown" ;;
    esac
}

detect_arch() {
    case "$(uname -m 2>/dev/null)" in
        x86_64|amd64|x64)  echo "x86_64" ;;
        aarch64|arm64)     echo "arm64" ;;
        i386|i686|x86)     echo "x86" ;;
        armv7l|armv7)      echo "armv7" ;;
        *)                 echo "$(uname -m 2>/dev/null || echo unknown)" ;;
    esac
}

OS_TAG="$(detect_os)"
ARCH_TAG="$(detect_arch)"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEFAULT_TAG="${OS_TAG}-${ARCH_TAG}"

EXPLICIT_TAG=""
PASSTHROUGH=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag=*)  EXPLICIT_TAG="${1#*=}"; shift ;;
        --tag)    shift
                  if [ "$#" -gt 0 ]; then EXPLICIT_TAG="$1"; shift; fi ;;
        *)        PASSTHROUGH+=("$1"); shift ;;
    esac
done

TAG="${EXPLICIT_TAG:-${DEFAULT_TAG}}"

echo "======================================================================"
echo "  MP PERFORMANCE RUN — ${TAG}"
echo "======================================================================"
echo "  host    : $(hostname 2>/dev/null || echo unknown)"
echo "  os      : ${OS_TAG} (${ARCH_TAG})   $(uname -sr 2>/dev/null)"
echo "  python  : $("$PY" -V 2>&1)"
echo "  output  : ${PROJECT_ROOT}/REPORTS/PERF/${TAG}-${STAMP}.json"
echo "----------------------------------------------------------------------"

RC=0
"$PY" "$BENCH" \
    --tag "${TAG}" \
    --out "${PROJECT_ROOT}/REPORTS/PERF/${TAG}-${STAMP}.json" \
    "${PASSTHROUGH[@]:-}" || RC=$?

if [ "$RC" -ne 0 ]; then
    echo "mp_perf_platform.sh: benchmark exited ${RC}" >&2
fi
exit "$RC"
