#!/usr/bin/env bash
# ==============================================================================
# mp_perf_suite.sh - One command that runs the whole performance programme
# ------------------------------------------------------------------------------
# Orchestrates, in order:
#
#   1. mp_perf_bench.py        CPU / memory / render benchmark  (this host)
#   2. mp_benchmark_mode.py    time the 4 shortcuts to fully loaded
#   3. mp_soak_orchestrator.py 4 shortcuts + 20 parallel scripts
#   4. mp_perf_compare.py      comparison against the previous run
#   5. mp_perf_audio_report.py spoken summary (Stephen Hawking voice)
#   6. mp_perf_email.py        email the reports and audio
#
# Cross-host runs bundle the same steps onto each remote machine over SSH and
# pull the JSON back into REPORTS/PERF/.
#
# Usage
#   ./scripts/mp_perf_suite.sh                        # local, full
#   ./scripts/mp_perf_suite.sh --quick                # local, fast
#   ./scripts/mp_perf_suite.sh --remote 192.168.1.13  # one host
#   ./scripts/mp_perf_suite.sh --all-hosts            # every known host
#   ./scripts/mp_perf_suite.sh --all-hosts --quick
#   ./scripts/mp_perf_suite.sh --no-email
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "$PROJECT_ROOT" || exit 1

# Hosts the programme measures. Edit this list, or pass --remote.
MAC_HOST="${MP_MAC_HOST:-192.168.1.138}"
FEDORA_HOST="${MP_FEDORA_HOST:-192.168.1.13}"
FREEBSD_VM_HOST="${MP_FREEBSD_HOST:-192.168.1.16}"

BENCH="${SCRIPT_DIR}/mp_perf_bench.py"
MARK="${SCRIPT_DIR}/mp_benchmark_mode.py"
SOAK="${SCRIPT_DIR}/mp_soak_orchestrator.py"
CMP="${SCRIPT_DIR}/mp_perf_compare.py"
AUDIO="${SCRIPT_DIR}/mp_perf_audio_report.py"
MAIL="${SCRIPT_DIR}/mp_perf_email.py"

QUICK=0
DO_EMAIL=1
DO_AUDIO=1
HOSTS=()
SOAK_SECS=""
STEP_TIMEOUT="${MP_STEP_TIMEOUT:-3600}"

for arg in "$@"; do
    case "$arg" in
        --quick)      QUICK=1 ;;
        --no-email)   DO_EMAIL=0 ;;
        --no-audio)   DO_AUDIO=0 ;;
        --all-hosts)  HOSTS+=("$MAC_HOST" "$FEDORA_HOST" "$FREEBSD_VM_HOST") ;;
        --remote=*)   HOSTS+=("${arg#*=}") ;;
        --soak=*)     SOAK_SECS="${arg#*=}" ;;
        -h|--help)
            sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) ;;
    esac
done
# "--remote host" form.
prev=""
for arg in "$@"; do
    if [ "$prev" = "--remote" ]; then HOSTS+=("$arg"); fi
    prev="$arg"
done

PY="${MP_PYTHON:-python3}"
if [ -x /usr/bin/python3 ]; then PY=/usr/bin/python3; fi

if [ "$QUICK" -eq 1 ]; then
    BENCHEXTRA=(--quick)
    BENCH_SECS="--seconds 2"
    SOAK_DEFAULT=120
else
    BENCHEXTRA=()
    BENCH_SECS=""
    SOAK_DEFAULT=1800
fi
[ -z "$SOAK_SECS" ] && SOAK_SECS="$SOAK_DEFAULT"

STAMP="$(date +%Y%m%d-%H%M%S)"
SUITE_LOG="${PROJECT_ROOT}/REPORTS/PERF/suite-${STAMP}.log"
mkdir -p "$(dirname "$SUITE_LOG")"

log() { printf '%s\n' "$*" | tee -a "$SUITE_LOG"; }
step() {
    log ""
    log "=================================================================="
    log "  $*"
    log "=================================================================="
}

START_EPOCH=$(date +%s)

step "STEP 1/6  CPU / MEMORY / RENDER BENCHMARK"
# shellcheck disable=SC2086
timeout "$STEP_TIMEOUT" "$PY" "$BENCH" "${BENCHEXTRA[@]}" $BENCH_SECS 2>&1 | tee -a "$SUITE_LOG" | tail -40
log "benchmark exit: ${PIPESTATUS[0]}"

step "STEP 2/6  BENCHMARK MODE (time to fully loaded)"
if [ "$QUICK" -eq 1 ]; then MARKARGS=(--quick); else MARKARGS=(); fi
timeout "$STEP_TIMEOUT" "$PY" "$MARK" "${MARKARGS[@]}" 2>&1 | tee -a "$SUITE_LOG" | tail -20
log "benchmark mode exit: ${PIPESTATUS[0]}"

step "STEP 3/6  SOAK + 20 PARALLEL SCRIPTS (${SOAK_SECS}s)"
timeout "$STEP_TIMEOUT" "$PY" "$SOAK" --soak "$SOAK_SECS" --scripts 20 \
    --timeout 90 --seed 42 2>&1 | tee -a "$SUITE_LOG" | tail -20
log "soak exit: ${PIPESTATUS[0]}"

step "STEP 4/6  COMPARISON"
"$PY" "$CMP" 2>&1 | tee -a "$SUITE_LOG" | tail -40
log "compare exit: ${PIPESTATUS[0]}"

if [ "$DO_AUDIO" -eq 1 ]; then
    step "STEP 5/6  SPOKEN REPORT (Stephen Hawking voice)"
    timeout 900 "$PY" "$AUDIO" --voice hawking 2>&1 | tee -a "$SUITE_LOG" | tail -10
    log "audio exit: ${PIPESTATUS[0]}"
fi

if [ "$DO_EMAIL" -eq 1 ]; then
    step "STEP 6/6  EMAIL REPORT"
    timeout 300 "$PY" "$MAIL" --tag "local-${STAMP}" 2>&1 | tee -a "$SUITE_LOG" | tail -20
    log "email exit: ${PIPESTATUS[0]}"
fi

# ------------------------------------------------------------------------------
# Remote hosts: copy the harness, run the same steps there, pull results back.
# ------------------------------------------------------------------------------
run_remote_suite() {
    local host="$1"
    local rdir="~/mp-perf-suite"
    local started
    started=$(date +%s)

    log ""
    log "------------------------------------------------------------------"
    log "  REMOTE: ${host}"
    log "------------------------------------------------------------------"

    if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" "mkdir -p ${rdir}" 2>/dev/null; then
        log "  cannot reach ${host} over SSH — skipping"
        return 1
    fi

    for rel in scripts/mp_perf_bench.py scripts/mp_perf_platform.sh \
               scripts/mp_benchmark_mode.py scripts/mp_benchmark_mode.sh \
               scripts/mp_soak_orchestrator.py scripts/mp_perf_compare.py \
               scripts/mp_perf_audio_report.py; do
        [ -f "$PROJECT_ROOT/$rel" ] || continue
        if scp -q -o BatchMode=yes -o ConnectTimeout=10 \
               "$PROJECT_ROOT/$rel" "$host:${rdir}/" 2>/dev/null; then
            log "  copied $(basename "$rel")"
        else
            log "  FAILED to copy $(basename "$rel")"
        fi
    done

    local soak_arg="--soak ${SOAK_SECS}"
    local tag
    tag="$(ssh -o BatchMode=yes "$host" 'uname -s 2>/dev/null | tr "[:upper:]" "[:lower:]"; uname -m' 2>/dev/null | tr '\n' '-' | sed 's/-$//')"
    [ -z "$tag" ] && tag="remote"

    log "  running benchmark on ${host} (tag ${tag})…"
    # macOS needs the Homebrew interpreter: /usr/bin/python3 has no PyOpenGL.
    # Every other host is happy with python3 from PATH.
    local rpy="python3"
    case "$(ssh -o BatchMode=yes "$host" 'uname -s' 2>/dev/null)" in
        Darwin)
            rpy="$(ssh -o BatchMode=yes "$host" \
                'for p in /opt/homebrew/bin/python3 /usr/local/bin/python3; do [ -x "$p" ] && { echo "$p"; break; }; done' 2>/dev/null)"
            [ -z "$rpy" ] && rpy="python3"
            ;;
    esac
    log "  python on ${host}: ${rpy}"

    ssh -o BatchMode=yes "$host" \
        "cd ${rdir} && ('${rpy}' mp_perf_bench.py --tag '${tag}' --out '${rdir}/perf-${tag}.json' ${BENCHEXTRA[*]:-} 2>&1 | tail -30); \
         echo '--- benchmark mode ---'; ('${rpy}' mp_benchmark_mode.py --tag '${tag}' ${MARKARGS[*]:-} 2>&1 | tail -20); \
         echo '--- soak ---'; ('${rpy}' mp_soak_orchestrator.py ${soak_arg} --scripts 20 --timeout 90 --seed 42 --tag '${tag}' 2>&1 | tail -20)" \
        2>&1 | tee -a "$SUITE_LOG"

    # Pull the JSON results home so the comparison sees every OS.
    mkdir -p "$PROJECT_ROOT/REPORTS/PERF/remote-${tag}"
    scp -q -o BatchMode=yes "$host:${rdir}/*.json" \
        "$PROJECT_ROOT/REPORTS/PERF/remote-${tag}/" 2>/dev/null \
        && log "  results pulled into REPORTS/PERF/remote-${tag}/" \
        || log "  (no JSON results to pull)"

    log "  remote ${host} finished in $(( $(date +%s) - started ))s"
    return 0
}

if [ "${#HOSTS[@]}" -gt 0 ]; then
    step "CROSS-HOST RUNS (${#HOSTS[@]} host(s))"
    for h in "${HOSTS[@]}"; do
        run_remote_suite "$h"
    done

    step "CROSS-OS COMPARISON"
    "$PY" "$CMP" 2>&1 | tee -a "$SUITE_LOG" | tail -60

    if [ "$DO_EMAIL" -eq 1 ]; then
        step "FINAL EMAIL (all hosts)"
        "$PY" "$MAIL" --tag "all-hosts-${STAMP}" 2>&1 | tee -a "$SUITE_LOG" | tail -20
    fi
fi

ELAPSED=$(( $(date +%s) - START_EPOCH ))
step "DONE in ${ELAPSED}s"
log "  suite log : ${SUITE_LOG}"
log "  reports   : ${PROJECT_ROOT}/REPORTS/PERF/"
