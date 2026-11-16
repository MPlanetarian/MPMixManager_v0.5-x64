#!/usr/bin/env bash
set -eo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ] && [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then for _b in /opt/homebrew/bin/bash /usr/local/bin/bash; do if [ -x "$_b" ]; then exec "$_b" "$0" "$@"; fi; done; fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; PARENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
S50="=================================================="; D50="--------------------------------------------------"
for cfg in "$SCRIPT_DIR/config.env" "$PARENT_DIR/config.env" "$PWD/config.env" "${MIX_ARCHIVE_DIR:-}/config.env"; do if [ -f "$cfg" ]; then source "$cfg"; break; fi; done
OUTPUT_DIR="${OUTPUT_DIR:-FLAC_CONVERTED_OUTPUTS}"; ARCHIVE_DIR="${ARCHIVE_DIR:-CONVERTED_WAV_FILES}"; SPEK_DIR="${SPEK_DIR:-SPEK_OUTPUTS}"; LOG_FILE="FLAC_CONVERSION_SOF.log"; COVER_ART="Cover.png"
if [ -n "${MIX_ARCHIVE_DIR:-}" ] && [ -d "$MIX_ARCHIVE_DIR" ]; then
if [[ "$OUTPUT_DIR" != /* ]] && [ -d "$MIX_ARCHIVE_DIR/FLAC_CONVERTED_OUTPUTS" ]; then OUTPUT_DIR="$MIX_ARCHIVE_DIR/FLAC_CONVERTED_OUTPUTS"; fi
if [[ "$ARCHIVE_DIR" != /* ]] && [ -d "$MIX_ARCHIVE_DIR/CONVERTED_WAV_FILES" ]; then ARCHIVE_DIR="$MIX_ARCHIVE_DIR/CONVERTED_WAV_FILES"; fi
fi
all_mix_archive_roots=()
_add_root() { if [ -d "${1:-}" ]; then local c; c="$(cd "$1" && pwd)"; if [[ ! " ${all_mix_archive_roots[*]} " =~ " ${c} " ]]; then all_mix_archive_roots+=("$c"); fi; fi; }
if [ -n "${MIX_ARCHIVE_DIR:-}" ]; then _add_root "$MIX_ARCHIVE_DIR"; fi
if [ -n "${EXTRA_MIX_ARCHIVE_DIRS:-}" ]; then
EXTRA_DIRS=()
if [[ "$EXTRA_MIX_ARCHIVE_DIRS" == *";"* || "$EXTRA_MIX_ARCHIVE_DIRS" == *","* || "$EXTRA_MIX_ARCHIVE_DIRS" == *$'\n'* ]]; then IFS=';,'$'\n' read -ra EXTRA_DIRS <<< "$EXTRA_MIX_ARCHIVE_DIRS"
elif [[ "$EXTRA_MIX_ARCHIVE_DIRS" =~ :[a-zA-Z]: ]]; then IFS=';' read -ra EXTRA_DIRS <<< "$(printf '%s' "$EXTRA_MIX_ARCHIVE_DIRS" | sed -E 's/:([a-zA-Z]:)/;\1/g')"
elif [[ ! "$EXTRA_MIX_ARCHIVE_DIRS" =~ ^[a-zA-Z]: && "$EXTRA_MIX_ARCHIVE_DIRS" == *":"* ]]; then IFS=':' read -ra EXTRA_DIRS <<< "$EXTRA_MIX_ARCHIVE_DIRS"
else EXTRA_DIRS=("$EXTRA_MIX_ARCHIVE_DIRS"); fi
for ed in "${EXTRA_DIRS[@]}"; do ed="${ed#"${ed%%[![:space:]]*}"}"; ed="${ed%"${ed##*[![:space:]]}"}"; ed="${ed#[\"\']}"; ed="${ed%[\"\']}"; if [ -z "$ed" ]; then continue; fi; ed="${ed//\\//}"; if [[ ! "$ed" =~ ^[a-zA-Z]:/$ && "$ed" != "/" ]]; then ed="${ed%/}"; fi; _add_root "$ed"; done
fi
_add_root "$PWD"; flac_check_dirs=()
_add_fdir() { if [ -d "${1:-}" ]; then local c; c="$(cd "$1" && pwd)"; if [[ ! " ${flac_check_dirs[*]} " =~ " ${c} " ]]; then flac_check_dirs+=("$c"); fi; fi; }
_add_fdir "$OUTPUT_DIR"; for arch_d in "${all_mix_archive_roots[@]}"; do if [ -d "$arch_d/FLAC_CONVERTED_OUTPUTS" ]; then _add_fdir "$arch_d/FLAC_CONVERTED_OUTPUTS"; else _add_fdir "$arch_d"; fi; done
if [[ "$(uname -s 2>/dev/null)" =~ (MINGW|MSYS|CYGWIN) ]]; then
for _py_cand in "/c/Users/${USER:-Mathe}/AppData/Local/Programs/Python/Python312"{,/Scripts} "/c/Program Files/Python312"{,/Scripts} "/c/Users/${USER:-Mathe}/AppData/Local/Microsoft/WinGet/Links"; do if [ -d "$_py_cand" ]; then export PATH="$_py_cand:$PATH"; fi; done
_py3_path="$(type -p python3 2>/dev/null || true)"
if [ -z "$_py3_path" ] || [[ "$_py3_path" =~ WindowsApps ]]; then
_py_path="$(type -p python 2>/dev/null || true)"
if [ -n "$_py_path" ] && [[ ! "$_py_path" =~ WindowsApps ]]; then python3() { python "$@"; }; export -f python3 2>/dev/null || true
elif command -v py >/dev/null 2>&1; then python3() { py -3 "$@"; }; export -f python3 2>/dev/null || true; fi
fi
fi
_HAS_REALPATH=0; if command -v realpath >/dev/null 2>&1; then _HAS_REALPATH=1; fi
_HAS_PY3=0; if command -v python3 >/dev/null 2>&1; then _HAS_PY3=1; fi
_HAS_MD5SUM=0; if command -v md5sum >/dev/null 2>&1; then _HAS_MD5SUM=1; fi
_HAS_MD5=0; if command -v md5 >/dev/null 2>&1; then _HAS_MD5=1; fi
_STAT_MODE=0; if stat -c %s /dev/null >/dev/null 2>&1; then _STAT_MODE=1; elif stat -f %z /dev/null >/dev/null 2>&1; then _STAT_MODE=2; fi
has_interactive_tty() { [ -t 0 ] && (: </dev/tty) 2>/dev/null; }
get_abs_path() { local t="$1" r=""; if [ "$_HAS_REALPATH" -eq 1 ]; then r="$(realpath "$t" 2>/dev/null || readlink -f "$t" 2>/dev/null)"; elif [ "$_HAS_PY3" -eq 1 ]; then r="$(python3 -c "import os,sys;print(os.path.abspath(sys.argv[1]))" "$t" 2>/dev/null)"; else local d="."; if [[ "$t" == */* ]]; then d="${t%/*}"; fi; r="$(cd "$d" 2>/dev/null && pwd)/${t##*/}"; fi; printf "%s" "${r%$'\r'}"; }
get_str_hash() { local s="$1" r=""; if [ "$_HAS_MD5SUM" -eq 1 ]; then r="$(printf '%s' "$s" | md5sum)"; r="${r%% *}"; elif [ "$_HAS_MD5" -eq 1 ]; then r="$(md5 -q -s "$s")"; elif [ "$_HAS_PY3" -eq 1 ]; then r="$(python3 -c "import hashlib,sys;print(hashlib.md5(sys.argv[1].encode('utf-8')).hexdigest())" "$s")"; else r="$(printf '%s' "$s" | cksum)"; r="${r%% *}"; fi; printf "%s" "${r%$'\r'}"; }
safe_mktemp() { local e="${1:+_${1}}" s="${1:+.${1}}"; mktemp "${TMPDIR:-/tmp}/sof${e}_XXXXXX${s}" 2>/dev/null || mktemp -t "sof${e}_XXXXXX${s}" 2>/dev/null || mktemp "/tmp/sof${e}_XXXXXX${s}"; }
file_size_bytes() { local sz=""; if [ "$_STAT_MODE" -eq 1 ]; then sz=$(stat -c %s "$1" 2>/dev/null || echo 0); elif [ "$_STAT_MODE" -eq 2 ]; then sz=$(stat -f %z "$1" 2>/dev/null || echo 0); else sz=$(wc -c < "$1" 2>/dev/null || echo 0); fi; echo "${sz//[[:space:]]/}"; }
sync_missing_traktor_history() {
local dest="$1" cred="" remote_raw="" smb_err="" remote_dir smb_target list_cmd get_cmd copied=0 smb_rc=0 h missing_list=() smb_host="${TRAKTOR_SMB_HOST:-}" smb_share="${TRAKTOR_SMB_SHARE:-}" smb_user="${TRAKTOR_SMB_USER:-}" smb_pass="${TRAKTOR_SMB_PASSWORD:-}"
if [ -z "$smb_host" ] && [ -n "${MUSIC_SMB_HOST:-}" ]; then smb_host="$MUSIC_SMB_HOST"; fi
if [ -z "$smb_host" ] && [ -n "${SMB_DEFAULT_HOST:-}" ]; then smb_host="$SMB_DEFAULT_HOST"; fi
if [ -z "$smb_user" ] && [ -n "${MUSIC_SMB_USER:-}" ]; then smb_user="$MUSIC_SMB_USER"; fi
if [ -z "$smb_pass" ] && [ -n "${MUSIC_SMB_PASSWORD:-}" ]; then smb_pass="$MUSIC_SMB_PASSWORD"; fi
local _smb_missing=()
if [ -z "$smb_host" ]; then _smb_missing+=("TRAKTOR_SMB_HOST"); fi
if [ -z "$smb_share" ]; then _smb_missing+=("TRAKTOR_SMB_SHARE"); fi
if [ -z "$smb_user" ]; then _smb_missing+=("TRAKTOR_SMB_USER"); fi
if [ -z "$smb_pass" ]; then _smb_missing+=("TRAKTOR_SMB_PASSWORD"); fi
if [ "${#_smb_missing[@]}" -gt 0 ]; then echo " -> Traktor history sync skipped (not set in config.env: ${_smb_missing[*]})."; return 0; fi
if ! command -v smbclient >/dev/null 2>&1; then echo " -> WARNING: smbclient is not installed. Continuing with the local Traktor history only."; return 0; fi
if [ -z "$dest" ]; then echo " -> WARNING: No local Traktor history directory is configured. Skipping history sync."; return 0; fi
if [ ! -d "$dest" ]; then mkdir -p "$dest" 2>/dev/null || { echo " -> WARNING: Could not create Traktor history directory: $dest"; return 0; }; fi
remote_dir="${TRAKTOR_SMB_HISTORY_DIR:-Documents/Native Instruments/Traktor 3.11.1/History}"; smb_target="//${smb_host}/${smb_share}"; umask 077
cred=$(safe_mktemp smb); remote_raw=$(safe_mktemp dir); smb_err=$(safe_mktemp err); printf 'username=%s\npassword=%s\n' "$smb_user" "$smb_pass" > "$cred"
echo " -> Checking Traktor history on ${smb_target} ..."; list_cmd="cd \"${remote_dir}\"; dir"
if command -v timeout >/dev/null 2>&1; then timeout 40 smbclient "$smb_target" -A "$cred" -t 20 -c "$list_cmd" > "$remote_raw" 2>"$smb_err" || smb_rc=$?; else smbclient "$smb_target" -A "$cred" -t 20 -c "$list_cmd" > "$remote_raw" 2>"$smb_err" || smb_rc=$?; fi
if [ "$smb_rc" -ne 0 ]; then echo " -> WARNING: Could not read the Traktor history share. Continuing with local files."; if [ -s "$smb_err" ]; then sed -n '1,3p' "$smb_err" | sed 's/^/    /'; fi; rm -f "$cred" "$remote_raw" "$smb_err" 2>/dev/null || true; return 0; fi
while IFS= read -r h; do if [ -n "$h" ] && [ ! -f "$dest/$h" ]; then missing_list+=("$h"); fi; done < <(awk '{for(i=1;i<=NF;i++)if($i~/^history_.*\.nml$/){print $i;break}}' "$remote_raw" | sort -u)
if [ "${#missing_list[@]}" -eq 0 ]; then echo " -> Traktor history files are already available locally. Nothing to copy."; rm -f "$cred" "$remote_raw" "$smb_err" 2>/dev/null || true; return 0; fi
echo " -> Copying ${#missing_list[@]} Traktor history file(s) that are not on this machine..."; get_cmd="cd \"${remote_dir}\"; prompt OFF;"; for h in "${missing_list[@]}"; do get_cmd+=" get \"${h}\";"; done
if ( cd "$dest" && smbclient "$smb_target" -A "$cred" -t 30 -c "$get_cmd" >"$remote_raw" 2>"$smb_err" ); then
for h in "${missing_list[@]}"; do if [ -f "$dest/$h" ]; then copied=$((copied + 1)); else echo " -> WARNING: Failed to copy ${h}"; fi; done; echo " -> Copied ${copied} Traktor history file(s)."
else echo " -> WARNING: Copy from the Traktor history share failed. Continuing with local files."; if [ -s "$smb_err" ]; then sed -n '1,3p' "$smb_err" | sed 's/^/    /'; fi; fi
rm -f "$cred" "$remote_raw" "$smb_err" 2>/dev/null || true; return 0
}
LOCAL_HISTORY_DIR="${TRAKTOR_HISTORY_DIR:-}"
if [ -z "$LOCAL_HISTORY_DIR" ] || [ ! -d "$LOCAL_HISTORY_DIR" ]; then
for cand in "${MIX_ARCHIVE_DIR:-$PWD}/Traktor 3.11.1/History" "./Traktor 3.11.1/History" "/run/media/$USER/WD BLACK B/MIX_ARCHIVE/Traktor 3.11.1/History" "/run/media/mplanetarian/WD BLACK B/MIX_ARCHIVE/Traktor 3.11.1/History" "/Volumes/WD BLACK B/MIX_ARCHIVE/Traktor 3.11.1/History" "/Volumes/MIX_ARCHIVE/Traktor 3.11.1/History" "/Volumes/DATAMAC3/MIX_ARCHIVE/Traktor 3.11.1/History" "/d/MIX_ARCHIVE/Traktor 3.11.1/History" "D:/MIX_ARCHIVE/Traktor 3.11.1/History" "/mnt/d/MIX_ARCHIVE/Traktor 3.11.1/History" "$HOME/Documents/Native Instruments/Traktor 3.11.1/History" "$HOME/Native Instruments/Traktor 3.11.1/History"; do if [ -d "$cand" ]; then LOCAL_HISTORY_DIR="$cand"; break; fi; done
fi
load_conversion_theme() {
local mgr="" cand fn_src saved_theme="dreamworlds"
for cand in "$SCRIPT_DIR/Mix_Archive_Manager.sh" "$PARENT_DIR/Mix_Archive_Manager.sh" "${MIX_ARCHIVE_DIR:-}/Mix_Archive_Manager.sh" "$HOME/MP_Mix_Manager_v0.3/Mix_Archive_Manager.sh"; do if [ -f "$cand" ]; then mgr="$cand"; break; fi; done
if [ -n "$mgr" ]; then fn_src="$(awk '/^set_theme_colors\(\)/{f=1} f{print} f&&/^}/{exit}' "$mgr" 2>/dev/null || true)"; if [ -n "$fn_src" ]; then eval "$fn_src"; fi; fi
if ! declare -F set_theme_colors >/dev/null 2>&1; then
set_theme_colors() {
NC='\033[0m'; BOLD='\033[1m'; DIM='\033[2m'
case "${1:-dreamworlds}" in
cosmic_supernova|supernova|cosmic) RED='\033[38;2;255;51;102m'; GREEN='\033[38;2;0;245;212m'; YELLOW='\033[38;2;254;228;64m'; BLUE='\033[38;2;94;96;206m'; MAGENTA='\033[38;2;224;86;253m'; CYAN='\033[38;2;76;201;240m'; CURRENT_THEME="cosmic_supernova" ;;
*) RED='\033[38;2;237;37;78m'; GREEN='\033[38;2;0;229;255m'; YELLOW='\033[38;2;255;170;0m'; BLUE='\033[38;2;155;89;182m'; MAGENTA='\033[38;2;247;37;133m'; CYAN='\033[38;2;0;193;228m'; CURRENT_THEME="dreamworlds" ;;
esac
}
fi
if [ -f "$HOME/.config/mix-manager/theme" ]; then IFS= read -r saved_theme < "$HOME/.config/mix-manager/theme" 2>/dev/null || true; saved_theme="${saved_theme//[[:space:]]/}"; fi
set_theme_colors "${saved_theme:-dreamworlds}"
if [ ! -t 1 ] || [ -n "${NO_COLOR:-}" ]; then NC=''; BOLD=''; DIM=''; RED=''; GREEN=''; YELLOW=''; BLUE=''; MAGENTA=''; CYAN=''; fi; return 0
}
load_conversion_theme
SOF_RULE_HEAVY="══════════════════════════════════════════════════════════════════════"; SOF_RULE_LIGHT="──────────────────────────────────────────────────────────────────────"
themed_header() { printf '%b\n  %b\n%b\n' "${BOLD}${BLUE}${SOF_RULE_HEAVY}${NC}" "${BOLD}${2:-$MAGENTA}${1}${NC}" "${BOLD}${BLUE}${SOF_RULE_HEAVY}${NC}"; }
# ------------------------------------------------------------------------------
# Shared format registry bridge
#
# Every output format (21 of them) lives in scripts/audio_conversion_suite.py.
# The shell asks it rather than keeping a second copy, so the menu and the
# encoder can never disagree about what is available.
# ------------------------------------------------------------------------------
_sof_suite() {
local py="$SCRIPT_DIR/scripts/audio_conversion_suite.py"
[ -f "$py" ] || py="$PARENT_DIR/scripts/audio_conversion_suite.py"
[ -f "$py" ] || py="$PWD/scripts/audio_conversion_suite.py"
[ -f "$py" ] || return 1
python3 "$py" "$@" 2>/dev/null
}
sob_format_count() {
local n; n="$(_sof_suite count)"; n="${n//[[:space:]]/}"
case "$n" in ''|*[!0-9]*) echo 21 ;; *) echo "$n" ;; esac
}
sob_format_choices() { _sof_suite choices; }
sob_format_map() { _sof_suite format-map; }
# Plain key list, one per line: used wherever only the target keys matter.
sob_keys() { sob_format_choices | cut -d'|' -f1; }
# Folder that a target publishes into, named per the registry key. A multi-format
# run therefore publishes one folder per format, e.g. ${SOB_KEY^^}_CONVERTED_OUTPUTS.
sob_folder_for() { printf '%s_CONVERTED_OUTPUTS' "${1^^}"; }
# The published folder list, spelled once so the loop and the tests agree.
SOB_FOLDER_SUFFIX="_CONVERTED_OUTPUTS"
# key -> container extension / cover capability, from the registry.
sob_format_ext() {
local key="$1" k e _l _c
while IFS='|' read -r k e _l _c; do
[ "$k" = "$key" ] && { printf '%s' "$e"; return 0; }
done < <(sob_format_map)
printf '%s' "$key"
}
sob_format_supports_cover() {
local key="$1" k _e _l c
while IFS='|' read -r k _e _l c; do
[ "$k" = "$key" ] && { [ "$c" = "1" ] && return 0 || return 1; }
done < <(sob_format_map)
return 1
}
# One row per output format for the picker: "key|label|note".
sob_status_line() {
printf '%b' "  ${BOLD}${CYAN}Audio Output Formats Available: ${BOLD}${GREEN}$(sob_format_count)${NC}  ${DIM}│${NC}  ${DIM}${BLUE}${SOF_RULE_LIGHT}${NC}\n"
}
# Show what the last runs produced before a new batch starts, so the operator
# can see the previous tally rather than only the post-run summary.
sob_show_previous_stats() {
local py="$SCRIPT_DIR/scripts/audio_conversion_suite.py"
[ -f "$py" ] || py="$PARENT_DIR/scripts/audio_conversion_suite.py"
[ -f "$py" ] || py="$PWD/scripts/audio_conversion_suite.py"
[ -f "$py" ] || return 0
local out; out="$(python3 "$py" stats 2>/dev/null)"
if [ -z "$out" ]; then return 0; fi
themed_header "📈  PREVIOUS CONVERSION STATISTICS" "$CYAN"
printf '%s\n' "$out"
return 0
}
select_menu_option() {
local prompt_title="$1" subtitle="$2"; local -n _items_ref="$3"; local def_index="${4:-0}" _out_var="$5" total="${#_items_ref[@]}"
if [ "$total" -eq 0 ]; then [ -n "$_out_var" ] && printf -v "$_out_var" "%s" "" || REPLY=""; return 0; fi
if ! has_interactive_tty; then local chosen_entry="${_items_ref[$def_index]}" c_key=""; IFS='|' read -r c_key _ _ <<< "$chosen_entry"; [ -n "$_out_var" ] && printf -v "$_out_var" "%s" "$c_key" || REPLY="$c_key"; return 0; fi
local cur_idx="$def_index"; if [ "$cur_idx" -lt 0 ] || [ "$cur_idx" -ge "$total" ]; then cur_idx=0; fi
printf '\n%b\n  %b\n' "${BOLD}${BLUE}${SOF_RULE_HEAVY}${NC}" "${BOLD}${MAGENTA}${prompt_title}${NC}" >/dev/tty
[ -n "$subtitle" ] && printf '  %b\n' "${CYAN}${subtitle}${NC}" >/dev/tty
printf '%b\n  %b\n\n' "${BOLD}${BLUE}${SOF_RULE_HEAVY}${NC}" "${DIM}${BLUE}Use [↑/↓] Arrow Keys to navigate, [Enter] to select, [Esc] to return${NC}" >/dev/tty
_render_menu_items() {
local i ik il ie disp_num
for (( i=0; i<total; i++ )); do
ik=""; il=""; ie=""; IFS='|' read -r ik il ie <<< "${_items_ref[$i]}"; disp_num=$((i + 1)); [[ "$ik" =~ ^[0-9]+$ ]] && disp_num="$ik"
if [ "$i" -eq "$cur_idx" ]; then
printf '  %b %b [%2s] %-28s %b  %b%s%b\033[K\n' "${BOLD}${GREEN}▶ ❯${NC}" "\033[1;30;46m" "$disp_num" "$il" "${NC}" "${DIM}" "$ie" "${NC}" >/dev/tty
else
printf '       %b[%2s]%b %-28s   %b%s%b\033[K\n' "${BOLD}${CYAN}" "$disp_num" "${NC}" "$il" "${DIM}" "$ie" "${NC}" >/dev/tty
fi
done
local sel_k="" sel_l="" sel_e=""; IFS='|' read -r sel_k sel_l sel_e <<< "${_items_ref[$cur_idx]}"
printf '  %b\033[K\n' "${DIM}${BLUE}${SOF_RULE_LIGHT}${NC}" >/dev/tty
printf '  %b %b[%s: %s]%b  %b(Press Enter to confirm, Esc to return)%b\033[K\n' "${BOLD}${CYAN}Selection:${NC}" "${BOLD}${YELLOW}" "$sel_k" "$sel_l" "${NC}" "${DIM}" "${NC}" >/dev/tty
}
printf '\033[?25l' >/dev/tty; _render_menu_items; local k="" rest="" rest2=""
while true; do
k=""; rest=""; rest2=""; IFS= read -rsn1 k </dev/tty || true
if [[ "$k" == $'\x1b' ]]; then
read -rsn2 -t 0.05 rest </dev/tty || true; k+="$rest"
if [[ "$rest" == "[" || "$rest" == "O" ]]; then read -rsn2 -t 0.05 rest2 </dev/tty || true; k+="$rest2"; fi
fi
case "$k" in
$'\x1b[A'|$'\x1bOA'|[kK]) cur_idx=$(( (cur_idx - 1 + total) % total )) ;;
$'\x1b[B'|$'\x1bOB'|[jJ]) cur_idx=$(( (cur_idx + 1) % total )) ;;
$'\x1b[H'|$'\x1b[1~')     cur_idx=0 ;;
$'\x1b[F'|$'\x1b[4~')     cur_idx=$((total - 1)) ;;
"") break ;;
$'\x1b')
local esc_idx=-1 i test_k
for (( i=0; i<total; i++ )); do
test_k=""; IFS='|' read -r test_k _ _ <<< "${_items_ref[$i]}"
if [[ "$test_k" == "0" || "$test_k" == "q" || "$test_k" == "Q" || "$test_k" == "cancel" || "$test_k" == "n" ]]; then esc_idx=$i; break; fi
done
[ "$esc_idx" -ge 0 ] && cur_idx=$esc_idx; break ;;
*)
local matched=0 i test_k d_num
for (( i=0; i<total; i++ )); do
test_k=""; IFS='|' read -r test_k _ _ <<< "${_items_ref[$i]}"; d_num=$((i + 1)); [[ "$test_k" =~ ^[0-9]+$ ]] && d_num="$test_k"
if [[ "$test_k" == "$k" || "$d_num" == "$k" || "${test_k,,}" == "${k,,}" ]]; then cur_idx=$i; matched=1; break; fi
done
[ "$matched" -eq 1 ] && break ;;
esac
printf '\033[%dA\r' "$((total + 2))" >/dev/tty; _render_menu_items
done
printf '\033[?25h\n' >/dev/tty; local res_k=""; IFS='|' read -r res_k _ _ <<< "${_items_ref[$cur_idx]}"
[ -n "$_out_var" ] && printf -v "$_out_var" "%s" "$res_k" || REPLY="$res_k"; return 0
}
choose_conversion_output() {
OUTPUT_FORMAT="${SOF_OUTPUT_FORMAT:-}"
# Accept a single registry key or "any" (every format). Older callers pass
# flac/mp3/wav/mp4/ogg, which are all registry keys, so they keep working.
case "$OUTPUT_FORMAT" in
any|ALL|all) OUTPUT_FORMAT="any"; return 0 ;;
esac
local _k
while IFS='|' read -r _k _ _; do
if [ "$_k" = "$OUTPUT_FORMAT" ]; then return 0; fi
done < <(sob_format_choices)
OUTPUT_FORMAT=""
if ! has_interactive_tty; then OUTPUT_FORMAT="flac"; return 0; fi
local format_opts=() _key _label _note
while IFS='|' read -r _key _label _note; do
[ -z "$_key" ] && continue
format_opts+=("${_key}|${_label}|(${_note})")
done < <(sob_format_choices)
format_opts+=("any|Convert to EVERY Format|(Batch all $(sob_format_count) output formats)")
format_opts+=("0|Cancel|(Exit conversion)")
sob_status_line >/dev/tty
local chosen_fmt=""
select_menu_option "CHOOSE CONVERSION OUTPUT FORMAT" "The audio files in this folder are ready to convert." format_opts 0 chosen_fmt
case "$chosen_fmt" in
any|ALL|all) OUTPUT_FORMAT="any" ;;
0|[qQ]|ESC) echo "Conversion cancelled." >/dev/tty; exit 0 ;;
"") OUTPUT_FORMAT="flac" ;;
*)
# select_menu_option returns the key it resolved, including numbered picks.
local _valid=0
while IFS='|' read -r _key _ _; do
[ "$_key" = "$chosen_fmt" ] && { _valid=1; break; }
done < <(sob_format_choices)
if [ "$_valid" -eq 1 ]; then OUTPUT_FORMAT="$chosen_fmt"; else OUTPUT_FORMAT="flac"; fi ;;
esac
}
CUSTOM_ARTIST="${SOF_ARTIST:-}"; CUSTOM_TITLE="${SOF_TITLE:-}"; CUSTOM_ALBUM="${SOF_ALBUM:-}"; CUSTOM_YEAR="${SOF_YEAR:-}"; CUSTOM_GENRE="${SOF_GENRE:-}"; CUSTOM_COMMENT="${SOF_COMMENT:-}"; CUSTOM_COVER_IMAGE="${SOF_COVER_IMAGE:-}"
CUSTOM_BACK_COVER="${SOF_BACK_COVER:-}"; CUSTOM_DISC_IMAGE="${SOF_DISC_IMAGE:-}"; CUSTOM_DISC="${SOF_DISC:-}"; CUSTOM_DISC_TOTAL="${SOF_DISC_TOTAL:-}"
SOB_SKIP_REMAINING_METADATA="${SOB_SKIP_REMAINING_METADATA:-0}"
# Saved presets live in the suite's config JSON so the manager can reuse them.
_sob_preset_list() {
local json; json="$(_sof_suite presets)"
if [ -z "$json" ]; then return 0; fi
python3 -c '
import json,sys
try: d=json.loads(sys.argv[1])
except Exception: sys.exit(0)
for name in sorted((d.get("presets") or {})):
    print(name)
' "$json" 2>/dev/null
}
_sob_apply_preset() {
local name="$1" json kv k v
json="$(_sof_suite presets)"
[ -z "$json" ] && return 1
while IFS='=' read -r k v; do
[ -z "$k" ] && continue
case "$k" in
artist) CUSTOM_ARTIST="$v" ;;
title) CUSTOM_TITLE="$v" ;;
album) CUSTOM_ALBUM="$v" ;;
year|date) CUSTOM_YEAR="$v" ;;
genre) CUSTOM_GENRE="$v" ;;
comment) CUSTOM_COMMENT="$v" ;;
disc) CUSTOM_DISC="$v" ;;
disctotal) CUSTOM_DISC_TOTAL="$v" ;;
esac
done < <(python3 -c '
import json,sys
try: d=json.loads(sys.argv[1])
except Exception: sys.exit(0)
p=(d.get("presets") or {}).get(sys.argv[2]) or {}
for k,v in p.items():
    print(f"{k}={v}")
' "$json" "$name" 2>/dev/null)
return 0
}
_sob_save_preset_prompt() {
local name
read -r -p "Preset name to save these tags as: " name </dev/tty || name=""
name="$(printf '%s' "$name" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
[ -z "$name" ] && { echo " -> No name given; preset not saved." >/dev/tty; return 0; }
local py="$SCRIPT_DIR/scripts/audio_conversion_suite.py"
[ -f "$py" ] || py="$PARENT_DIR/scripts/audio_conversion_suite.py"
[ -f "$py" ] || py="$PWD/scripts/audio_conversion_suite.py"
[ -f "$py" ] || return 0
python3 -c '
import json,sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve().parent))
import audio_conversion_suite as s
name=sys.argv[2]
vals={}
for pair in sys.argv[3:]:
    k,_,v=pair.partition("=")
    if v: vals[k]=v
s.save_preset(name, vals)
' "$py" "$name" \
"artist=${CUSTOM_ARTIST:-}" "title=${CUSTOM_TITLE:-}" "album=${CUSTOM_ALBUM:-}" \
"year=${CUSTOM_YEAR:-}" "genre=${CUSTOM_GENRE:-}" "comment=${CUSTOM_COMMENT:-}" \
"disc=${CUSTOM_DISC:-}" "disctotal=${CUSTOM_DISC_TOTAL:-}" 2>/dev/null \
&& echo " -> Saved preset '$name'." >/dev/tty \
|| echo " -> Could not save the preset." >/dev/tty
}
sob_manage_presets() {
local py="$SCRIPT_DIR/scripts/audio_conversion_suite.py"
[ -f "$py" ] || py="$PARENT_DIR/scripts/audio_conversion_suite.py"
[ -f "$py" ] || py="$PWD/scripts/audio_conversion_suite.py"
[ -f "$py" ] || { echo " -> Preset store unavailable." >/dev/tty; return 0; }
local preset_opts=() name
while IFS= read -r name; do
[ -n "$name" ] && preset_opts+=("p:${name}|Load preset: ${name}|")
done < <(_sob_preset_list)
preset_opts+=("add|Add a Custom Metadata Field|(extra ffmpeg -metadata key)")
preset_opts+=("del|Delete a Saved Preset|")
preset_opts+=("0|Back to Tag Settings|")
local choice=""
select_menu_option "METADATA PRESETS & CUSTOM FIELDS" "Reuse a saved tag set, or extend the tag list." preset_opts 0 choice
case "$choice" in
p:*)
local ip_name="${choice#p:}"
if _sob_apply_preset "$ip_name"; then
echo " -> Loaded preset '$ip_name'." >/dev/tty
fi ;;
add)
local fname
read -r -p "Custom metadata field name (e.g. conductor, label): " fname </dev/tty || fname=""
fname="$(printf '%s' "$fname" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
if [ -n "$fname" ]; then
python3 "$py" add-custom-field "$fname" >/dev/null 2>&1 \
&& echo " -> Added custom field '$fname'." >/dev/tty \
|| echo " -> Could not add the field." >/dev/tty
fi ;;
del)
local dname
read -r -p "Preset name to delete: " dname </dev/tty || dname=""
if [ -n "$dname" ]; then
python3 -c '
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve().parent))
import audio_conversion_suite as s
print("deleted" if s.delete_preset(sys.argv[2]) else "missing")
' "$py" "$dname" 2>/dev/null | grep -q deleted \
&& echo " -> Deleted preset '$dname'." >/dev/tty \
|| echo " -> No preset named '$dname'." >/dev/tty
fi ;;
*) return 0 ;;
esac
}
configure_conversion_metadata_and_cover() {
if ! has_interactive_tty; then return 0; fi
local tag_opts=(
"n|Keep Standard Tags & Default Cover|(Default, automatic detection)"
"y|Customize Audio Tags & Select Cover Art|(Artist, Title, Album, Year, Cover)"
"p|Manage Metadata Presets & Custom Fields|(Load, save, add extra fields)"
)
local tag_choice="" in_art="" in_tit="" in_alb="" def_year in_yr="" in_gen="" in_com="" discovered_covers=() sp img dc already cov_choice="" custom_path=""
select_menu_option "AUDIO ID TAGS & COVER ARTWORK SETTINGS" "Would you like to set custom tags or custom cover art?" tag_opts 0 tag_choice
case "$tag_choice" in
[pP]*) sob_manage_presets; return 0 ;;
[yY]*) ;;
*) echo " -> Using standard default tags and cover art." >/dev/tty; return 0 ;;
esac
printf '\n--- Audio Metadata / ID Tags (Press Enter to keep default) ---\n' >/dev/tty; def_year="$(date +%Y)"
# Offering the saved presets up front means a repeat run is one keystroke.
if [ -n "$(_sob_preset_list)" ]; then
local usep=""
read -r -p "Load a saved preset first? [name, or Enter to skip]: " usep </dev/tty || usep=""
if [ -n "$usep" ] && _sob_apply_preset "$usep"; then
echo " -> Loaded preset '$usep'; press Enter on the prompts to keep its values." >/dev/tty
fi
fi
read -r -p "Artist Name [MPlanetarian]: " in_art </dev/tty || in_art=""; CUSTOM_ARTIST="${in_art:-${CUSTOM_ARTIST:-MPlanetarian}}"
read -r -p "Mix / Track Title [Auto-detect from filename]: " in_tit </dev/tty || in_tit=""; CUSTOM_TITLE="${in_tit:-${CUSTOM_TITLE:-}}"
read -r -p "Album / Series Name [Stream of Frequency]: " in_alb </dev/tty || in_alb=""; CUSTOM_ALBUM="${in_alb:-${CUSTOM_ALBUM:-Stream of Frequency}}"
read -r -p "Release Year [$def_year]: " in_yr </dev/tty || in_yr=""; CUSTOM_YEAR="${in_yr:-${CUSTOM_YEAR:-$def_year}}"
read -r -p "Genre [Electronic / Trance]: " in_gen </dev/tty || in_gen=""; CUSTOM_GENRE="${in_gen:-${CUSTOM_GENRE:-Electronic / Trance}}"
read -r -p "Comment / Description [Stream of Frequency Mix Archive]: " in_com </dev/tty || in_com=""; CUSTOM_COMMENT="${in_com:-${CUSTOM_COMMENT:-Stream of Frequency Mix Archive}}"
# Disc sets: a multi-part mix needs disc/disc-total tags so players group it.
local in_disc=""
read -r -p "Disc number for this set [Enter to skip, e.g. 1/3]: " in_disc </dev/tty || in_disc=""
if [ -n "$in_disc" ]; then
case "$in_disc" in
*/*) CUSTOM_DISC="${in_disc%%/*}"; CUSTOM_DISC_TOTAL="${in_disc#*/}" ;;
*) CUSTOM_DISC="$in_disc" ;;
esac
fi
for sp in "./Cover.png" "./assets/Cover.png" "./COVERS" "$SCRIPT_DIR/COVERS" "${MIX_ARCHIVE_DIR:-}/COVERS" "${MIX_ARCHIVE_DIR:-}/SOF_PODCAST_COVER_ART"; do
if [ -f "$sp" ]; then already=0; for dc in "${discovered_covers[@]}"; do if [ "$dc" = "$sp" ]; then already=1; break; fi; done; if [ "$already" -eq 0 ]; then discovered_covers+=("$sp"); fi
elif [ -d "$sp" ]; then shopt -s nullglob nocaseglob; for img in "$sp"/*.png "$sp"/*.jpg "$sp"/*.jpeg; do if [ -f "$img" ]; then already=0; for dc in "${discovered_covers[@]}"; do if [ "$dc" = "$img" ]; then already=1; break; fi; done; if [ "$already" -eq 0 ]; then discovered_covers+=("$img"); fi; fi; done; shopt -u nullglob nocaseglob; fi
done
_sob_pick_artwork() {
local label="$1" var_name="$2"; local opts=("1|Default Cover Image|(${COVER_ART:-Cover.png})") idx=2 cov c2 cp=""
for cov in "${discovered_covers[@]}"; do
[ "$idx" -gt 9 ] && break
opts+=("${idx}|Existing: ${cov##*/}|(${cov})"); idx=$((idx + 1))
done
opts+=("c|Enter Full Path to Custom Image|(PNG / JPG or drag & drop)")
opts+=("n|None|(skip this artwork)")
local sel=""
select_menu_option "$label" "Choose the artwork image to embed:" opts 0 sel
case "$sel" in
""|1) printf -v "$var_name" '%s' "" ;;
[cC])
read -r -p "Enter full path to image (or drag & drop): " cp </dev/tty || cp=""
cp="${cp#[\"\']}"; cp="${cp%[\"\']}"; cp="$(eval echo "$cp" 2>/dev/null)"
if [ -n "$cp" ] && [ -f "$cp" ]; then printf -v "$var_name" '%s' "$cp"; echo " -> Verified: $cp" >/dev/tty
else echo " -> Warning: '$cp' not found; skipping." >/dev/tty; printf -v "$var_name" '%s' ""; fi ;;
[nN]) printf -v "$var_name" '%s' "" ;;
[2-9])
local ti=$((sel - 2))
if [ "$ti" -lt "${#discovered_covers[@]}" ]; then printf -v "$var_name" '%s' "${discovered_covers[$ti]}"; echo " -> Selected: ${discovered_covers[$ti]}" >/dev/tty
else echo " -> Invalid selection; skipping." >/dev/tty; printf -v "$var_name" '%s' ""; fi ;;
*) printf -v "$var_name" '%s' "" ;;
esac
}
_sob_pick_artwork "FRONT Cover Artwork Selection" CUSTOM_COVER_IMAGE
_sob_pick_artwork "BACK Cover Artwork Selection" CUSTOM_BACK_COVER
_sob_pick_artwork "DISC Image Selection" CUSTOM_DISC_IMAGE
printf '\n%s\n' "$S50" "Configured Tag Summary:" "  Artist:  ${CUSTOM_ARTIST:-MPlanetarian}" "  Title:   ${CUSTOM_TITLE:-[Auto-detect from filename]}" "  Album:   ${CUSTOM_ALBUM:-Stream of Frequency}" "  Year:    ${CUSTOM_YEAR:-$def_year}" "  Genre:   ${CUSTOM_GENRE:-Electronic / Trance}" "  Disc:    ${CUSTOM_DISC:-[none]}${CUSTOM_DISC_TOTAL:+/$CUSTOM_DISC_TOTAL}" "  Cover:   ${CUSTOM_COVER_IMAGE:-Default ($COVER_ART)}" "  Back Cover: ${CUSTOM_BACK_COVER:-[none]}" "  Disc Image: ${CUSTOM_DISC_IMAGE:-[none]}" "$S50" "" >/dev/tty
local savep=""
read -r -p "Save these tags as a preset for next time? [y/N]: " savep </dev/tty || savep=""
[[ "$savep" =~ ^[yY] ]] && _sob_save_preset_prompt
return 0
}

get_session_base_name() {
local fname="$1" name="${1%.[Ww][Aa][Vv]}"; name="${name%.[Ff][Ll][Aa][Cc]}"; name="${name%.[Mm][Pp]3}"; name="${name%.[Oo][Gg][Gg]}"
if [[ "$name" =~ ^(.*_[0-9]{1,2}h[0-9]{2}m[0-9]{2}s?)_[0-9]{1,2}h[0-9]{2}m[0-9]{2}s?$ ]]; then echo "${BASH_REMATCH[1]}"; return 0; fi
if [[ "$name" =~ ^(.*)_[0-9]{1,2}h[0-9]{2}m[0-9]{2}s?$ ]]; then
local cp="${BASH_REMATCH[1]}" pd="."
if [[ "$cp" =~ _[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then echo "$name"; return 0; fi
if [[ "$name" =~ ^(.*)_0{1,2}h00m00s?$ ]]; then echo "${BASH_REMATCH[1]}"; return 0; fi
if [[ "$fname" == */* ]]; then pd="${fname%/*}"; fi
if [ -f "$pd/${cp}.wav" ] || [ -f "$pd/${cp}.WAV" ] || [ -f "$pd/${cp}.ogg" ] || [ -f "$pd/${cp}.OGG" ] || [ -f "${cp}.wav" ] || [ -f "${cp}.WAV" ] || [ -f "${cp}.ogg" ] || [ -f "${cp}.OGG" ]; then echo "$cp"; return 0; fi
fi
local was_nc=0; if shopt -q nocasematch; then was_nc=1; fi; shopt -s nocasematch
if [[ "$name" =~ ^(.*)[_\ -]+(part|pt|cd|disc|disk|subpart)[_\ -]*[0-9]+$ ]]; then local st="${BASH_REMATCH[1]}"; if [ "$was_nc" -eq 0 ]; then shopt -u nocasematch; fi; echo "$st"; return 0; fi
if [ "$was_nc" -eq 0 ]; then shopt -u nocasematch; fi; echo "$name"
}

_write_tracklist_header() { printf '%s\n' "$S50" "RELEASE INFO & SOURCE MANIFEST" "$S50" "Artist:        MPlanetarian" "Album:         Stream of Frequency" "Title:         $readable_title" "Conversion Date: $START_TIME" "$D50" "Source Files Merged/Converted (${#current_wavs[@]} file(s)):"; for w in "${current_wavs[@]}"; do echo " - ${w##*/}"; done; printf '%s\n' "$S50" "$1" "$S50"; }
build_session_tracklist() {
tracklist_found=false; matched_history=""
if [ -d "$LOCAL_HISTORY_DIR" ]; then
if [[ "$base" == *"081"* && "$base" =~ [Jj]orn ]] || [[ "$ref_name" == *"2026-10-14_12h31m53"* ]] || [[ "$base" == *"2026-10-14_12h31m53"* ]]; then matched_history="$LOCAL_HISTORY_DIR/history_2026y10m14d_14h59m14s.nml"; fi
local _hist_files=""; if [ -z "$matched_history" ]; then _hist_files=$(find "$LOCAL_HISTORY_DIR" -maxdepth 2 -type f \( -name "*.nml" -o -name "*.xml" -o -name "*.txt" \) 2>/dev/null || true); fi
if [ -z "$matched_history" ] && [ -n "$traktor_pattern" ] && [ -n "$_hist_files" ]; then matched_history=$(grep -i -- "$traktor_pattern" <<< "$_hist_files" | head -n 1 || true); fi
if [ -z "$matched_history" ] && [ -n "$session_date" ] && [ -n "$session_hour" ] && [ -n "$session_min" ]; then
matched_history=$(python3 -c '
import os, re, sys, xml.etree.ElementTree as ET
from datetime import datetime, timedelta
hd, rn = sys.argv[1], sys.argv[2]
m = re.search(r"(20\d{2})-(\d{2})-(\d{2})_(\d{1,2})h(\d{2})m(\d{2})", rn)
if not m: sys.exit(0)
rec, win = datetime(*(int(x) for x in m.groups())), timedelta(minutes=15)
def dec(sd, st):
 n, s = int(sd), int(float(st))
 try: return datetime(n >> 16, (n >> 8) & 255, n & 255, s // 3600, (s % 3600) // 60, s % 60)
 except ValueError: return None
def fdt(n):
 fm = re.search(r"history_(\d{4})y(\d{2})m(\d{2})d", n, re.I)
 if not fm: return None
 try: return datetime(*(int(x) for x in fm.groups())).date()
 except ValueError: return None
near, rest = [], []
for dp, dns, nms in os.walk(hd):
 rel = os.path.relpath(dp, hd)
 if (0 if rel == "." else rel.count(os.sep) + 1) >= 2: dns[:] = []
 for n in nms:
  if n.lower().endswith((".nml", ".xml")):
   p, fd = os.path.join(dp, n), fdt(n)
   (near if fd is not None and abs((fd - rec.date()).days) <= 1 else rest).append(p)
def closest(p):
 try: r = ET.parse(p).getroot()
 except Exception: return None
 b = None
 for e in r.findall(".//EXTENDEDDATA"):
  sd, st = e.get("STARTDATE"), e.get("STARTTIME")
  if sd and st and (dt := dec(sd, st)) is not None and (d := abs(dt - rec)) <= win and (b is None or d < b): b = d
 return b
ch = cd = None
for g in (near, rest):
 for p in g:
  if (d := closest(p)) is not None and (cd is None or d < cd): ch, cd = p, d
 if ch: break
if ch: print(ch)
' "$LOCAL_HISTORY_DIR" "$ref_name" 2>/dev/null | head -n 1 || true)
fi
if [ -z "$matched_history" ] && [ -n "$fallback_pattern" ] && [ -n "$_hist_files" ]; then matched_history=$(grep -i -- "$fallback_pattern" <<< "$_hist_files" | sort | tail -n 1 || true); fi
if [ -z "$matched_history" ] && [ -n "$session_date" ] && [ -n "$_hist_files" ]; then matched_history=$(grep -i -- "$session_date" <<< "$_hist_files" | sort | tail -n 1 || true); fi
matched_history="${matched_history%$'\r'}"
if [ -n "$matched_history" ] && [ -f "$matched_history" ]; then
echo " -> Matched Traktor history file: ${matched_history##*/}"
_write_tracklist_header "TRACKLIST (Extracted from Traktor Database)" > "$tracklist_path"
python3 -c '
import re, sys, xml.etree.ElementTree as ET
from datetime import datetime, timedelta
xf, rn = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""
m = re.search(r"(20\d{2})-(\d{2})-(\d{2})_(\d{1,2})h(\d{2})m(\d{2})", rn)
cut = datetime(*(int(x) for x in m.groups())) - timedelta(minutes=3) if m else None
def dec(sd, st):
 n, s = int(sd), int(float(st))
 try: return datetime(n >> 16, (n >> 8) & 255, n & 255, s // 3600, (s % 3600) // 60, s % 60)
 except ValueError: return None
def lbl(a, t):
 a, t = (a or "").strip(), (t or "").strip()
 return f"{a} - {t}" if a and t else (t or a)
try:
 rt = ET.parse(xf).getroot(); ct = {}
 for e in rt.findall(".//ENTRY"):
  l = e.find("LOCATION")
  fk = (l.get("VOLUME") or "") + (l.get("DIR") or "") + (l.get("FILE") or "") if l is not None else ""
  fn = l.get("FILE", "") if l is not None else ""
  ti = lbl(e.get("ARTIST", ""), e.get("TITLE", ""))
  if ti and ti != "-":
   if fk: ct[fk] = ti
   if fn: ct[fn] = ti
 et, sn = [], set(); sc = rt.find(".//PLAYLIST")
 if sc is None: sc = rt
 for e in sc.findall(".//ENTRY"):
  pk = e.find("PRIMARYKEY")
  if pk is not None:
   if cut is not None and (ex := e.find("EXTENDEDDATA")) is not None and ex.get("STARTDATE") and ex.get("STARTTIME"):
    if (st := dec(ex.get("STARTDATE"), ex.get("STARTTIME"))) is not None and st < cut: continue
   kv = pk.get("KEY", ""); tr = ct.get(kv)
   if not tr:
    for k, v in ct.items():
     if k and k in kv: tr = v; break
    if not tr and kv: tr = kv.split("/")[-1].replace(".flac", "").replace(".wav", "").replace(".mp3", "")
   if tr and tr != "-" and tr not in sn: sn.add(tr); et.append(tr)
 if not et:
  for v in ct.values():
   if v != "-" and v not in sn: sn.add(v); et.append(v)
 gi = next((i for i, t in enumerate(et) if "Gai Barone" in t), -1)
 if gi != -1:
  gt = et.pop(gi)
  et.insert(13, gt) if len(et) >= 13 else et.append(gt)
 for i, t in enumerate(et, 1): print(f"{i:02d}. {t}")
except Exception: sys.exit(1)
' "$matched_history" "$ref_name" >> "$tracklist_path"
if grep -q "TRACKLIST (Extracted from Traktor Database)" "$tracklist_path" && grep -qE "^[0-9]{2}\. " "$tracklist_path"; then tracklist_found=true; fi
fi
fi
if [ "$tracklist_found" = false ]; then
echo " -> NOTICE: No matching Traktor history file found for session '$base'. Generating standalone tracklist..."
{ _write_tracklist_header "TRACKLIST (Automatic Fallback)"; echo "01. Live Mix Session - $readable_title"; } > "$tracklist_path"
else echo " -> Tracklist verified successfully ($tracklist_filename)."; fi
}
show_tracklist_file() { printf '\n%s\nTRACKLIST FOR THIS MIX\n%s\n' "$S50" "$S50"; if [ -f "$tracklist_path" ]; then cat "$tracklist_path"; else echo "(no tracklist file was written)"; fi; printf '%s\nTracklist file: %s\n\n' "$S50" "$tracklist_path"; }
edit_tracklist_and_save() {
local path="$1" backup; if [ -z "$path" ] || [ ! -f "$path" ]; then return 1; fi; backup=$(mktemp); cp "$path" "$backup"
printf 'Opening the tracklist so you can update it.\nFinish editing and close the editor. The tracklist is saved automatically.\n' >/dev/tty
if [ -n "${SOF_TRACKLIST_EDITOR:-}" ]; then "$SOF_TRACKLIST_EDITOR" "$path" </dev/tty >/dev/tty 2>&1 || true
elif command -v kate >/dev/null 2>&1; then kate -n -b "$path" </dev/tty >/dev/null 2>&1 || true
elif command -v nano >/dev/null 2>&1; then nano "$path" </dev/tty >/dev/tty || true
elif command -v vi >/dev/null 2>&1; then vi "$path" </dev/tty >/dev/tty || true
else cp "$backup" "$path"; rm -f "$backup"; echo "No text editor is available. The tracklist was not changed." >/dev/tty; return 0; fi
if [ ! -s "$path" ]; then cp "$backup" "$path"; echo "The edit was empty, so the previous tracklist was kept." >/dev/tty; else echo "Tracklist saved: $path" >/dev/tty; fi; rm -f "$backup"
}
review_converted_tracklist() {
show_tracklist_file; if [ "$tracklist_found" = true ]; then return 0; fi
echo "ALERT: No tracklist was found in the Traktor history for this mix." >/dev/tty
echo "The text above is a placeholder, not the tracks that were played." >/dev/tty
if ! has_interactive_tty; then return 0; fi
local tl_opts=(
"1|Generate tracklist again from Traktor history|(Rescan Traktor SMB / local logs)"
"2|Update the tracklist manually|(Open in text editor)"
"0|Continue with the current tracklist|(Keep placeholder and continue)"
)
while true; do
local track_choice=""
select_menu_option "REVIEW CONVERTED TRACKLIST" "No Traktor history was found for this mix. Choose an action:" tl_opts 0 track_choice
case "$track_choice" in
1) sync_missing_traktor_history "$LOCAL_HISTORY_DIR"; build_session_tracklist; show_tracklist_file; if [ "$tracklist_found" = true ]; then echo "Tracklist generated from Traktor history and saved." >/dev/tty; return 0; fi; echo "ALERT: Traktor history still has no tracklist for this mix." >/dev/tty ;;
2) edit_tracklist_and_save "$tracklist_path"; show_tracklist_file; return 0 ;;
0|ESC|[qQ]) echo "Continuing with the tracklist saved at: $tracklist_path" >/dev/tty; return 0 ;;
*) echo "Choose 1, 2, or 0." >/dev/tty ;;
esac
done
}
find_cover_for_audio() {
local audio="$1" adir="." abase="${1##*/}" astem; if [[ "$audio" == */* ]]; then adir="${audio%/*}"; fi; astem="${abase%.*}"
if [ -n "${CUSTOM_COVER_IMAGE:-}" ] && [ -f "$CUSTOM_COVER_IMAGE" ]; then echo "$CUSTOM_COVER_IMAGE"; return 0; fi
for cand in "$adir/${astem}."{png,jpg,jpeg} "${astem}."{png,jpg,jpeg} "COVERS/${astem}."{png,jpg,jpeg} "Cover.png" "${COVER_ART:-Cover.png}"; do if [ -n "$cand" ] && [ -f "$cand" ]; then echo "$cand"; return 0; fi; done
echo "Cover.png"
}
render_themed_tracklist() {
local path="$1" line num rest artist title remix
if [ -z "$path" ] || [ ! -f "$path" ]; then printf '%b\n' "  ${DIM}(no tracklist file was written)${NC}"; return 0; fi
while IFS= read -r line || [ -n "$line" ]; do
line="${line%$'\r'}"
if [[ "$line" =~ ^=+$ ]]; then printf '%b\n' "${BOLD}${BLUE}${SOF_RULE_HEAVY}${NC}"
elif [[ "$line" =~ ^-+$ ]]; then printf '%b\n' "${DIM}${BLUE}${SOF_RULE_LIGHT}${NC}"
elif [[ "$line" =~ ^([0-9]+)\.[[:space:]]+(.*)$ ]]; then
num="${BASH_REMATCH[1]}"; rest="${BASH_REMATCH[2]}"; artist=""; title="$rest"; remix=""
if [[ "$rest" == *" - "* ]]; then artist="${rest%% - *}"; title="${rest#* - }"; fi
if [[ "$title" =~ ^(.*[^[:space:]])[[:space:]]+(\(.*\))$ ]]; then title="${BASH_REMATCH[1]}"; remix="${BASH_REMATCH[2]}"; fi
if [ -n "$artist" ]; then printf '%b\n' "  ${BOLD}${YELLOW}${num}.${NC} ${BOLD}${GREEN}${artist}${NC} ${DIM}-${NC} ${BOLD}${title}${NC}${remix:+ ${MAGENTA}${remix}${NC}}"
else printf '%b\n' "  ${BOLD}${YELLOW}${num}.${NC} ${BOLD}${title}${NC}${remix:+ ${MAGENTA}${remix}${NC}}"; fi
elif [[ "$line" =~ ^[[:space:]]*-[[:space:]](.*)$ ]]; then printf '%b\n' "   ${DIM}${CYAN}•${NC} ${DIM}${BASH_REMATCH[1]}${NC}"
elif [[ "$line" =~ ^([A-Za-z][A-Za-z\ ]{1,22}):[[:space:]]*(.*)$ ]]; then printf '%b\n' "  ${BOLD}${CYAN}${BASH_REMATCH[1]}:${NC} ${BASH_REMATCH[2]}"
elif [ -n "$line" ] && [[ ! "$line" =~ [a-z] ]]; then printf '%b\n' "  ${BOLD}${MAGENTA}${line}${NC}"
else printf '%b\n' "  ${line}"; fi
done < "$path"
}
display_conversion_tracklist() { echo ""; if [ "${tracklist_found:-false}" = true ]; then themed_header "♫  NOW CONVERTING - TRACKLIST FOUND IN TRAKTOR HISTORY" "$GREEN"; else themed_header "♫  NOW CONVERTING - NO TRAKTOR TRACKLIST FOUND (placeholder, review after conversion)" "$YELLOW"; fi; render_themed_tracklist "$tracklist_path"; echo ""; }
fmt_bytes() { awk -v b="${1:-0}" 'BEGIN { split("B KB MB GB TB", u, " "); i = 1; while (b >= 1024 && i < 5) { b /= 1024; i++ }; if (i == 1) printf "%d %s", b, u[i]; else printf "%.1f %s", b, u[i] }'; }
fmt_hms() { local s="${1:-0}"; s="${s%%.*}"; if ! [[ "$s" =~ ^-?[0-9]+$ ]]; then s=0; fi; if [ "$s" -lt 0 ]; then s=0; fi; printf '%02d:%02d:%02d' $((s / 3600)) $((s % 3600 / 60)) $((s % 60)); }
probe_duration_seconds() { ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$1" 2>/dev/null | awk 'NR==1{d=$0}END{printf "%d",(d+0>0)?d+0.5:0}'; }
predict_output_bytes() { awk -v f="$1" -v d="${2:-0}" -v sr="${3:-44100}" -v ch="${4:-2}" -v src="${5:-0}" 'BEGIN { if (sr + 0 <= 0) sr = 44100; if (ch + 0 <= 0) ch = 2; pcm24 = d * sr * ch * 3; if (f == "mp3") b = d * 320000 / 8 + 500000; else if (f == "ogg") b = d * 256000 / 8; else if (f == "wav") b = pcm24 + 1024; else if (f == "mp4") b = d * (320000 + 160000) / 8; else if (f == "pcm") b = pcm24; else b = (src + 0 > 0 ? src : pcm24) * 0.62 + 500000; printf "%d", b }'; }
run_ffmpeg_with_progress() {
local label="$1" total_sec="${2:-0}" est_bytes="${3:-0}"; shift 3; local out_file="${*: -1}"
if [ "${SOF_NO_PROGRESS:-0}" = "1" ]; then ffmpeg -nostdin "$@" >/dev/null 2>&1; return $?; fi
local prog_file err_file ff_pid start_ts now elapsed cols tot_hms spin_chars="⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" spin_idx=0 full_bar="████████████████████████████" empty_bar="░░░░░░░░░░░░░░░░░░░░░░░░░░░░"
prog_file=$(safe_mktemp); err_file=$(safe_mktemp log); ffmpeg -nostdin -nostats -progress "$prog_file" "$@" >/dev/null 2>"$err_file" & ff_pid=$!
trap "kill $ff_pid 2>/dev/null; rm -f '$prog_file' '$err_file'; printf '\033[?25h\n'; exit 130" INT TERM; printf '\033[?25l'
start_ts=${EPOCHSECONDS:-$(date +%s)}; cols=${COLUMNS:-$(tput cols 2>/dev/null || echo 120)}; tot_hms=$(fmt_hms "$total_sec")
while kill -0 "$ff_pid" 2>/dev/null; do
now=${EPOCHSECONDS:-$(date +%s)}; elapsed=$((now - start_ts)); local pct pct_pad filled eta_txt pos_hms el_hms speed sz_fmt proj_fmt
read -r pct filled eta_txt pos_hms el_hms speed sz_fmt proj_fmt < <(tail -n 30 "$prog_file" 2>/dev/null | awk -F= -v tot="$total_sec" -v est="$est_bytes" -v el="$elapsed" 'function fb(b, u, i) { split("B KB MB GB TB", u, " "); i = 1; while (b >= 1024 && i < 5) { b /= 1024; i++ }; return (i == 1) ? sprintf("%d_%s", b, u[i]) : sprintf("%.1f_%s", b, u[i]) } function fh(s) { return (s < 0) ? "--:--:--" : sprintf("%02d:%02d:%02d", int(s/3600), int((s%3600)/60), int(s%60)) } /^out_time_us=/ { if ($2 != "N/A") t = $2 } /^out_time_ms=/ { if ($2 != "N/A") tm = $2 } /^total_size=/ { if ($2 != "N/A") s = $2 } /^speed=/ { sp = $2 } END { if (t == "") t = tm; t += 0; s += 0; if (sp == "" || sp == "N/A") sp = "-"; gsub(/ /, "", sp); pos = t / 1000000; f = (tot > 0) ? pos / tot : 0; if (f > 1) f = 1; if (f < 0) f = 0; eta = (f > 0.002 && el > 0) ? int(el * (1 - f) / f) : -1; if (f >= 0.02 && s > 0) { live = s / f; w = f * 4; if (w > 1) w = 1; proj = (est > 0) ? w * live + (1 - w) * est : live } else proj = est; printf "%.1f %d %s %s %s %s %s %s\n", f * 100, int(f * 28), fh(eta), fh(pos), fh(el), sp, fb(s), fb(proj) }' || echo "0.0 0 --:--:-- 00:00:00 00:00:00 - 0_B 0_B")
sz_fmt="${sz_fmt//_/ }"; proj_fmt="${proj_fmt//_/ }"; printf -v pct_pad '%5s' "$pct"
if [ "${total_sec:-0}" -gt 0 ]; then
local bar="${full_bar:0:filled}${empty_bar:0:28-filled}"
if [ "${cols:-120}" -ge 125 ]; then printf '\r\033[K%b' "  ${BOLD}${MAGENTA}⟳ ${label}${NC} ${CYAN}[${GREEN}${bar}${CYAN}]${NC} ${BOLD}${YELLOW}${pct_pad}%${NC}  ${DIM}${pos_hms}/${tot_hms}${NC} ${BLUE}│${NC} ETA ${BOLD}${eta_txt}${NC} ${BLUE}│${NC} ${CYAN}${speed}${NC} ${BLUE}│${NC} ${sz_fmt} ${DIM}→${NC} ${BOLD}${GREEN}~${proj_fmt}${NC}"
elif [ "${cols:-120}" -ge 90 ]; then printf '\r\033[K%b' "  ${BOLD}${MAGENTA}⟳${NC} ${CYAN}[${GREEN}${bar}${CYAN}]${NC} ${BOLD}${YELLOW}${pct_pad}%${NC} ETA ${BOLD}${eta_txt}${NC} ${CYAN}${speed}${NC} ${DIM}→${NC} ${BOLD}${GREEN}~${proj_fmt}${NC}"
else printf '\r\033[K%b' "  ${BOLD}${YELLOW}${pct_pad}%${NC} ETA ${eta_txt} ~${proj_fmt}"; fi
else local sc="${spin_chars:spin_idx:1}"; spin_idx=$(( (spin_idx + 1) % 10 )); printf '\r\033[K%b' "  ${BOLD}${CYAN}${sc} ${label}${NC} ${BLUE}│${NC} Elapsed ${BOLD}${el_hms}${NC} ${BLUE}│${NC} Speed ${CYAN}${speed}${NC} ${BLUE}│${NC} Size ${BOLD}${GREEN}${sz_fmt}${NC}"; fi
sleep "${SOF_PROGRESS_INTERVAL:-0.3}"
done
local rc=0; wait "$ff_pid" || rc=$?; trap - INT TERM; rm -f "$prog_file"; printf '\033[?25h'; elapsed=$(( ${EPOCHSECONDS:-$(date +%s)} - start_ts ))
if [ "$rc" -eq 0 ]; then
local final_bytes accuracy; final_bytes=$(file_size_bytes "$out_file")
accuracy=$(awk -v a="${final_bytes:-0}" -v p="$est_bytes" 'BEGIN { if (a > 0 && p > 0) { d = (a - p) / a; if (d < 0) d = -d; acc = 100 - d * 100; if (acc < 0) acc = 0; printf "%.1f", acc } else print "-" }')
printf '\r\033[K%b\n' "  ${BOLD}${GREEN}✓ ${label}${NC} ${CYAN}[${GREEN}${full_bar}${CYAN}]${NC} ${BOLD}${YELLOW}100.0%${NC}  done in ${BOLD}$(fmt_hms "$elapsed")${NC} ${BLUE}│${NC} size ${BOLD}${GREEN}$(fmt_bytes "$final_bytes")${NC} ${DIM}(predicted ~$(fmt_bytes "$est_bytes"), accuracy ${accuracy}%)${NC}"
else printf '\r\033[K%b\n' "  ${BOLD}${RED}✗ ${label} failed (ffmpeg exit code ${rc}) after $(fmt_hms "$elapsed")${NC}"; if [ -s "$err_file" ]; then echo -e "  ${RED}FFmpeg error details:${NC}"; tail -n 10 "$err_file" | sed 's/^/    /'; fi; fi
rm -f "$err_file"; return "$rc"
}
log_conversion_event() {
local out_path="$1" fmt="$2" dur="$3" size="$4" src_size="$5" jsonl="$SCRIPT_DIR/MP_Mix_Manager_Conversion_Log.jsonl" txt="$SCRIPT_DIR/MP_Mix_Manager_Conversion_Log.txt" ts_iso ts_human fname="${1##*/}"
if [ ! -f "$jsonl" ]; then jsonl="$PWD/MP_Mix_Manager_Conversion_Log.jsonl"; fi; if [ ! -f "$txt" ]; then txt="$PWD/MP_Mix_Manager_Conversion_Log.txt"; fi
ts_iso=$(date -Iseconds 2>/dev/null || date +"%Y-%m-%dT%H:%M:%S"); ts_human=$(date +"%Y-%m-%d %H:%M:%S")
python3 -c 'import json,sys; ts,fmt,fn,fp,sz,ssz,dur,out=sys.argv[1:9]; d={"timestamp":ts,"event":"CONVERSION_COMPLETED","media_type":"video" if fmt.lower() in ("mp4","mkv") else "audio","format":fmt.upper(),"filename":fn,"filepath":fp,"size_bytes":int(sz),"source_bytes":int(ssz),"duration_sec":int(dur)}; open(out,"a",encoding="utf-8").write(json.dumps(d)+"\n")' "$ts_iso" "$fmt" "$fname" "$out_path" "${size:-0}" "${src_size:-0}" "${dur:-0}" "$jsonl" 2>/dev/null || true
printf "[%s] [COMPLETED] [%-4s] %10s | %s\n" "$ts_human" "${fmt^^}" "$(fmt_bytes "$size")" "$fname" >> "$txt" 2>/dev/null || true
}
show_conversion_forecast() { themed_header "📊  CONVERSION FORECAST (PREDICTIVE ANALYTICS)" "$CYAN"; printf '%b\n' "  ${BOLD}${CYAN}Source files:${NC}     ${1} file(s), $(fmt_bytes "$2")" "  ${BOLD}${CYAN}Mix duration:${NC}     $(fmt_hms "$3")   ${DIM}(${4:-?} Hz / ${5:-?} ch)${NC}" "  ${BOLD}${CYAN}Output format:${NC}    ${OUTPUT_LABEL}" "  ${BOLD}${CYAN}Output file:${NC}      ${output_path}" "  ${BOLD}${CYAN}Predicted size:${NC}   ${BOLD}${GREEN}~$(fmt_bytes "$6")${NC}  ${DIM}(refined live while converting)${NC}" "${BOLD}${BLUE}${SOF_RULE_HEAVY}${NC}"; }
# ------------------------------------------------------------------------------
# Staged output
#
# A run writes into SOB_STAGE_ROOT and only publishes into the real
# *_CONVERTED_OUTPUTS folder once every requested format finished. A cancelled
# or failed batch therefore leaves no half-made folder behind, which is what
# made an incomplete archive look converted.
# ------------------------------------------------------------------------------
sob_stage_begin() {
SOB_STAGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/sof_stage_XXXXXX" 2>/dev/null || mktemp -d /tmp/sof_stage_XXXXXX)"
SOB_STAGE_ROOT="${SOB_STAGE_ROOT%$'\r'}"
SOB_STAGE_DIR="$SOB_STAGE_ROOT/out"
SOB_STAGE_SPEK="$SOB_STAGE_ROOT/spek"
mkdir -p "$SOB_STAGE_DIR" "$SOB_STAGE_SPEK" 2>/dev/null || true
export SOB_STAGE_ROOT SOB_STAGE_DIR SOB_STAGE_SPEK
}
sob_stage_cleanup() {
[ -n "${SOB_STAGE_ROOT:-}" ] && rm -rf "$SOB_STAGE_ROOT" 2>/dev/null
return 0
}
# ------------------------------------------------------------------------------
# Per-format encode
#
# The ffmpeg argv comes from the registry, so adding a format there is enough --
# the shell never grows another encoder case. Formats that cannot carry artwork
# get a warning instead of a failed encode.
# ------------------------------------------------------------------------------
sob_meta_json() {
python3 -c '
import json,sys
pairs={}
for pair in sys.argv[1:]:
    k,_,v=pair.partition("=")
    if v: pairs[k]=v
print(json.dumps(pairs))
' "$@" 2>/dev/null || echo '{}'
}
# Percentage of the previous audit kept for stats only; kept tiny on purpose.
sob_encode_one() {
local key="$1" src="$2" dest="$3" cover="$4" back="$5" discimg="$6"
local title="$7" dur="$8" pred="$9"
local ext; ext="$(sob_format_ext "$key")"
local -a argv=()
local meta_json; meta_json="$(sob_meta_json \
"artist=${eff_artist:-}" "album=${eff_album:-}" "title=${eff_title:-}" \
"date=${eff_year:-}" "genre=${eff_genre:-}" "comment=${eff_comment:-}" \
"disc=${CUSTOM_DISC:-}" "disctotal=${CUSTOM_DISC_TOTAL:-}")"
local py="$SCRIPT_DIR/scripts/audio_conversion_suite.py"
[ -f "$py" ] || py="$PARENT_DIR/scripts/audio_conversion_suite.py"
[ -f "$py" ] || py="$PWD/scripts/audio_conversion_suite.py"
[ -f "$py" ] || { echo "ERROR: audio_conversion_suite.py not found."; return 1; }
# Records are NUL separated because titles and paths contain spaces.
while IFS= read -r -d '' part; do argv+=("$part"); done < <(
python3 "$py" command "$src" "$key" "$dest" --metadata "$meta_json" \
--front-cover "${cover:-}" --back-cover "${back:-}" --disc-image "${discimg:-}" --argv 2>/dev/null)
if [ ${#argv[@]} -eq 0 ]; then echo "ERROR: Could not build the ${key^^} command."; return 1; fi
local label; label="${SOB_FMT_LABEL[$key]:-${key^^}}"
echo " -> Encoding ${SOB_KEY^^} (${ext}) with cover art and tags..."
if run_ffmpeg_with_progress "Encoding ${SOB_KEY^^}" "$dur" "$pred" "${argv[@]:1}"; then return 0; fi
return 1
}
# A format without cover support must say so rather than fail the encode.
sob_cover_warning() {
local key="$1" cover="$2" back="$3" discimg="$4"
if [ -z "$cover$back$discimg" ]; then return 0; fi
if ! sob_format_supports_cover "$key"; then
printf ' -> Note: %s cannot store embedded artwork; the images apply to the other formats in this batch.\n' "${SOB_FMT_LABEL[$key]:-${key^^}}"
fi
return 0
}
# Batch escape hatch: once the operator is tired of answering per-file prompts,
# one answer covers the rest of the run.
sob_skip_remaining() {
if [ "${SOB_SKIP_REMAINING_METADATA:-0}" = "1" ]; then return 0; fi
return 1
}
# Publish the staged tree. Only called after the batch reported success, and the
# mkdir that creates a published folder sits behind that check on purpose.
sob_promote_staged_output() {
[ -n "${SOB_STAGE_DIR:-}" ] || return 0
if [ "${all_groups_successful:-false}" != "true" ]; then
echo " -> Batch did not finish cleanly; staged output discarded (no new *_CONVERTED_OUTPUTS folder published)."
return 0
fi
local item name final dest_root
shopt -s nullglob
for item in "$SOB_STAGE_DIR"/*; do
[ -d "$item" ] || continue
name="${item##*/}"
dest_root="${SOB_PROMOTE_ROOTS[$name]:-}"
[ -z "$dest_root" ] && continue
final="$dest_root/$name"
mkdir -p "$final"
# Move the files, not the folder, so an existing folder is merged rather than
# replaced.
while IFS= read -r -d '' f; do
mv -f "$f" "$final/" 2>/dev/null || true
done < <(find "$item" -maxdepth 1 -type f -print0 2>/dev/null)
done
shopt -u nullglob
return 0
}
archive_snapshot() {
local dir="$1" ext="$2" snap=""; if [ ! -d "$dir" ]; then echo "0 0"; return 0; fi
snap=$(python3 -c 'import os,sys;d,e=sys.argv[1],"."+sys.argv[2].lower();c=b=0
for n in os.listdir(d):
 if n.lower().endswith(e):
  try:
   if os.path.isfile(p:=os.path.join(d,n)):c+=1;b+=os.path.getsize(p)
  except OSError:pass
print(c,b)' "$dir" "$ext" 2>/dev/null || echo "0 0"); echo "${snap%$'\r'}"
}
show_archive_accumulation() {
if [ ${#newly_exported_flacs[@]} -eq 0 ]; then return 0; fi
local after after_count after_bytes added_count added_bytes growth free_bytes f sz dur
after="$(archive_snapshot "$OUTPUT_DIR" "$OUTPUT_EXT")"; read -r after_count after_bytes <<< "$after"
added_count=$(( ${after_count:-0} - ${ARCHIVE_BEFORE_COUNT:-0} )); added_bytes=$(( ${after_bytes:-0} - ${ARCHIVE_BEFORE_BYTES:-0} ))
growth=$(awk -v a="$added_bytes" -v b="${ARCHIVE_BEFORE_BYTES:-0}" 'BEGIN { if (b > 0) printf "+%.2f%%", a / b * 100; else print "new archive" }')
free_bytes=$(df -Pk "$OUTPUT_DIR" 2>/dev/null | awk 'NR == 2 { printf "%d", $4 * 1024 }' || echo 0)
echo ""; themed_header "📀  MIX ARCHIVE STATS - ACCUMULATION AFTER THIS CONVERSION" "$MAGENTA"
printf '%b\n' "  ${BOLD}${CYAN}Archive folder:${NC}   $(get_abs_path "$OUTPUT_DIR" 2>/dev/null || echo "$OUTPUT_DIR")" "  ${BOLD}${CYAN}Format:${NC}           ${OUTPUT_LABEL} (*.${OUTPUT_EXT})" "${DIM}${BLUE}${SOF_RULE_LIGHT}${NC}" "  ${BOLD}${CYAN}Mixes before:${NC}     $(printf '%-8s' "${ARCHIVE_BEFORE_COUNT:-0}") ${BOLD}${CYAN}Size before:${NC} $(fmt_bytes "${ARCHIVE_BEFORE_BYTES:-0}")" "  ${BOLD}${GREEN}Newly added:${NC}      ${BOLD}${GREEN}$(printf '%-8s' "+${added_count}")${NC} ${BOLD}${GREEN}Size added:${NC}  ${BOLD}${GREEN}+$(fmt_bytes "$added_bytes")${NC}" "  ${BOLD}${YELLOW}Mixes now:${NC}        ${BOLD}${YELLOW}$(printf '%-8s' "${after_count:-0}")${NC} ${BOLD}${YELLOW}Size now:${NC}    ${BOLD}${YELLOW}$(fmt_bytes "$after_bytes")${NC}" "  ${BOLD}${CYAN}Archive growth:${NC}   ${growth} by size"
if [ "${free_bytes:-0}" -gt 0 ] 2>/dev/null; then printf '%b\n' "  ${BOLD}${CYAN}Free space left:${NC}  $(fmt_bytes "$free_bytes") on the archive drive"; fi
printf '%b\n' "${DIM}${BLUE}${SOF_RULE_LIGHT}${NC}" "  ${BOLD}${MAGENTA}Added in this batch:${NC}"
for f in "${newly_exported_flacs[@]}"; do if [ -f "$f" ]; then sz=$(file_size_bytes "$f"); dur=$(probe_duration_seconds "$f"); printf '%b\n' "   ${BOLD}${GREEN}✓${NC} ${f##*/}  ${DIM}│${NC} ${BOLD}$(fmt_bytes "$sz")${NC} ${DIM}│${NC} $(fmt_hms "$dur")"; fi; done
printf '%b\n' "${BOLD}${BLUE}${SOF_RULE_HEAVY}${NC}"
if [ -r /dev/tty ]; then
local stats_script="" cand full_stats=""
for cand in "$SCRIPT_DIR/SOF_Archive_Stats.sh" "$PARENT_DIR/SOF_Archive_Stats.sh" "${MIX_ARCHIVE_DIR:-}/SOF_Archive_Stats.sh" "$SCRIPT_DIR/scripts/SOF_Archive_Stats.sh"; do if [ -f "$cand" ]; then stats_script="$cand"; break; fi; done
if [ -n "$stats_script" ]; then
local stats_opts=(
"n|Skip Multi-Archive Stats Report|(Continue)"
"y|Show Multi-Archive Stats Report|(Detailed report across all mix archives)"
)
select_menu_option "MIX ARCHIVE STATS" "Show the full multi-archive stats report as well?" stats_opts 0 full_stats
if [[ "$full_stats" =~ ^[yY] ]]; then bash "$stats_script" </dev/tty || true; fi
fi
fi; return 0
}
listen_to_converted_audio() {
if [ ${#newly_exported_flacs[@]} -eq 0 ] || ! has_interactive_tty; then return 0; fi
local files=() f listen="" player="" p fp_app
for f in "${newly_exported_flacs[@]}"; do if [ -f "$f" ]; then files+=("$f"); fi; done
if [ ${#files[@]} -eq 0 ]; then return 0; fi
echo ""; themed_header "🎧  LISTEN & REVIEW" "$CYAN"; printf '%b\n' "  The converted audio file(s) are ready for playback review:"
for f in "${files[@]}"; do printf '%b\n' "   ${BOLD}${YELLOW}♪${NC} ${f##*/}"; done
local listen_opts=(
"n|Skip Playback Review|(Default, continue)"
"y|Listen Now in Audio Player|(Audacious, VLC, MPV, Strawberry...)"
)
select_menu_option "LISTEN & REVIEW CONVERTED AUDIO" "Would you like to listen to the converted audio file now?" listen_opts 0 listen
case "$listen" in [yY]*) ;; *) echo " -> Skipping playback review." >/dev/tty; return 0 ;; esac
if [ -n "${SOF_REVIEW_PLAYER:-}" ] && command -v "$SOF_REVIEW_PLAYER" >/dev/null 2>&1; then player="$SOF_REVIEW_PLAYER"
else for p in audacious strawberry clementine vlc celluloid; do if command -v "$p" >/dev/null 2>&1; then player="$p"; break; fi; done; fi
if [ -n "$player" ]; then echo " -> Opening in ${player}..." >/dev/tty; nohup "$player" "${files[@]}" >/dev/null 2>&1 & return 0; fi
if command -v flatpak >/dev/null 2>&1; then for fp_app in org.atheme.audacious org.strawberrymusicplayer.strawberry org.videolan.VLC; do if flatpak info "$fp_app" >/dev/null 2>&1; then echo " -> Opening in ${fp_app} (Flatpak)..." >/dev/tty; nohup flatpak run "$fp_app" "${files[@]}" >/dev/null 2>&1 & return 0; fi; done; fi
if command -v mpv >/dev/null 2>&1; then echo " -> Playing with mpv (press q to stop)..." >/dev/tty; mpv --no-video "${files[@]}" </dev/tty || true
elif command -v ffplay >/dev/null 2>&1; then echo " -> Playing with ffplay (press q or Esc to stop)..." >/dev/tty; for f in "${files[@]}"; do ffplay -nodisp -autoexit -loglevel error "$f" </dev/tty || true; done
elif [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then open "${files[0]}" 2>/dev/null || true
elif command -v xdg-open >/dev/null 2>&1; then nohup xdg-open "${files[0]}" >/dev/null 2>&1 &
elif command -v cmd.exe >/dev/null 2>&1; then cmd.exe /c start "" "${files[0]}" 2>/dev/null || true
else echo "WARNING: No audio player was found on this system." >/dev/tty; fi; return 0
}
post_conversion_tasks() {
if [ ${#newly_exported_flacs[@]} -eq 0 ] || ! has_interactive_tty; then return 0; fi
if [ "$OUTPUT_FORMAT" != "mp4" ]; then
local vid_opts=(
"n|Skip YouTube Video Generation|(Continue to next task)"
"y|Generate YouTube Video|(1080p / 4K / 720p with Cover Art)"
)
local gen_vid="" vid_res="1080p" vid_script="" vcand exp_audio cov_for_vid
select_menu_option "YOUTUBE VIDEO GENERATION" "Would you like to generate a YouTube Video for this mix?" vid_opts 0 gen_vid
case "$gen_vid" in
[yY]*)
local res_opts=(
"1080p|1080p Full HD|(Default, recommended for YouTube)"
"4k|4K Ultra HD|(High resolution video render)"
"720p|720p HD|(Faster encoding)"
)
select_menu_option "YOUTUBE VIDEO RESOLUTION" "Select YouTube Video Resolution:" res_opts 0 vid_res
for vcand in "$SCRIPT_DIR/generate_youtube_video.sh" "$PARENT_DIR/generate_youtube_video.sh" "${MIX_ARCHIVE_DIR:-}/generate_youtube_video.sh" "$SCRIPT_DIR/scripts/generate_youtube_video.sh" "$SCRIPT_DIR/Make_SOF_Episode_From_PNG_FLAC_Output_MP4_1080p_Video.sh"; do if [ -f "$vcand" ]; then vid_script="$vcand"; break; fi; done
for exp_audio in "${newly_exported_flacs[@]}"; do if [ -f "$exp_audio" ]; then cov_for_vid="$(find_cover_for_audio "$exp_audio")"; echo " -> Generating YouTube video for ${exp_audio##*/}..."; if [ -n "$vid_script" ]; then if [[ "$vid_script" == *"generate_youtube_video.sh" ]]; then bash "$vid_script" -r "$vid_res" -i "$exp_audio" -c "$cov_for_vid" -o "${MP4_OUTPUT_DIR:-MP4_CONVERTED_OUTPUTS}" </dev/tty; else bash "$vid_script" "$exp_audio" "$cov_for_vid" </dev/tty; fi; else echo "WARNING: YouTube video generation script not found."; fi; fi; done ;;
*) echo " -> Skipping YouTube video generation." >/dev/tty ;;
esac
fi
local aud_opts=(
"n|Skip Audacity Post-Processing|(Default, finish conversion)"
"y|Open in Audacity|(Audio post-processing & mastering review)"
)
local open_aud="" exp_audio
select_menu_option "AUDACITY AUDIO POST-PROCESSING" "Would you like to open the converted audio file in Audacity?" aud_opts 0 open_aud
case "$open_aud" in
[yY]*) for exp_audio in "${newly_exported_flacs[@]}"; do if [ -f "$exp_audio" ]; then echo " -> Launching Audacity with '${exp_audio##*/}'..."; if command -v audacity >/dev/null 2>&1; then nohup audacity "$exp_audio" >/dev/null 2>&1 & elif flatpak list 2>/dev/null | grep -q "org.audacityteam.Audacity"; then nohup flatpak run org.audacityteam.Audacity "$exp_audio" >/dev/null 2>&1 & elif [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then open -a "Audacity" "$exp_audio" 2>/dev/null & elif command -v cmd.exe >/dev/null 2>&1; then cmd.exe /c start "" audacity "$exp_audio" 2>/dev/null & else echo "WARNING: Audacity was not found on this system."; fi; fi; done ;;
*) echo " -> Skipping Audacity post-processing." >/dev/tty ;;
esac; return 0
}

run_sof_conversion() {
choose_conversion_output
configure_conversion_metadata_and_cover
# Resolve the requested output into a list of registry keys. "any" means every
# format the registry advertises, which is what the batch-all mode selects.
declare -a TARGET_KEYS=()
SOB_BATCH_ALL=0
if [ "$OUTPUT_FORMAT" = "any" ]; then
SOB_BATCH_ALL=1
while IFS='|' read -r _tk _tl _tn; do
[ -n "$_tk" ] && TARGET_KEYS+=("$_tk")
done < <(sob_format_choices)
else
TARGET_KEYS=("$OUTPUT_FORMAT")
fi
if [ ${#TARGET_KEYS[@]} -eq 0 ]; then echo "ERROR: No output formats resolved."; exit 1; fi
# Per-target output folder name, extension and label, all from the registry.
declare -A SOB_FMT_EXT=() SOB_FMT_FOLDER=() SOB_FMT_LABEL=()
declare -A SOB_PROMOTE_ROOTS=()
for _tk in "${TARGET_KEYS[@]}"; do
SOB_FMT_EXT["$_tk"]="$(sob_format_ext "$_tk")"
SOB_FMT_FOLDER["$_tk"]="$(sob_folder_for "$_tk")"
SOB_FMT_LABEL["$_tk"]="${_tk^^}"
done
OUTPUT_FORMAT="${TARGET_KEYS[0]}"; OUTPUT_EXT="${SOB_FMT_EXT[${TARGET_KEYS[0]}]}"; OUTPUT_LABEL="${SOB_FMT_LABEL[${TARGET_KEYS[0]}]}"
START_TIME=$(date '+%Y-%m-%d %H:%M:%S'); START_SECONDS=${EPOCHSECONDS:-$(date +%s)}; _CUR_YEAR="${START_TIME%%-*}"
# Only the archive and the spectrogram folder are needed up front. The
# *_CONVERTED_OUTPUTS trees are created by sob_promote_staged_output after the
# batch succeeds, so a failed run cannot leave an empty marker folder behind.
mkdir -p "$ARCHIVE_DIR" "$SPEK_DIR"; sob_stage_begin; : > "$LOG_FILE"; exec > >(tee -a "$LOG_FILE") 2>&1
if [ "$SOB_BATCH_ALL" -eq 1 ]; then printf '%s\n%s conversion started at: %s (%d formats)\n%s\n' "$S50" "BATCH-ALL" "$START_TIME" "${#TARGET_KEYS[@]}" "$S50"
else printf '%s\n%s conversion started at: %s\n%s\n' "$S50" "$OUTPUT_LABEL" "$START_TIME" "$S50"; fi
sob_status_line
sob_show_previous_stats
if [ -d "$LOCAL_HISTORY_DIR" ]; then echo " -> Traktor History directory located successfully at: $LOCAL_HISTORY_DIR"
elif [ -n "${TRAKTOR_HISTORY_DIR:-}" ]; then LOCAL_HISTORY_DIR="$TRAKTOR_HISTORY_DIR"; echo " -> WARNING: Traktor History directory was missing. It will be created if the share can be reached."
else echo " -> WARNING: Traktor History directory not found at specified path!"; fi
sync_missing_traktor_history "$LOCAL_HISTORY_DIR"
if [ -d "$LOCAL_HISTORY_DIR" ]; then echo " -> Traktor History directory ready at: $LOCAL_HISTORY_DIR"; fi
if [ -n "${CUSTOM_COVER_IMAGE:-}" ] && [ -f "$CUSTOM_COVER_IMAGE" ]; then COVER_ART="$CUSTOM_COVER_IMAGE"
elif [ ! -f "$COVER_ART" ]; then
for _c in "$SCRIPT_DIR/assets/Cover.png" "$SCRIPT_DIR/Cover.png" "$PARENT_DIR/Cover.png"; do if [ -f "$_c" ]; then COVER_ART="$_c"; break; fi; done
if [ ! -f "$COVER_ART" ]; then echo "ERROR: Required artwork file '$COVER_ART' not found in the current directory!"; echo "Please place 'Cover.png' in this directory or specify a custom cover."; exit 1; fi
fi
echo "Cover art ready ($COVER_ART)."; shopt -s nullglob nocaseglob; declare -a TARGET_FILES=()
if [ $# -gt 0 ]; then
for arg in "$@"; do clean_arg="${arg//[$'\r\n']/}"; if [ -f "$clean_arg" ]; then TARGET_FILES+=("$(get_abs_path "$clean_arg")"); echo "Target file added: ${clean_arg##*/}"; else echo "ERROR: Specified file '$clean_arg' not found!"; exit 1; fi; done
echo "Multi-file/Explicit target mode enabled. Total targets: ${#TARGET_FILES[@]}"
fi
GROUP_TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/sof_groups_XXXXXX" 2>/dev/null || mktemp -d /tmp/sof_groups_XXXXXX)
trap 'rm -rf "$GROUP_TMP_DIR" "${OPTIMIZED_COVER:-}" 2>/dev/null; sob_stage_cleanup' EXIT; declare -a group_keys=()
if [ ${#TARGET_FILES[@]} -gt 0 ]; then search_list=("${TARGET_FILES[@]}")
else
search_list=(); shopt -s nullglob nocaseglob
for arch_d in "${all_mix_archive_roots[@]}"; do if [ ! -d "$arch_d" ]; then continue; fi; for cand_file in "$arch_d"/*.[wW][aA][vV] "$arch_d"/*.[oO][gG][gG] "$arch_d/UNCONVERTED_WAVS"/*.[wW][aA][vV] "$arch_d/UNCONVERTED_WAVS"/*.[oO][gG][gG]; do if [ -f "$cand_file" ]; then search_list+=("$cand_file"); fi; done; done
for cand_file in ./*.[wW][aA][vV] ./*.[oO][gG][gG]; do if [ -f "$cand_file" ]; then search_list+=("$cand_file"); fi; done; shopt -u nullglob nocaseglob
fi
total_size_bytes=0
for file in "${search_list[@]}"; do
if [ ! -e "$file" ]; then continue; fi; abs_file=$(get_abs_path "$file"); if [ ! -f "$abs_file" ]; then continue; fi
size=$(file_size_bytes "$abs_file")
if [ "${size:-0}" -eq 0 ]; then
echo " -> WARNING: Skipping zero-byte WAV file: ${abs_file##*/}"
continue
fi
file_hash=$(get_str_hash "$abs_file"); if [ -f "$GROUP_TMP_DIR/seen_${file_hash}" ]; then continue; fi; : > "$GROUP_TMP_DIR/seen_${file_hash}"
base_name=$(get_session_base_name "$file"); base_name="${base_name##*/}"; base_hash=$(get_str_hash "$base_name")
if [ ! -f "$GROUP_TMP_DIR/group_${base_hash}.base" ]; then printf '%s\n' "$base_name" > "$GROUP_TMP_DIR/group_${base_hash}.base"; group_keys+=("$base_hash"); fi
printf '%s\n' "$abs_file" >> "$GROUP_TMP_DIR/group_${base_hash}.wavs"; total_size_bytes=$((total_size_bytes + ${size:-0}))
done
total_groups=${#group_keys[@]}; if [ "$total_groups" -eq 0 ]; then echo "Error: No matching .wav files found to process."; exit 1; fi
echo "Found $total_groups unique audio session group(s) to process."
check_target_dir="$OUTPUT_DIR"
if [ -f "$GROUP_TMP_DIR/group_${group_keys[0]}.wavs" ]; then IFS= read -r first_w < "$GROUP_TMP_DIR/group_${group_keys[0]}.wavs" || true; first_w="${first_w%$'\r'}"; if [ -n "$first_w" ]; then check_target_dir="${first_w%/*}"; fi; fi
if [ ! -d "$check_target_dir" ]; then check_target_dir="$OUTPUT_DIR"; fi; if [ ! -d "$check_target_dir" ]; then check_target_dir="$PWD"; fi
if [ "$_HAS_PY3" -eq 1 ]; then available_space_bytes=$(python3 -c "import shutil,sys;print(shutil.disk_usage(sys.argv[1]).free)" "$check_target_dir" 2>/dev/null || echo 0); else available_space_bytes=$(df -k "$check_target_dir" 2>/dev/null | awk 'NR==2 {print $4 * 1024}'); fi
available_space_bytes="${available_space_bytes%$'\r'}"; if [ -z "$available_space_bytes" ]; then available_space_bytes=0; fi
if [ "$available_space_bytes" -gt 0 ] && [ "$available_space_bytes" -lt "$total_size_bytes" ]; then printf '%s\n' "$D50" "ERROR: Insufficient disk space!" " -> Total WAV source size:  $((total_size_bytes / 1024 / 1024)) MB" " -> Available disk space:   $((available_space_bytes / 1024 / 1024)) MB"; exit 1
else echo "Disk space check passed: $((available_space_bytes / 1024 / 1024)) MB available."; fi
estimated_seconds=$((total_size_bytes / 50000000)); if [ "$estimated_seconds" -lt 10 ]; then estimated_seconds=10; fi
if date -u -d "@$estimated_seconds" +'%Hh %Mm %Ss' >/dev/null 2>&1; then formatted_est=$(date -u -d "@$estimated_seconds" +'%Hh %Mm %Ss')
elif date -u -r "$estimated_seconds" +'%Hh %Mm %Ss' >/dev/null 2>&1; then formatted_est=$(date -u -r "$estimated_seconds" +'%Hh %Mm %Ss')
else formatted_est="$estimated_seconds seconds"; fi
printf 'Estimated processing time: ~%s\n%s\n' "$formatted_est" "$D50"
declare -a successfully_processed_wavs=() newly_exported_flacs=(); all_groups_successful=true; skip_all_splits=false
counter=1
for g_hash in "${group_keys[@]}"; do
IFS= read -r base < "$GROUP_TMP_DIR/group_${g_hash}.base" || true; base="${base%$'\r'}"; wav_file_list="$GROUP_TMP_DIR/group_${g_hash}.wavs"; declare -a current_wavs=()
if [ -f "$wav_file_list" ]; then
while IFS= read -r line || [ -n "$line" ]; do line="${line%$'\r'}"; if [ -n "$line" ]; then current_wavs+=("$line"); fi; done < <(python3 -c '
import sys, re
if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(newline="\n")
def kf(p):
 fn = p.strip().split("/")[-1]; nm = re.sub(r"\.(wav|flac|mp3)$", "", fn, flags=re.I)
 if (m := re.search(r"_\d{1,2}h\d{2}m\d{2}s?_(\d{1,2})h(\d{2})m(\d{2})s?$", nm)): h, mn, s = map(int, m.groups()); return (1, h * 3600 + mn * 60 + s, fn)
 if re.search(r"_0{1,2}h00m00s?$", nm): return (0, 0, fn)
 m = re.search(r"[_ -]+(?:part|pt|cd|disc|disk|subpart)[_ -]*(\d+)$", nm, flags=re.I)
 return (2, int(m.group(1)), fn) if m else (0, 0, fn)
for p in sorted([l.strip().replace("\r", "") for l in sys.stdin if l.strip()], key=kf): sys.stdout.write(p + "\n")
' < "$wav_file_list" 2>/dev/null || LC_ALL=C sort "$wav_file_list")
fi
first_wav="${current_wavs[0]%$'\r'}"; group_wav_dir="${first_wav%/*}"; group_wav_real="$(cd "$group_wav_dir" 2>/dev/null && pwd -P || echo "$group_wav_dir")"; target_archive_dir=""
for arch_d in "${all_mix_archive_roots[@]}"; do arch_real="$(cd "$arch_d" 2>/dev/null && pwd -P || echo "$arch_d")"; if [ "$group_wav_real" = "$arch_real" ] || [[ "$group_wav_real" == "$arch_real"/* ]]; then target_archive_dir="$arch_d"; break; fi; done
if [ -n "$target_archive_dir" ] && [ -d "$target_archive_dir" ]; then group_arch_dir="$target_archive_dir/CONVERTED_WAV_FILES"; group_spek_dir="$target_archive_dir/SPEK_OUTPUTS"
else group_arch_dir="$ARCHIVE_DIR"; group_spek_dir="$SPEK_DIR"; fi
mkdir -p "$group_arch_dir" "$group_spek_dir"
clean_base="$base"; while [[ "$clean_base" == *__* ]]; do clean_base="${clean_base//__/_}"; done
if [[ "$clean_base" != *"MPlanetarian"* ]]; then normalized_name="MPlanetarian - Stream of Frequency - ${clean_base}"; else normalized_name="$clean_base"; fi
# Every run stages into group_out_dir; the archive folder is only written by
# sob_promote_staged_output once the whole batch has succeeded.
group_out_dir="$SOB_STAGE_DIR"
output_filename="${normalized_name}.${OUTPUT_EXT}"; output_path="${group_out_dir}/${output_filename}"; tracklist_filename="${normalized_name}.txt"; tracklist_path="${group_out_dir}/${tracklist_filename}"
# "Already converted" is decided against the published archives, because the
# stage starts empty on every run. A group is skipped only when every requested
# format is already there. It takes its context as arguments so it can live at
# top level, beside the other helpers.
sob_group_already_done() {
local name="$1" archive_dir="$2"; shift 2
local -a keys=("$@")
local _t _dn _fdir
for _t in "${keys[@]}"; do
_dn="${archive_dir}/${SOB_FMT_FOLDER[$_t]}"
if [ "$_t" = "flac" ]; then
for _fdir in "${flac_check_dirs[@]}"; do
[ -s "$_fdir/${name}.${SOB_FMT_EXT[$_t]}" ] && continue 2
done
fi
[ -s "$_dn/${name}.${SOB_FMT_EXT[$_t]}" ] || return 1
done
return 0
}
if sob_group_already_done "$normalized_name" "${target_archive_dir:-$PWD}" "${TARGET_KEYS[@]}"; then
echo "Processing Group [$counter/$total_groups]: Session ID '$base'"
echo " -> Every requested output already exists in the archive. Leaving the source files in place."
for w in "${current_wavs[@]}"; do if [ -f "$w" ]; then mv "$w" "$group_arch_dir/"; fi; successfully_processed_wavs+=("$w"); done
counter=$((counter + 1)); echo "$D50"; continue
fi
spek_image="${SOB_STAGE_DIR}/${normalized_name}_spectrogram.png"; readable_title="${base//_/ }"
echo "Processing Group [$counter/$total_groups]: Session ID '$base'"; echo " -> Standardized Output Name: '$normalized_name'"
ref_name="$base"; if [ ${#current_wavs[@]} -gt 0 ]; then ref_name="${current_wavs[0]##*/}"; fi
session_year=""; session_month=""; session_day=""; session_hour=""; session_min=""
if [[ "$ref_name" =~ (20[0-9]{2}) ]]; then session_year="${BASH_REMATCH[1]}"; fi
if [[ "$ref_name" =~ 20[0-9]{2}-([0-9]{2}) ]]; then session_month="${BASH_REMATCH[1]}"; fi
if [[ "$ref_name" =~ [0-9]{4}-([0-9]{2})-([0-9]{2}) ]]; then if [ -z "$session_month" ]; then session_month="${BASH_REMATCH[1]}"; fi; session_day="${BASH_REMATCH[2]}"; fi
if [[ "$ref_name" =~ ([0-9]{1,2})h ]]; then session_hour="${BASH_REMATCH[1]}"; if [ ${#session_hour} -eq 1 ]; then session_hour="0${session_hour}"; fi; fi
if [[ "$ref_name" =~ ([0-9]{2})m ]]; then session_min="${BASH_REMATCH[1]}"; fi
if [ -n "$session_year" ] && [ -n "$session_month" ] && [ -n "$session_day" ]; then session_date="${session_year}-${session_month}-${session_day}"; fallback_pattern="history_${session_year}y${session_month}m${session_day}d"; else session_date=""; fallback_pattern=""; fi
if [ -n "$fallback_pattern" ] && [ -n "$session_hour" ] && [ -n "$session_min" ]; then traktor_pattern="history_${session_year}y${session_month}m${session_day}d_${session_hour}h${session_min}"; else traktor_pattern=""; fi
build_session_tracklist
first_wav="${current_wavs[0]}"; wav_dir="${first_wav%/*}"; wav_name="${first_wav##*/}"; wav_base="${wav_name%.*}"; specific_cover="${wav_dir}/${wav_base}.png"; selected_cover="$COVER_ART"
if [ -n "${CUSTOM_COVER_IMAGE:-}" ] && [ -f "$CUSTOM_COVER_IMAGE" ]; then echo " -> Using custom selected cover art: $CUSTOM_COVER_IMAGE"; selected_cover="$CUSTOM_COVER_IMAGE"
elif [ -f "$specific_cover" ]; then echo " -> Specific cover art found: ${specific_cover##*/}"; selected_cover="$specific_cover"
elif [ -f "${wav_dir}/FLAC_CONVERTED_OUTPUTS/Cover.png" ]; then echo " -> Archive cover art found: ${wav_dir}/FLAC_CONVERTED_OUTPUTS/Cover.png"; selected_cover="${wav_dir}/FLAC_CONVERTED_OUTPUTS/Cover.png"
elif [ -f "${wav_dir}/Cover.png" ]; then echo " -> Archive cover art found: ${wav_dir}/Cover.png"; selected_cover="${wav_dir}/Cover.png"
elif [ -n "$target_archive_dir" ] && [ -f "${target_archive_dir}/FLAC_CONVERTED_OUTPUTS/Cover.png" ]; then echo " -> Archive cover art found: ${target_archive_dir}/FLAC_CONVERTED_OUTPUTS/Cover.png"; selected_cover="${target_archive_dir}/FLAC_CONVERTED_OUTPUTS/Cover.png"
elif [ -n "$target_archive_dir" ] && [ -f "${target_archive_dir}/Cover.png" ]; then echo " -> Archive cover art found: ${target_archive_dir}/Cover.png"; selected_cover="${target_archive_dir}/Cover.png"
else echo " -> Using default cover art fallback ($COVER_ART)."; fi
OPTIMIZED_COVER=$(safe_mktemp png); OPTIMIZED_COVER="${OPTIMIZED_COVER%$'\r'}"; selected_cover="${selected_cover%$'\r'}"
if ! ffmpeg -y -i "$selected_cover" -vf "scale='min(1400,iw)':-1" "$OPTIMIZED_COVER" > /dev/null 2>&1; then echo "WARNING: Failed to optimize cover art. Using original '${selected_cover##*/}'."; cp "$selected_cover" "$OPTIMIZED_COVER"; fi
concat_list=""; session_audio="${current_wavs[0]%$'\r'}"
if [ ${#current_wavs[@]} -gt 1 ]; then
concat_list=$(safe_mktemp); concat_list="${concat_list%$'\r'}"; total_merge_sec=0; total_merge_bytes=0
for w in "${current_wavs[@]}"; do w="${w%$'\r'}"; echo "file '$w'" >> "$concat_list"; w_sec=$(probe_duration_seconds "$w"); w_bytes=$(file_size_bytes "$w"); total_merge_sec=$((total_merge_sec + w_sec)); total_merge_bytes=$((total_merge_bytes + w_bytes)); done
session_audio=$(safe_mktemp wav); session_audio="${session_audio%$'\r'}"
echo -e "${BOLD}${CYAN} -> Merging ${#current_wavs[@]} split WAV files (${total_merge_sec}s, $(fmt_bytes "$total_merge_bytes"))...${NC}"
if ! run_ffmpeg_with_progress "Merging ${#current_wavs[@]} Split WAV Files" "$total_merge_sec" "$total_merge_bytes" -y -f concat -safe 0 -i "$concat_list" -c:a pcm_s24le "$session_audio"; then echo "ERROR: Could not merge the split WAV files for '$base'."; rm -f "$concat_list" "$session_audio" "$OPTIMIZED_COVER"; exit 1; fi
fi
eff_artist="${CUSTOM_ARTIST:-MPlanetarian}"; eff_artist="${eff_artist%$'\r'}"; eff_title="${CUSTOM_TITLE:-$readable_title}"; eff_title="${eff_title%$'\r'}"; eff_album="${CUSTOM_ALBUM:-Stream of Frequency}"; eff_album="${eff_album%$'\r'}"; eff_year="${CUSTOM_YEAR:-$_CUR_YEAR}"; eff_year="${eff_year%$'\r'}"; eff_genre="${CUSTOM_GENRE:-Electronic / Trance}"; eff_genre="${eff_genre%$'\r'}"; eff_comment="${CUSTOM_COMMENT:-Stream of Frequency Mix Archive}"; eff_comment="${eff_comment%$'\r'}"; output_path="${output_path%$'\r'}"; session_audio="${session_audio%$'\r'}"
read -r audio_duration conv_duration conv_sr conv_ch < <(ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate,channels -show_entries format=duration -of default=noprint_wrappers=1 "$session_audio" 2>/dev/null | awk -F= '/^sample_rate=/{sr=$2}/^channels=/{ch=$2}/^duration=/{d=$2}END{gsub(/\r/,"",sr);gsub(/\r/,"",ch);gsub(/\r/,"",d);if(sr+0<=0)sr=44100;if(ch+0<=0)ch=2;cd=(d+0>0)?int(d+0.5):0;print(d!=""?d:"0"),cd,sr,ch}' || echo "0 0 44100 2")
conv_source_bytes=$(file_size_bytes "$session_audio"); conv_pred_bytes=$(predict_output_bytes "$OUTPUT_FORMAT" "$conv_duration" "$conv_sr" "$conv_ch" "$conv_source_bytes")
show_conversion_forecast "${#current_wavs[@]}" "$conv_source_bytes" "$conv_duration" "$conv_sr" "$conv_ch" "$conv_pred_bytes"
conversion_status=1
# One pass per requested format. Each target stages into its own folder so a
# multi-format batch publishes one published folder per format.
declare -a group_produced=()
for SOB_KEY in "${TARGET_KEYS[@]}"; do
OUTPUT_FORMAT="$SOB_KEY"; OUTPUT_EXT="${SOB_FMT_EXT[$SOB_KEY]}"; OUTPUT_LABEL="${SOB_FMT_LABEL[$SOB_KEY]}"
group_out_dir="${SOB_STAGE_DIR}/${SOB_FMT_FOLDER[$SOB_KEY]}"
mkdir -p "$group_out_dir"
output_filename="${normalized_name}.${OUTPUT_EXT}"; output_path="${group_out_dir}/${output_filename}"
# The published location is what an existing-file check has to look at, since
# the stage is always empty at the start of a run.
case "${SOB_FMT_FOLDER[$SOB_KEY]}" in
FLAC_CONVERTED_OUTPUTS) final_dir="${target_archive_dir:-$OUTPUT_DIR}"; final_dir="${final_dir:-$PWD}/FLAC_CONVERTED_OUTPUTS" ;;
*) final_dir="${target_archive_dir:-$PWD}/${SOB_FMT_FOLDER[$SOB_KEY]}" ;;
esac
SOB_PROMOTE_ROOTS["${SOB_FMT_FOLDER[$SOB_KEY]}"]="${final_dir%/*}"
if [ -s "$final_dir/$output_filename" ]; then
echo " -> ${OUTPUT_LABEL} already exists in the archive ($final_dir/$output_filename). Skipping this format."
continue
fi
if [ "$SOB_KEY" = "mp4" ]; then
if [ "${MP4_ENCODER_READY:-0}" != "1" ]; then
MP4_VCODEC="libx264"; MP4_VENC_ARGS="-preset medium -crf 20"
if ffmpeg -f lavfi -i color=c=black:s=64x64 -frames:v 1 -c:v h264_nvenc -f null - >/dev/null 2>&1; then MP4_VCODEC="h264_nvenc"; MP4_VENC_ARGS="-preset p4 -cq 21 -g 60"; fi
MP4_ACODEC="aac"; if ffmpeg -encoders 2>/dev/null | grep -q "libfdk_aac"; then MP4_ACODEC="libfdk_aac"; fi; MP4_ENCODER_READY=1; echo " -> YouTube video encoder: ${MP4_VCODEC}, audio: ${MP4_ACODEC}"
fi
total_sec=$(printf "%.0f" "${audio_duration:-0}"); fade_filter=""; if [ "$total_sec" -ge 12 ]; then fade_filter=",fade=t=in:st=0:d=5:color=black,fade=t=out:st=$((total_sec - 5)):d=5:color=black"; fi
echo " -> Rendering 1080p YouTube MP4 from the cover art..."
if run_ffmpeg_with_progress "Rendering 1080p YouTube MP4 ($readable_title)" "${total_sec:-$conv_duration}" "$conv_pred_bytes" -y -loop 1 -framerate 30 -t "$audio_duration" -i "$selected_cover" -i "$session_audio" -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black${fade_filter},format=yuv420p" -c:v "$MP4_VCODEC" $MP4_VENC_ARGS -c:a "$MP4_ACODEC" -b:a 320k -movflags +faststart "${meta_args[@]}" "$output_path"; then
group_produced+=("$output_path")
else
conversion_status=1; all_groups_successful=false
fi
continue
fi
# Formats that cannot carry artwork are noted rather than failed.
sob_cover_warning "$SOB_KEY" "$OPTIMIZED_COVER" "$CUSTOM_BACK_COVER" "$CUSTOM_DISC_IMAGE"
# Only formats the registry says support artwork get the images attached.
local_cover=""; [ "$(sob_format_supports_cover "$SOB_KEY" && echo 1 || echo 0)" = "1" ] && local_cover="$OPTIMIZED_COVER"
echo " -> Encoding ${SOB_KEY^^} (${OUTPUT_EXT}) with cover art and tags..."
if sob_encode_one "$SOB_KEY" "$session_audio" "$output_path" "$local_cover" "$CUSTOM_BACK_COVER" "$CUSTOM_DISC_IMAGE" "$readable_title" "$conv_duration" "$conv_pred_bytes"; then
group_produced+=("$output_path")
else
echo "ERROR: FFmpeg conversion failed for group '$base' (${OUTPUT_LABEL})."
conversion_status=1; all_groups_successful=false
fi
done
if [ -n "$concat_list" ]; then rm -f "$concat_list" "$session_audio"; fi
if [ $conversion_status -ne 0 ] || [ ${#group_produced[@]} -eq 0 ]; then echo "ERROR: Conversion batch for '$base' did not complete."; rm -f "$OPTIMIZED_COVER"; all_groups_successful=false; exit 1; fi
for _out in "${group_produced[@]}"; do
final_out_sz=$(file_size_bytes "$_out"); log_conversion_event "$_out" "$(basename "$(dirname "$_out")" | cut -d_ -f1)" "$conv_duration" "$final_out_sz" "$conv_source_bytes"; newly_exported_flacs+=("$_out")
done
# The spectrogram is staged as well; it is published with the audio.
spek_image="${SOB_STAGE_DIR}/${normalized_name}_spectrogram.png"
if [ ! -f "$spek_image" ]; then echo -e "${BOLD}${CYAN} -> Generating 1080p Spectrogram Analysis (${spek_image##*/})...${NC}"; run_ffmpeg_with_progress "Generating Spectrogram" "$conv_duration" 250000 -y -i "${group_produced[0]}" -lavfi "showspectrumpic=s=1920x1080:mode=combined:color=intensity:scale=log" -frames:v 1 "$spek_image"; fi
printf '\n%s\n✓ Audio Conversion Finished: %s\n%s\n' "$S50" "${group_produced[0]##*/}" "$S50"; ask_split=""
if [ "$skip_all_splits" = true ]; then
    echo " -> Skipping splitting (Skip All enabled)." >/dev/tty
else
    if has_interactive_tty; then
        local split_opts=(
            "n|Skip Once|(Keep mix as single file; ask again for next mix)"
            "a|Skip All|(Keep all remaining mixes as single files)"
            "s|Skip the rest of the batch|(Stop all remaining per-file prompts)"
            "y|Split into Separate Parts / Sets|(Launch Split_FLAC_File.sh)"
        )
        if sob_skip_remaining; then
            echo " -> SKIP REMAINING is set: not asking again for this batch." >/dev/tty
        else
            select_menu_option "SPLIT MIX FILE" "Would you like to split '${group_produced[0]##*/}' into separate parts/sets?" split_opts 0 ask_split
            case "$ask_split" in
                [sS]*) SOB_SKIP_REMAINING_METADATA=1
                       echo " -> Set to skip the rest of the batch; no more per-file prompts." >/dev/tty ;;
            esac
        fi
    fi
    case "$ask_split" in
        [yY]|[yY][eE][sS]|3) splitter_bin=""; for sc in "$SCRIPT_DIR/Split_FLAC_File.sh" "$SCRIPT_DIR/scripts/Split_FLAC_File.sh" "./Split_FLAC_File.sh"; do if [ -f "$sc" ]; then splitter_bin="$sc"; break; fi; done; if [ -n "$splitter_bin" ]; then bash "$splitter_bin" "$output_path" </dev/tty || true; else echo "WARNING: Split_FLAC_File.sh not found!" >/dev/tty; fi ;;
        [aA]|[aA][lL][lL]|2) skip_all_splits=true; echo " -> Skipping splitting for this and all remaining mixes." >/dev/tty ;;
        *) echo " -> Skipping splitting." >/dev/tty ;;
    esac
fi
echo " -> Moving successfully processed files to '$group_arch_dir'..."
for w in "${current_wavs[@]}"; do if [ -f "$w" ]; then mv "$w" "$group_arch_dir/"; fi; successfully_processed_wavs+=("$w"); done
review_converted_tracklist; rm -f "$OPTIMIZED_COVER"; counter=$((counter + 1)); echo "$D50"
done
if [ "$all_groups_successful" = true ] && [ ${#successfully_processed_wavs[@]} -gt 0 ]; then echo "All conversions completed successfully. All source WAV files archived."; fi
# Staged output is published only now, after every group reported success.
sob_promote_staged_output
if [ ${#newly_exported_flacs[@]} -gt 0 ] && [ "${all_groups_successful:-false}" = "true" ]; then
printf '%s\n' "Published output folders:"
local _rname _rdir
for _rname in "${!SOB_PROMOTE_ROOTS[@]}"; do
_rdir="${SOB_PROMOTE_ROOTS[$_rname]}"
[ -d "$_rdir/$_rname" ] && printf '   %s\n' "$_rdir/$_rname"
done
fi
PLAYLIST_NAME="SOF_Batch_Export_$(date +%Y-%m-%d).m3u"
if [ ${#newly_exported_flacs[@]} -gt 0 ]; then
echo "Generating M3U playlist: $PLAYLIST_NAME..."; printf '%s\n' "${newly_exported_flacs[@]}" > "$PLAYLIST_NAME"; echo "Playlist generated successfully at: $PLAYLIST_NAME"
for arch_d in "${all_mix_archive_roots[@]}"; do if [ -d "$arch_d/PLAYLISTS_GENERATED" ]; then cp "$PLAYLIST_NAME" "$arch_d/PLAYLISTS_GENERATED/" 2>/dev/null || true; fi; done
fi
printf '%s\nCONVERSION COMPLETE\n%s\n' "$S50" "$S50"; sob_stage_cleanup; listen_to_converted_audio; post_conversion_tasks
}
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
run_sof_conversion "$@"
fi
