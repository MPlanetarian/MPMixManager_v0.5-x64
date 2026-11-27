#!/usr/bin/env bash
# Open the MP Harmony AI Assistant from MP Mix Manager.
# If Ollama is not answering, offer to start it: a systemd user service,
# a distrobox/podman container (the container name is asked for), or a
# plain `ollama serve`. Declining returns straight to the manager.
# The model is the local tag gpt-oss-pinned:latest, which is now the NVIDIA
# Nemotron 3.5 Bolt E4B build (8B total / 4B effective) sized for a 10 GB GPU.
# The tag name is kept for compatibility with existing configs and tooling.
# Shift+A on an empty assistant line returns to the manager.

if [ "${MP_HARMONY_LIB:-0}" = "1" ]; then
    set +e
else
    set -euo pipefail
fi

HARMONY_MODEL="${HARMONY_MODEL:-gpt-oss-pinned:latest}"
HARMONY_PARENT_MODEL="${HARMONY_PARENT_MODEL:-nemotron-3.5-bolt:e4b}"
OLLAMA_API_URL="${OLLAMA_API_URL:-http://127.0.0.1:11434}"
OLLAMA_API_URL="${OLLAMA_API_URL%/}"

OLLAMA_SERVICE="${OLLAMA_SERVICE:-ollama.service}"
OLLAMA_CONTAINER_DEFAULT="${OLLAMA_CONTAINER_DEFAULT:-ollama-container}"
OLLAMA_CONTAINER="${OLLAMA_CONTAINER:-}"
OLLAMA_START_TIMEOUT="${OLLAMA_START_TIMEOUT:-60}"
OLLAMA_CONTAINER_LOG="${OLLAMA_CONTAINER_LOG:-${TMPDIR:-/tmp}/mp-harmony-ollama-serve.log}"

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[0;33m'
CYAN=$'\033[0;36m'
BOLD=$'\033[1m'
NC=$'\033[0m'

# Which interpreter runs the API helper and the agent.
#
# On Windows 10 a bare `python3` resolves to the Microsoft Store alias in
# WindowsApps. That is a stub, not an interpreter: it prints "Python was not
# found" and exits non-zero. Every API call in this script (model load, pull,
# copy) and the agent launch are Python, so with the stub all of them failed
# and a machine with Ollama running and gpt-oss-pinned:latest already
# installed still reported "Ollama did not load the model".
#
# resolve_mp_python.sh is the resolver the rest of the suite already uses: it
# skips the Store alias, prefers the project venv, then py -3, then the real
# installations. MP_PY_BIN from the manager wins when it is set.
_harmony_resolve_python() {
    local bin=""
    if [ -n "${MP_PY_BIN:-}" ]; then
        if "$MP_PY_BIN" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >/dev/null 2>&1; then
            printf '%s\n' "$MP_PY_BIN"
            return 0
        fi
    fi
    if [ -n "${MP_PYTHON:-}" ]; then
        case "$MP_PYTHON" in
            *[Ww]indows[Aa]pps*) ;;
            *)
                if "$MP_PYTHON" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >/dev/null 2>&1; then
                    printf '%s\n' "$MP_PYTHON"
                    return 0
                fi
                ;;
        esac
    fi
    local resolver="${HARMONY_RESOLVER:-}"
    if [ -z "$resolver" ]; then
        local here=""
        here="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)" || here=""
        [ -n "$here" ] && [ -f "$here/resolve_mp_python.sh" ] && resolver="$here/resolve_mp_python.sh"
    fi
    if [ -n "$resolver" ] && [ -f "$resolver" ]; then
        # The resolver prints the interpreter and exports MP_PYTHON.
        bin="$(SCRIPT_DIR="${SCRIPT_DIR:-}" BASE_DIR="${BASE_DIR:-}" bash -c '
            source "$1" >/dev/null 2>&1 || exit 1
            mp_python_bin 2>/dev/null || resolve_mp_python 2>/dev/null
        ' _ "$resolver" 2>/dev/null | tail -n 1)"
        bin="${bin//$'\r'/}"
        if [ -n "$bin" ] && "$bin" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >/dev/null 2>&1; then
            printf '%s\n' "$bin"
            return 0
        fi
    fi
    # Last resort: a python3 that is not the Store alias, then python.
    for bin in python3 python; do
        local found=""
        found="$(type -p "$bin" 2>/dev/null || true)"
        case "$found" in
            ''|*[Ww]indows[Aa]pps*) continue ;;
        esac
        if "$found" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >/dev/null 2>&1; then
            printf '%s\n' "$found"
            return 0
        fi
    done
    return 1
}

HARMONY_PYTHON="$(_harmony_resolve_python 2>/dev/null || true)"
if [ -z "$HARMONY_PYTHON" ]; then
    # Keep the name so the failure below reads as a Python problem and not as
    # a missing model. harmony_api_python reports it before it is used.
    HARMONY_PYTHON="python3"
fi

harmony_say() {
    printf '%b\n' "$*"
}

harmony_wait_enter() {
    [ "${MP_HARMONY_NO_PAUSE:-0}" = "1" ] && return 0
    local _tty=/dev/tty
    [ -r "$_tty" ] || _tty=/dev/stdin
    printf '%b' "${BOLD}Press Enter to return to MP Mix Manager.${NC} "
    read -r _ </dev/tty 2>/dev/null || read -r _ || true
}

harmony_ollama_is_running() {
    if [ "${MP_HARMONY_FORCE_DOWN:-0}" = "1" ]; then
        return 1
    fi
    if [ "${MP_HARMONY_FORCE_UP:-0}" = "1" ]; then
        return 0
    fi
    local body=""
    body="$(curl -sf --connect-timeout 2 --max-time 4 "${OLLAMA_API_URL}/" 2>/dev/null || true)"
    if printf '%s' "$body" | grep -qi "Ollama is running"; then
        return 0
    fi
    return 1
}

harmony_ask() {
    # harmony_ask "prompt" [default] -> stores the answer in HARMONY_ANSWER.
    local prompt="$1" default="${2:-}"
    HARMONY_ANSWER=""
    if [ "${MP_HARMONY_ASSUME_YES:-0}" = "1" ]; then
        HARMONY_ANSWER="${default:-y}"
        return 0
    fi
    if [ "${MP_HARMONY_ASSUME_NO:-0}" = "1" ]; then
        return 0
    fi
    printf '%b' "$prompt"
    if [ -r /dev/tty ]; then
        read -r HARMONY_ANSWER </dev/tty || HARMONY_ANSWER=""
    else
        read -r HARMONY_ANSWER || HARMONY_ANSWER=""
    fi
    [ -z "$HARMONY_ANSWER" ] && HARMONY_ANSWER="$default"
    return 0
}

harmony_confirm_yes() {
    # harmony_confirm_yes "prompt" [default] -> success when the user answers yes.
    harmony_ask "$@"
    case "$HARMONY_ANSWER" in
        y|Y|yes|YES|Yes) return 0 ;;
        *) return 1 ;;
    esac
}

harmony_wait_for_ollama() {
    local deadline=$((SECONDS + OLLAMA_START_TIMEOUT))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if harmony_ollama_is_running; then
            return 0
        fi
        sleep 1
    done
    return 1
}

harmony_systemd_service_exists() {
    command -v systemctl >/dev/null 2>&1 || return 1
    systemctl --user list-unit-files "$OLLAMA_SERVICE" >/dev/null 2>&1 || return 1
    systemctl --user list-unit-files "$OLLAMA_SERVICE" 2>/dev/null | grep -q "^${OLLAMA_SERVICE}"
}

harmony_podman_container_exists() {
    local name="$1"
    [ -n "$name" ] || return 1
    command -v podman >/dev/null 2>&1 || return 1
    podman container exists "$name" >/dev/null 2>&1
}

harmony_container_is_running() {
    local name="$1"
    [ -n "$name" ] || return 1
    command -v podman >/dev/null 2>&1 || return 1
    [ "$(podman inspect -f '{{.State.Running}}' "$name" 2>/dev/null || true)" = "true" ]
}

harmony_start_systemd_service() {
    harmony_say "${BOLD}Starting ${OLLAMA_SERVICE} with systemctl --user...${NC}"
    if systemctl --user start "$OLLAMA_SERVICE" >/dev/null 2>&1; then
        harmony_wait_for_ollama && return 0
        harmony_say "${YELLOW}${OLLAMA_SERVICE} started, but Ollama is not answering yet.${NC}"
        return 1
    fi
    harmony_say "${RED}systemctl --user could not start ${OLLAMA_SERVICE}.${NC}"
    return 1
}

harmony_start_container() {
    local name="$1"
    [ -n "$name" ] || return 1
    command -v distrobox >/dev/null 2>&1 || {
        harmony_say "${RED}distrobox is not installed, so ${name} cannot be entered.${NC}"
        return 1
    }
    if ! harmony_podman_container_exists "$name"; then
        harmony_say "${RED}No podman container named ${name} exists on this machine.${NC}"
        return 1
    fi
    harmony_say "${BOLD}Starting Ollama inside the ${name} container...${NC}"
    if ! harmony_container_is_running "$name"; then
        harmony_say "  Waking the container first."
        podman start "$name" >/dev/null 2>&1 || true
    fi
    : >"$OLLAMA_CONTAINER_LOG" 2>/dev/null || true
    nohup distrobox enter --no-workdir -T "$name" -- env \
        OLLAMA_HOST="0.0.0.0:${OLLAMA_API_URL##*:}" \
        OLLAMA_ORIGINS="*" \
        ollama serve >"$OLLAMA_CONTAINER_LOG" 2>&1 &
    if harmony_wait_for_ollama; then
        return 0
    fi
    harmony_say "${YELLOW}Ollama did not answer from ${name} within ${OLLAMA_START_TIMEOUT}s.${NC}"
    harmony_say "  Log: ${OLLAMA_CONTAINER_LOG}"
    return 1
}

harmony_find_ollama_exe() {
    # Windows installs Ollama as a desktop app, not on the Git Bash PATH, so
    # `command -v ollama` fails even though the server is installed. Look in
    # the per-user and machine install folders the installer uses.
    local cand
    for cand in \
        "${LOCALAPPDATA:-}/Programs/Ollama/ollama.exe" \
        "${PROGRAMFILES:-}/Ollama/ollama.exe" \
        "${PROGRAMFILES:-}/Ollama/ollama app.exe" \
        "/c/Users/${USERNAME:-${USER:-}}/AppData/Local/Programs/Ollama/ollama.exe" \
        "/c/Program Files/Ollama/ollama.exe" \
        "/c/Program Files/Ollama/ollama app.exe"; do
        [ -n "$cand" ] && [ -f "$cand" ] && { printf '%s\n' "$cand"; return 0; }
    done
    local found=""
    found="$(type -p ollama 2>/dev/null || true)"
    [ -n "$found" ] && { printf '%s\n' "$found"; return 0; }
    return 1
}

harmony_is_windows() {
    case "$(uname -s 2>/dev/null)" in
        MINGW*|MSYS*|CYGWIN*) return 0 ;;
    esac
    [ -n "${WINDIR:-}" ] && return 0
    return 1
}

harmony_start_local_serve() {
    local ollama_bin=""
    ollama_bin="$(harmony_find_ollama_exe 2>/dev/null || true)"
    if [ -z "$ollama_bin" ]; then
        harmony_say "${RED}No ollama binary was found on this machine.${NC}"
        if harmony_is_windows; then
            harmony_say "  Install it from ${BOLD}https://ollama.com/download/windows${NC} and try again."
        fi
        return 1
    fi
    if harmony_is_windows; then
        # The Windows build ships "ollama app.exe" (tray server) beside the
        # CLI. Starting the app is what a normal install expects; `ollama
        # serve` also works but shows a console window when started from a
        # shell without a hidden launcher, so prefer the app first.
        local app_bin=""
        app_bin="$(dirname "$ollama_bin")/ollama app.exe"
        if [ -f "$app_bin" ]; then
            harmony_say "${BOLD}Starting the Ollama desktop app...${NC}"
            ( cd "$(dirname "$app_bin")" && cmd.exe /c start "" "ollama app.exe" ) >/dev/null 2>&1 || true
            if harmony_wait_for_ollama; then
                return 0
            fi
            harmony_say "${YELLOW}The Ollama app did not answer in time; trying ollama serve.${NC}"
        fi
        harmony_say "${BOLD}Starting ollama serve in the background...${NC}"
        : >"$OLLAMA_CONTAINER_LOG" 2>/dev/null || true
        ( "$ollama_bin" serve >"$OLLAMA_CONTAINER_LOG" 2>&1 & ) || true
        if harmony_wait_for_ollama; then
            return 0
        fi
        harmony_say "${YELLOW}ollama serve did not answer within ${OLLAMA_START_TIMEOUT}s.${NC}"
        harmony_say "  Log: ${OLLAMA_CONTAINER_LOG}"
        return 1
    fi
    harmony_say "${BOLD}Starting ollama serve in the background...${NC}"
    : >"$OLLAMA_CONTAINER_LOG" 2>/dev/null || true
    nohup "$ollama_bin" serve >"$OLLAMA_CONTAINER_LOG" 2>&1 &
    if harmony_wait_for_ollama; then
        return 0
    fi
    harmony_say "${YELLOW}ollama serve did not answer within ${OLLAMA_START_TIMEOUT}s.${NC}"
    harmony_say "  Log: ${OLLAMA_CONTAINER_LOG}"
    return 1
}

harmony_choose_container() {
    # Sets HARMONY_CONTAINER. Asks for the name unless OLLAMA_CONTAINER is set.
    local candidate="${OLLAMA_CONTAINER:-$OLLAMA_CONTAINER_DEFAULT}"
    HARMONY_CONTAINER="$candidate"
    if [ "${MP_HARMONY_NO_PROMPT:-0}" = "1" ] || [ -n "${OLLAMA_CONTAINER:-}" ]; then
        return 0
    fi
    # Offer a detected container as the default name.
    local running=""
    if command -v podman >/dev/null 2>&1; then
        running="$(podman ps -a --format '{{.Names}}' 2>/dev/null | grep -i ollama | head -n 1 || true)"
        [ -n "$running" ] && candidate="$running"
    fi
    harmony_say ""
    harmony_say "Ollama can run inside a distrobox or podman container."
    harmony_say "Enter the container name so the manager can start it from there."
    harmony_say "  Press Enter for: ${BOLD}${candidate}${NC}"
    harmony_ask "${BOLD}Container name: ${NC}" "$candidate"
    [ -n "$HARMONY_ANSWER" ] && HARMONY_CONTAINER="$HARMONY_ANSWER"
    return 0
}

harmony_model_installed() {
    if [ "${MP_HARMONY_FORCE_MODEL:-}" = "1" ]; then
        return 0
    fi
    if [ "${MP_HARMONY_FORCE_MODEL:-}" = "0" ]; then
        return 1
    fi
    local tags="" bare="${HARMONY_MODEL%:latest}"
    tags="$(curl -sf --connect-timeout 3 --max-time 8 "${OLLAMA_API_URL}/api/tags" 2>/dev/null || true)"
    [ -n "$tags" ] || return 1
    # Ollama reports both "name" and "model" for a tag and has changed which
    # one leads between releases, so match either key. The bare name is also
    # accepted because `gpt-oss-pinned` and `gpt-oss-pinned:latest` are the
    # same tag in Ollama and users type both.
    if printf '%s' "$tags" | grep -F "\"name\":\"${HARMONY_MODEL}\"" >/dev/null 2>&1; then
        return 0
    fi
    if printf '%s' "$tags" | grep -F "\"model\":\"${HARMONY_MODEL}\"" >/dev/null 2>&1; then
        return 0
    fi
    if printf '%s' "$tags" | grep -F "\"name\":\"${bare}\"" >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

harmony_describe_install() {
    cat <<EOF
${BOLD}${CYAN}Ollama is running, but ${HARMONY_MODEL} is not installed.${NC}

${BOLD}You would be installing this Ollama model tag:${NC}
  ${BOLD}${HARMONY_MODEL}${NC}
  About 8 GB on disk.

${BOLD}What the model is${NC}
  NVIDIA Nemotron 3.5 Bolt E4B, a compact model for local coding,
  reasoning, and tool use.
  8 billion total parameters, about 4 billion effective per token.
  Quantized GGUF weights, OpenMDW License 1.1.
  It uses a normal chat template rather than OpenAI Harmony, so the
  assistant translates turns for it automatically.
  Text and image in, text out. Context window up to 128,000 tokens.
  The official Ollama parent is ${HARMONY_PARENT_MODEL}
  (the same weights and the same license).
  If the registry has no tag named ${HARMONY_MODEL}, the installer
  pulls ${HARMONY_PARENT_MODEL} and copies it to ${HARMONY_MODEL}.

${BOLD}Why this model${NC}
  The previous 20.9B gpt-oss model needed about 14 GB of weights, which
  does not fit a 10 GB card such as the RTX 3080. Ollama then silently
  ran it on the CPU at a fraction of the speed. This build fits in VRAM
  and runs fully on the GPU.

${BOLD}What it is used for in MP Mix Manager${NC}
  This is the local brain of the MP Harmony AI Assistant.
  It runs on the Ollama server already listening at ${OLLAMA_API_URL}.
  Your prompts stay on this machine. This is not a cloud chat tab.

${BOLD}What you get with the AI Assistant${NC}
  - Chat in this window. Shift+A on an empty line returns to the manager.
  - Read, write, and patch files, with a backup and a syntax check
    for Python, Bash, and JSON.
  - Shell commands and background jobs, with approval when that mode is on.
  - DJ tools: search the mix archive, read tracklists and cue sheets,
    find a track across mixes, and play, pause, or stop in MP Audio Player.
  - Archive totals: counts, size, and the newest mixes.
  - Web HUD at http://127.0.0.1:11435/ for CPU, memory, GPU, diffs, and jobs.
  - A speech archive and the local voice bridge.
  - An OpenAI-compatible API on port 11435 for Open WebUI.
  - Saved agent memory, git checkpoints, and web search.

EOF
}

harmony_confirm_install() {
    if [ "${MP_HARMONY_ASSUME_YES:-0}" = "1" ]; then
        return 0
    fi
    if [ "${MP_HARMONY_ASSUME_NO:-0}" = "1" ]; then
        return 1
    fi
    harmony_confirm_yes "${BOLD}Install ${HARMONY_MODEL} now? [y/N] ${NC}"
}

harmony_api_python() {
    # The Store alias must never be the interpreter: on Windows 10 `python3`
    # is a stub that exits non-zero, which is what made the model load, pull
    # and copy all fail on a machine where Ollama was already answering.
    if [ -z "${HARMONY_PYTHON:-}" ] || ! "$HARMONY_PYTHON" -c "" >/dev/null 2>&1; then
        harmony_say "${RED}No usable Python 3 was found for the Ollama API calls.${NC}"
        harmony_say "  The Microsoft Store python3 alias is not an interpreter."
        return 1
    fi
    "$HARMONY_PYTHON" - "$@" <<'PY'
import json
import sys
import urllib.error
import urllib.request

base = sys.argv[1].rstrip("/")
action = sys.argv[2]


def post(path, payload, timeout, stream=False):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        print(body.strip() or f"HTTP {exc.code}", file=sys.stderr)
        return exc.code
    if not stream:
        body = resp.read().decode("utf-8", "replace")
        if body.strip():
            print(body.strip())
        return 0
    failed = False
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            print(line, flush=True)
            continue
        if obj.get("error"):
            print(obj["error"], file=sys.stderr, flush=True)
            failed = True
            continue
        status = obj.get("status") or ""
        total = obj.get("total") or 0
        done = obj.get("completed") or 0
        if total and done:
            pct = int(done * 100 / total)
            print(f"{status} {pct}%", flush=True)
        elif status:
            print(status, flush=True)
    return 1 if failed else 0


if action == "pull":
    name = sys.argv[3]
    code = post("/api/pull", {"name": name, "stream": True}, 7200, stream=True)
    sys.exit(code)
if action == "copy":
    source, dest = sys.argv[3], sys.argv[4]
    code = post("/api/copy", {"source": source, "destination": dest}, 120, stream=False)
    sys.exit(0 if code == 0 else 1)
if action == "load":
    name = sys.argv[3]
    payload = {
        "model": name,
        "prompt": ".",
        "stream": False,
        "keep_alive": "30m",
        "options": {"num_predict": 1},
    }
    code = post("/api/generate", payload, 600, stream=False)
    sys.exit(0 if code == 0 else 1)
print(f"unknown action {action}", file=sys.stderr)
sys.exit(2)
PY
}

harmony_install_model() {
    if [ "${MP_HARMONY_SKIP_INSTALL:-0}" = "1" ]; then
        harmony_say "${YELLOW}Install skipped.${NC}"
        return 1
    fi
    harmony_say "${BOLD}Pulling ${HARMONY_MODEL} from Ollama...${NC}"
    if harmony_api_python "$OLLAMA_API_URL" pull "$HARMONY_MODEL"; then
        return 0
    fi
    harmony_say "${YELLOW}${HARMONY_MODEL} is not on the Ollama registry.${NC}"
    harmony_say "${BOLD}Pulling the official parent ${HARMONY_PARENT_MODEL}, then copying it to ${HARMONY_MODEL}.${NC}"
    if ! harmony_api_python "$OLLAMA_API_URL" pull "$HARMONY_PARENT_MODEL"; then
        harmony_say "${RED}The download of ${HARMONY_PARENT_MODEL} failed. ${HARMONY_MODEL} was not installed.${NC}"
        return 1
    fi
    if ! harmony_api_python "$OLLAMA_API_URL" copy "$HARMONY_PARENT_MODEL" "$HARMONY_MODEL"; then
        harmony_say "${RED}Could not copy ${HARMONY_PARENT_MODEL} to ${HARMONY_MODEL}.${NC}"
        return 1
    fi
    return 0
}

harmony_load_model() {
    if [ "${MP_HARMONY_SKIP_LOAD:-0}" = "1" ]; then
        harmony_say "${GREEN}${HARMONY_MODEL} is installed. Load skipped.${NC}"
        return 0
    fi
    harmony_say "${BOLD}Loading ${HARMONY_MODEL} into Ollama memory.${NC}"
    harmony_say "  The 8B model loads into GPU memory in a few seconds."
    if ! harmony_api_python "$OLLAMA_API_URL" load "$HARMONY_MODEL"; then
        harmony_say "${RED}Ollama did not load ${HARMONY_MODEL}. MP Harmony was not started.${NC}"
        return 1
    fi
    harmony_say "${GREEN}${HARMONY_MODEL} is loaded.${NC}"
    return 0
}

harmony_resolve_script() {
    # MP Harmony Agent is a separate project installed at ~/OpenAI/MP_Harmony_Agent
    # by scripts/install_harmony_agent.sh. It is not bundled in this tree.
    local dir=""
    if [ -n "${HARMONY_DIR:-}" ] && [ -f "${HARMONY_DIR}/MP_Harmony_Agent.py" ]; then
        dir="$HARMONY_DIR"
    elif [ -d "$HOME/OpenAI/MP_Harmony_Agent" ]; then
        dir="$HOME/OpenAI/MP_Harmony_Agent"
    elif [ -d "$HOME/OpenAI/Harmony_Agent" ]; then
        dir="$HOME/OpenAI/Harmony_Agent"
    else
        dir="${HARMONY_DIR:-$HOME/OpenAI/MP_Harmony_Agent}"
    fi
    HARMONY_DIR="$dir"
    HARMONY_SCRIPT="${HARMONY_DIR}/MP_Harmony_Agent.py"
    if [ ! -f "$HARMONY_SCRIPT" ] && [ -f "${HARMONY_DIR}/HA.py" ]; then
        HARMONY_SCRIPT="${HARMONY_DIR}/HA.py"
    fi
}

harmony_launch_agent() {
    if [ "${MP_HARMONY_SKIP_AGENT:-0}" = "1" ]; then
        harmony_say "${GREEN}Agent launch skipped.${NC}"
        return 0
    fi
    harmony_resolve_script
    if [ ! -f "${HARMONY_SCRIPT:-}" ]; then
        harmony_say "${RED}The MP Harmony Agent is not installed.${NC}"
        harmony_say "  It is a separate project, expected at ${HARMONY_DIR}."
        harmony_say "  Install it from the manager tree:"
        harmony_say "    ${BOLD}scripts/install_harmony_agent.sh install${NC}"
        harmony_say "  Or re-run ${BOLD}./install.sh${NC} and accept the optional agent."
        return 1
    fi
    harmony_say ""
    harmony_say "${BOLD}${CYAN}MP Harmony AI Assistant${NC}"
    harmony_say "  Model: ${HARMONY_MODEL}"
    harmony_say "  ${YELLOW}Shift+A on an empty line returns to MP Mix Manager.${NC}"
    harmony_say ""
    (
        cd "$HARMONY_DIR" || exit 1
        MP_MIX_MANAGER_SHIFT_A=1 \
            HARMONY_MODEL="$HARMONY_MODEL" \
            "$HARMONY_PYTHON" "$HARMONY_SCRIPT" "$HARMONY_MODEL"
    )
}

harmony_offer_start_ollama() {
    # Returns 0 when Ollama is answering, 2 when the user declines.
    harmony_say ""
    harmony_say "${RED}${BOLD}Ollama is not running, so MP Harmony was not started.${NC}"
    harmony_say ""
    harmony_say "The assistant needs the Ollama service answering at ${OLLAMA_API_URL}."

    local how="serve" name=""
    local has_container_tool=0
    if command -v distrobox >/dev/null 2>&1 && command -v podman >/dev/null 2>&1; then
        has_container_tool=1
    elif harmony_systemd_service_exists; then
        harmony_say ""
        harmony_say "A systemd user service is installed: ${BOLD}${OLLAMA_SERVICE}${NC}"
    fi

    harmony_say ""
    if ! harmony_confirm_yes "${BOLD}Start Ollama now? [y/N] ${NC}"; then
        harmony_say "${YELLOW}Ollama was not started. Returning to MP Mix Manager.${NC}"
        return 2
    fi

    local started=1
    if [ "$has_container_tool" -eq 1 ]; then
        how="container"
        harmony_choose_container
        name="$HARMONY_CONTAINER"
        harmony_start_container "$name" && started=0
        if [ "$started" -ne 0 ] && harmony_systemd_service_exists; then
            harmony_start_systemd_service && started=0
        fi
    elif harmony_systemd_service_exists; then
        how="systemd"
        harmony_start_systemd_service && started=0
    fi
    if [ "$started" -ne 0 ]; then
        harmony_start_local_serve && started=0
    fi
    if [ "$started" -ne 0 ]; then
        harmony_say ""
        harmony_say "${RED}Ollama could not be started, so MP Harmony was not started.${NC}"
        harmony_say "Start Ollama yourself, then choose ${BOLD}AI${NC} again or press ${BOLD}Shift+A${NC}."
        harmony_wait_enter
        return 2
    fi

    harmony_say ""
    harmony_say "${GREEN}Ollama is answering at ${OLLAMA_API_URL}.${NC}"
    return 0
}

harmony_main() {
    printf '\033[0m\033[?25h'
    # The agent is optional and installed outside this tree. Check for it before
    # anything else: the Ollama and model gates below can ask questions and start
    # services, and doing all that only to end with "not installed" is noise for
    # a user who never opted in to MP Harmony.
    harmony_resolve_script
    if [ ! -f "${HARMONY_SCRIPT:-}" ]; then
        harmony_say "${YELLOW}The MP Harmony Agent is not installed, so it cannot be started.${NC}"
        # The path is printed with %s, because harmony_say uses printf %b and a
        # Windows path such as X:\... would turn "\n" into a newline.
        printf '  It is an optional, separate project expected at %s.\n' "$HARMONY_DIR"
        harmony_say "  Install it with: ${BOLD}scripts/install_harmony_agent.sh install${NC}"
        harmony_say "  Or re-run ${BOLD}./install.sh${NC} and accept the optional agent."
        harmony_say "  The rest of MP Mix Manager is unaffected."
        harmony_wait_enter
        return 6
    fi
    if ! harmony_ollama_is_running; then
        local start_rc=0
        harmony_offer_start_ollama || start_rc=$?
        if [ "$start_rc" -ne 0 ]; then
            return "$start_rc"
        fi
    fi

    if ! harmony_model_installed; then
        harmony_say ""
        harmony_describe_install
        if ! harmony_confirm_install; then
            harmony_say "${YELLOW}${HARMONY_MODEL} was not installed. MP Harmony was not started.${NC}"
            harmony_wait_enter
            return 3
        fi
        if ! harmony_install_model; then
            harmony_wait_enter
            return 4
        fi
        if ! harmony_model_installed; then
            harmony_say "${RED}${HARMONY_MODEL} is still not in Ollama after the install.${NC}"
            harmony_wait_enter
            return 4
        fi
    else
        harmony_say ""
        harmony_say "${GREEN}Ollama is running.${NC}"
        harmony_say "${GREEN}${HARMONY_MODEL} is installed.${NC}"
    fi

    if ! harmony_load_model; then
        harmony_wait_enter
        return 5
    fi
    harmony_launch_agent
}

if [ "${MP_HARMONY_LIB:-0}" != "1" ]; then
    harmony_main "$@"
fi
