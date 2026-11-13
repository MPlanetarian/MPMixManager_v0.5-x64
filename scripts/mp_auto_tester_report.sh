#!/usr/bin/env bash
# ==============================================================================
# scripts/mp_auto_tester_report.sh
# Bridge between MP Reporter and the MP Auto Tester report builder.
#
# MP_Reporter.sh runs a task's REPORT_SCRIPT from inside the PDF bucket and
# reads the last line starting with "Report generated:" to find the artifact it
# will narrate. This wrapper finds the newest Auto Tester run, renders the PDF
# and text report, and prints that line.
#
# Usage:
#   scripts/mp_auto_tester_report.sh             # newest run, or a fresh one
#   scripts/mp_auto_tester_report.sh --force      # ignore the once-per-day cache
#   scripts/mp_auto_tester_report.sh /path/to/results.json
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
REPORTS_DIR="$ROOT/REPORTS/MP_AUTO_TESTER"
SHOTS_DIR="$HOME/MP_TESTER_MIX_MANAGER_SCREENSHOTS"
BUILDER="$SCRIPT_DIR/mp_auto_tester_report.py"
TASK_ID="DAILY_REPORT_TASK_005_AUTO_TESTER"

FORCE=0
RESULTS_ARG=""
for arg in "$@"; do
    case "$arg" in
        --force|-f) FORCE=1 ;;
        -*) ;;
        *) [ -z "$RESULTS_ARG" ] && RESULTS_ARG="$arg" ;;
    esac
done

# Pick a Python that has reportlab (report rendering needs it).
PY=""
for cand in "${MP_PYTHON:-}" /usr/bin/python3 python3; do
    [ -n "$cand" ] || continue
    if "$cand" -c 'import reportlab' >/dev/null 2>&1; then
        PY="$cand"
        break
    fi
done
[ -z "$PY" ] && PY="${MP_PYTHON:-python3}"

if [ ! -f "$BUILDER" ]; then
    echo "Error: $BUILDER is missing" >&2
    exit 1
fi

mkdir -p "$REPORTS_DIR" 2>/dev/null || true

# Resolve which run to report on. Walk newest-first and take the first run that
# actually has results; an interrupted run leaves an empty folder behind.
newest_with_results() {
    local dir
    while IFS= read -r dir; do
        [ -n "$dir" ] || continue
        if [ -f "${dir}results.json" ]; then
            printf '%s' "${dir}results.json"
            return 0
        fi
    done < <(ls -1dt "$REPORTS_DIR"/*/ 2>/dev/null)
    return 1
}

RESULTS=""
if [ -n "$RESULTS_ARG" ] && [ -f "$RESULTS_ARG" ]; then
    RESULTS="$RESULTS_ARG"
else
    RESULTS="$(newest_with_results || true)"
fi

# Nothing has been captured yet: run the local sweep so the daily briefing is
# never empty. The full five-system run stays a deliberate, manual action.
if [ -z "$RESULTS" ]; then
    echo "No Auto Tester run found; running the local test sweep for today's report..." >&2
    if [ -x "$ROOT/MP_Auto_Tester_Flash.sh" ]; then
        ( cd "$ROOT" && bash ./MP_Auto_Tester_Flash.sh --local --no-report --no-deploy ) >&2 || true
        RESULTS="$(newest_with_results || true)"
    fi
fi

if [ -z "$RESULTS" ]; then
    echo "Error: no Auto Tester results are available to report" >&2
    exit 1
fi

RUN_DIR="$(dirname "$RESULTS")"
STAMP="$(basename "$RUN_DIR")"

# Monday/Sunday guard: the daily task should stay cheap. If today's report is
# already rendered and --force was not passed, reuse it.
PDF="$RUN_DIR/MP_Auto_Tester_Report_${STAMP}.pdf"
if [ "$FORCE" -eq 0 ] && [ -s "$PDF" ]; then
    echo "Report generated: $PDF"
    TXT="$RUN_DIR/MP_Auto_Tester_Report_${STAMP}.txt"
    [ -s "$TXT" ] && tail -n +1 "$TXT" >&2 || true
    exit 0
fi

SHOT_ARG=()
[ -d "$SHOTS_DIR/$STAMP" ] && SHOT_ARG=(--screenshots "$SHOTS_DIR/$STAMP")

"$PY" "$BUILDER" \
    --results "$RESULTS" \
    --out-dir "$RUN_DIR" \
    --stamp "$STAMP" \
    "${SHOT_ARG[@]}" 2>/dev/null

PDF="$RUN_DIR/MP_Auto_Tester_Report_${STAMP}.pdf"
TXT="$RUN_DIR/MP_Auto_Tester_Report_${STAMP}.txt"
if [ ! -s "$PDF" ]; then
    echo "Error: the Auto Tester PDF was not created" >&2
    exit 1
fi

# Mirror into the MP Reporter daily bucket and the repo's daily report folder so
# the emailer's report collection picks it up alongside the other daily reports.
BUCKET="$HOME/MP_REPORTER/PDF_REPORTS/DAILY_REPORTS"
if [ -d "$HOME/MP_REPORTER/PDF_REPORTS" ]; then
    mkdir -p "$BUCKET" 2>/dev/null || true
    cp -f "$PDF" "$BUCKET/" 2>/dev/null || true
fi
DAILY="$ROOT/REPORTS/DAILY_REPORTS"
if [ -d "$DAILY" ]; then
    cp -f "$PDF" "$DAILY/" 2>/dev/null || true
fi

# A compact spoken transcript for the voice briefing. MP_Reporter prefers the
# PDF text, but a purpose-written script reads far better for a status report.
"$PY" "$BUILDER" --results "$RESULTS" --speech >"$RUN_DIR/MP_Auto_Tester_Report_${STAMP}.speech.txt" 2>/dev/null || true

echo "Report generated: $PDF"
[ -s "$TXT" ] && tail -n +1 "$TXT" >&2 || true
exit 0
