#!/usr/bin/env bash
# ==============================================================================
# MP_Mix_Manager_v0.5 - Studio & DJ Booth Mobile Web Companion Wrapper
# ==============================================================================
# Runs the zero-dependency companion server (mix_web_companion.py) and can
# register it as a login/boot service:
#
#   Linux   -> systemd --user unit  (mp-web-companion.service)
#   macOS   -> launchd LaunchAgent   (com.mplanetarian.mp-web-companion)
#
# Usage:
#   mix_web_companion.sh start|stop|restart|status
#   mix_web_companion.sh install     create the service, enable it, start it
#   mix_web_companion.sh uninstall   stop and remove the service
#   mix_web_companion.sh enable      enable autostart (creating the unit if needed)
#   mix_web_companion.sh disable     disable autostart
#   mix_web_companion.sh logs        show the service log
#   mix_web_companion.sh open        open the dashboard in a browser
#   mix_web_companion.sh <args...>   run the Python server directly
# ==============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
[ "$(basename "$SCRIPT_DIR")" = "scripts" ] && APP_DIR="$(dirname "$SCRIPT_DIR")"

PY_SCRIPT="$SCRIPT_DIR/mix_web_companion.py"
[ ! -f "$PY_SCRIPT" ] && PY_SCRIPT="$APP_DIR/scripts/mix_web_companion.py"

# Source config if available (app root first, then the script directory)
if [ -f "$APP_DIR/config.env" ]; then
    # shellcheck disable=SC1091
    source "$APP_DIR/config.env"
elif [ -f "$SCRIPT_DIR/config.env" ]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/config.env"
fi

HOST="${WEB_COMPANION_HOST:-0.0.0.0}"
PORT="${WEB_COMPANION_PORT:-8888}"
SERVICE_NAME="mp-web-companion.service"
LAUNCHD_LABEL="com.mplanetarian.mp-web-companion"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/$LAUNCHD_LABEL.plist"
LOG_DIR="$HOME/.config/mix-manager"
LOG_FILE="$LOG_DIR/mp-web-companion.log"
UNIT_PATH="$HOME/.config/systemd/user/$SERVICE_NAME"

# Where the macOS app bundle lives. /Applications is the folder Finder's
# sidebar calls "Applications" and the list System Settings shows, so the app
# is where the user actually looks for it. ~/Applications is only a fallback
# for a non-admin account where /Applications is not writable. Only the install
# tree is baked into the bundle (as APP_DIR), so the bundle can sit anywhere.
pick_mac_bundle() {
    local dir
    for dir in /Applications "$HOME/Applications"; do
        [ -d "$dir" ] || continue
        if [ -w "$dir" ]; then
            printf '%s\n' "$dir/MP Web Companion.app"
            return 0
        fi
    done
    printf '%s\n' "$HOME/Applications/MP Web Companion.app"
}
MAC_BUNDLE="$(pick_mac_bundle)"
# Older builds kept the bundle inside the install tree, then briefly in
# ~/Applications. install and uninstall remove those so macOS never resolves
# two copies of the same bundle id.
LEGACY_MAC_BUNDLES="$APP_DIR/MP Web Companion.app
$HOME/Applications/MP Web Companion.app"

is_macos() { [ "$(uname -s 2>/dev/null)" = "Darwin" ]; }

# systemd --user is only useful when this is a real login session with a bus.
have_user_systemd() {
    command -v systemctl >/dev/null 2>&1 || return 1
    systemctl --user show-environment >/dev/null 2>&1 || return 1
}

# Pick an interpreter that can actually run the server. macOS has no "python3"
# in a non-interactive launchd PATH, so the absolute system interpreter is used.
resolve_python() {
    local cand
    for cand in "${MP_PYTHON:-}" \
                "$APP_DIR/scripts/venv/bin/python3" \
                "$APP_DIR/.venv/bin/python3" \
                "$(command -v python3 2>/dev/null)" \
                /usr/bin/python3 \
                /opt/homebrew/bin/python3 \
                /usr/local/bin/python3 \
                "$(command -v python 2>/dev/null)"; do
        [ -n "$cand" ] && [ -x "$cand" ] || continue
        if "$cand" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >/dev/null 2>&1; then
            printf '%s\n' "$cand"
            return 0
        fi
    done
    return 1
}

# libpython for the interpreter picked above. The app bundle embeds the
# interpreter instead of exec'ing it, which needs the matching dylib.
resolve_python_lib() {
    [ -n "${PYTHON_BIN:-}" ] || return 1
    "$PYTHON_BIN" - <<'PY'
import os, sys, sysconfig

# sys.base_prefix is where the running interpreter really lives; the build-time
# LIBDIR can point at an Xcode that is not installed (it does on this Mac).
version = "%d.%d" % (sys.version_info.major, sys.version_info.minor)
cands = [os.path.join(sys.base_prefix, "lib", "libpython%s.dylib" % version)]

# Framework builds (all macOS CPythons) also ship the interpreter binary one
# level up in the framework directory, which is already a loadable dylib.
framework = sysconfig.get_config_var("PYTHONFRAMEWORK")
if framework:
    cands.append(os.path.join(sys.base_prefix, framework))

for cand in cands:
    if os.path.exists(cand):
        print(os.path.realpath(cand))
        break
PY
}


PYTHON_BIN="$(resolve_python || true)"
PYTHON_LIB="$(resolve_python_lib || true)"

get_lan_ip() {
    "${PYTHON_BIN:-python3}" -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('10.255.255.255', 1)); print(s.getsockname()[0]); s.close()" 2>/dev/null || echo "127.0.0.1"
}

get_pids() {
    # The bundle launcher embeds Python now, so its command line only shows the
    # app binary, not mix_web_companion.py. Match both names or `stop` leaves
    # the server running while launchd reports it as stopped.
    {
        pgrep -f "mix_web_companion.py" 2>/dev/null || true
        pgrep -f "MP Web Companion.app/Contents/MacOS/mp-web-companion" 2>/dev/null || true
    } | sort -u | tr '\n' ' '
}

service_active() {
    if is_macos; then
        if [ -f "$LAUNCHD_PLIST" ] && command -v launchctl >/dev/null 2>&1; then
            launchctl print "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1 && return 0
        fi
        [ -n "$(get_pids)" ]
        return
    fi
    if have_user_systemd && systemctl --user is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        return 0
    fi
    [ -n "$(get_pids)" ]
}

# ------------------------------------------------------------------------------
# Service definition
# ------------------------------------------------------------------------------
write_systemd_unit() {
    [ -n "$PYTHON_BIN" ] || { echo "✗ No usable Python 3.8+ interpreter found."; return 1; }
    mkdir -p "$(dirname "$UNIT_PATH")"

    # The archive lives on removable/external disks. Requiring their mount units
    # here is what makes the service come up with a populated library at boot
    # instead of starting before /mnt/1TB_BKP_005 exists and streaming nothing.
    local mount_units="" path candidate mount_unit
    local probe_dir
    probe_dir="$(sed -n 's/^MIX_ARCHIVE_DIR="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$APP_DIR/config.env" 2>/dev/null | head -1)"
    for path in "$probe_dir" \
                $(sed -n 's/^EXTRA_MIX_ARCHIVE_DIRS="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$APP_DIR/config.env" 2>/dev/null | tr ';,' '  '); do
        [ -n "$path" ] || continue
        # Walk up to the real mountpoint: the unit row names /mnt/1TB_BKP_005,
        # not the MIX_ARCHIVE folder inside it.
        candidate="$path"
        while [ -n "$candidate" ] && [ "$candidate" != "/" ]; do
            mount_unit="$(systemctl list-units --type=mount --all --no-legend --plain 2>/dev/null \
                | awk -v p="$candidate" '$0 ~ p {print $1; exit}')"
            if [ -z "$mount_unit" ]; then
                mount_unit="$(systemctl --user list-units --type=mount --all --no-legend --plain 2>/dev/null \
                    | awk -v p="$candidate" '$0 ~ p {print $1; exit}')"
            fi
            [ -n "$mount_unit" ] && break
            candidate="$(dirname "$candidate")"
        done
        if [ -n "$mount_unit" ]; then
            case " $mount_units " in
                *" $mount_unit "*) ;;
                *) mount_units="$mount_units $mount_unit" ;;
            esac
        fi
    done

    cat >"$UNIT_PATH" <<EOF
[Unit]
Description=MP Mix Manager Mobile Web Companion (DJ Booth Remote)
Documentation=file://$APP_DIR/README.md
After=network-online.target$mount_units
Wants=network-online.target$mount_units
# Soft ordering only: if an archive drive is unplugged the dashboard still
# answers and the Music library keeps working, instead of the unit refusing
# to start until the disk is attached.

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart="$PYTHON_BIN" "$PY_SCRIPT" --host "$HOST" --port "$PORT"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
    echo "✓ Wrote systemd user unit: $UNIT_PATH"
}

# Compile the app-bundle entry point so the bundle, not the interpreter, is the
# process macOS judges for Full Disk Access. A `#!/bin/bash ... exec python`
# shim is replaced by exec and loses the bundle identity, which is why a granted
# "MP Web Companion.app" could still be denied the archive volume. macOS CPython
# is itself Python.app, so even a compiled exec launcher hands the identity
# over; the launcher embeds libpython (Py_BytesMain) instead and only falls back
# to exec when the dylib cannot be loaded. If clang is missing the shell shim is
# kept, because a working (if harder to approve) server beats a bundle that
# will not start.
compile_bundle_launcher() {
    local bundle="$1" target="$2"
    local src="$target.c"
    local log="$LOG_DIR/mp-web-companion-build.log"

    if ! command -v clang >/dev/null 2>&1; then
        echo "  ⚠ clang not found; keeping the shell entry point."
        echo "    (install Xcode Command Line Tools for the 1-grant bundle)"
        return 0
    fi

    # Paths are baked in: this file is generated, never user-edited.
    cat >"$src" <<EOF
#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>

/* Keep the app bundle resident as the parent of Python so macOS TCC
 * attributes the archive read to the bundle that holds the FDA grant. */
static volatile sig_atomic_t child_pid = 0;

/* launchd stops a job with SIGTERM. Without forwarding it, Python is orphaned
 * and keeps port 8888 bound, so the "restart" appears to succeed while the old
 * server is still the one answering. */
static void forward_signal(int sig) {
    if (child_pid > 0) kill((pid_t)child_pid, sig);
}

static void install_forwarding(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = forward_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);
}

int main(void) {
    /* LaunchServices starts an app with "/" as its working directory and
     * ignores launchd's StandardOutPath, so the bundle takes both back here.
     * Without this, logging to mp-web-companion.log stops when the job is
     * started with `open`. */
    if (chdir("$APP_DIR") != 0) { perror("chdir"); }
    (void)freopen("$LOG_FILE", "a", stdout);
    (void)freopen("$LOG_FILE", "a", stderr);
    setenv("PYTHONUNBUFFERED", "1", 1);

    /* Run Python inside this process instead of exec'ing it. Every CPython on
     * macOS is itself Python.app, a bundle with its own code identity; exec
     * replaces the process image, so macOS judged the archive read as
     * "Python" and MP Web Companion never appeared in Privacy & Security.
     * Embedding keeps the bundle, and therefore the privacy grant, attached
     * to this process for its whole lifetime. */
    void *libpython = dlopen("$PYTHON_LIB", RTLD_NOW | RTLD_GLOBAL);
    if (libpython) {
        int (*run_python)(int, char **) =
            (int (*)(int, char **))dlsym(libpython, "Py_BytesMain");
        if (run_python) {
            char *py_argv[] = {
                (char *)"$PYTHON_BIN", (char *)"-u",
                (char *)"$PY_SCRIPT",
                (char *)"--host", (char *)"$HOST",
                (char *)"--port", (char *)"$PORT",
                NULL
            };
            int rc = run_python(7, py_argv);
            fflush(NULL);
            return rc;
        }
        fprintf(stderr, "mp-web-companion: Py_BytesMain missing from %s\n",
                "$PYTHON_LIB");
    } else {
        fprintf(stderr, "mp-web-companion: dlopen(%s) failed: %s\n",
                "$PYTHON_LIB", dlerror());
    }

    /* Fallback: exec the interpreter. Still serves, but macOS then judges the
     * archive read as Python.app, so a bundle grant will not apply. */
    install_forwarding();

    /* Block the forwardable signals across the fork so a SIGTERM cannot land
     * before child_pid is set and be dropped on the floor. */
    sigset_t blocked, previous;
    sigemptyset(&blocked);
    sigaddset(&blocked, SIGTERM);
    sigaddset(&blocked, SIGINT);
    sigaddset(&blocked, SIGHUP);
    sigprocmask(SIG_BLOCK, &blocked, &previous);

    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return 1; }
    if (pid == 0) {
        /* The child gets the default dispositions back, so the Python server
         * handles its own shutdown. */
        signal(SIGTERM, SIG_DFL);
        signal(SIGINT, SIG_DFL);
        signal(SIGHUP, SIG_DFL);
        sigprocmask(SIG_SETMASK, &previous, NULL);
        execl("$PYTHON_BIN", "$PYTHON_BIN", "$PY_SCRIPT",
              "--host", "$HOST", "--port", "$PORT", (char *)NULL);
        perror("execl");
        _exit(127);
    }

    child_pid = (sig_atomic_t)pid;
    sigprocmask(SIG_SETMASK, &previous, NULL);

    int status = 0;
    for (;;) {
        if (waitpid(pid, &status, 0) >= 0) break;
        if (errno != EINTR) { perror("waitpid"); return 1; }
    }
    child_pid = 0;
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return 0;
}
EOF

    if clang -O2 -Wall -Wextra -o "$target" "$src" >>"$log" 2>&1; then
        rm -f "$src"
        # Ad-hoc sign so the identity is stable across rebuilds; TCC keys the
        # grant to the bundle id, and a stable signature keeps that valid.
        codesign --force --sign - --identifier "com.mplanetarian.mp-web-companion" \
            "$bundle" >/dev/null 2>&1 || true
        echo "  ✓ Native bundle launcher built (single Full Disk Access grant)."
    else
        echo "  ⚠ Could not compile the bundle launcher; keeping the shell entry point."
        echo "    See $log"
    fi
}

build_bundle_icon() {
    local png="$1" dest="$2"
    [ -f "$png" ] || return 1
    command -v sips >/dev/null 2>&1 || return 1
    command -v iconutil >/dev/null 2>&1 || return 1
    local tmp
    tmp="$(mktemp -d "${TMPDIR:-/tmp}/mp-icon.XXXXXX")" || return 1
    local iconset="$tmp/icon.iconset"
    mkdir -p "$iconset"
    local pair size name
    for pair in "16:icon_16x16.png" "32:icon_16x16@2x.png" "32:icon_32x32.png" \
                "64:icon_32x32@2x.png" "128:icon_128x128.png" "256:icon_128x128@2x.png" \
                "256:icon_256x256.png" "512:icon_256x256@2x.png" "512:icon_512x512.png" \
                "1024:icon_512x512@2x.png"; do
        size="${pair%%:*}"
        name="${pair#*:}"
        sips -z "$size" "$size" "$png" --out "$iconset/$name" >/dev/null 2>&1 || {
            rm -rf "$tmp"; return 1;
        }
    done
    local rc=1
    if iconutil -c icns "$iconset" -o "$dest" >/dev/null 2>&1 && [ -s "$dest" ]; then
        rc=0
    fi
    rm -rf "$tmp"
    return $rc
}

# Remove a bundle and its LaunchServices record, so a rebuilt app is the only
# thing macOS resolves for the identifier.
remove_bundle() {
    local bundle="$1"
    [ -n "$bundle" ] || return 0
    local lsregister="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
    if [ -x "$lsregister" ] && [ -d "$bundle" ]; then
        "$lsregister" -u "$bundle" >/dev/null 2>&1 || true
    fi
    rm -rf "$bundle"
}

# Tell Launch Services to index the new bundle as an application. Without this
# the bundle can be missing from the Full Disk Access list until the next login.
register_bundle() {
    local bundle="$1"
    [ -d "$bundle" ] || return 0
    local lsregister="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
    if [ -x "$lsregister" ]; then
        "$lsregister" -f "$bundle" >/dev/null 2>&1 || true
    fi
}

remove_legacy_bundles() {
    local keep="$1" path
    # Split on newlines without word-splitting the paths, which contain spaces.
    while IFS= read -r path; do
        [ -n "$path" ] || continue
        [ "$path" = "$keep" ] && continue
        remove_bundle "$path"
    done <<EOF
$LEGACY_MAC_BUNDLES
EOF
}

write_launchd_plist() {
    [ -n "$PYTHON_BIN" ] || { echo "✗ No usable Python 3.8+ interpreter found."; return 1; }
    mkdir -p "$(dirname "$LAUNCHD_PLIST")" "$LOG_DIR"

    # macOS TCC (Privacy & Security) judges a background job by the bundle that
    # owns it. A bare python3 launchd job has no bundle, so it is denied read
    # access to /Volumes until someone grants Full Disk Access by hand. Running
    # it from a minimal .app gives the job a stable bundle identity that can be
    # approved once in System Settings -> Privacy & Security -> Full Disk Access.
    local bundle="$MAC_BUNDLE"
    local macos_dir="$bundle/Contents/MacOS"
    local version
    version="$(cat "$APP_DIR/VERSION" 2>/dev/null | awk '{print $1}')"
    [ -n "$version" ] || version="0.5.0"
    mkdir -p "$macos_dir" "$bundle/Contents/Resources"

    # An icon is not cosmetic here: an app without one is drawn as a blank
    # generic document in the Finder window the `grant` command opens, and a
    # bundle missing CFBundleVersion is treated as a lesser app by the Settings
    # picker. Both make it easy to pass over (or to click the wrong row).
    local icns="$bundle/Contents/Resources/mp_web_companion.icns"
    if [ -f "$APP_DIR/assets/mp_liquid_glass_icon.png" ] && command -v iconutil >/dev/null 2>&1; then
        build_bundle_icon "$APP_DIR/assets/mp_liquid_glass_icon.png" "$icns" || true
    fi
    local icon_xml=""
    [ -s "$icns" ] && icon_xml="	<key>CFBundleIconFile</key>
	<string>mp_web_companion</string>"

    cat >"$bundle/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleName</key>
	<string>MP Web Companion</string>
	<key>CFBundleDisplayName</key>
	<string>MP Web Companion</string>
	<key>CFBundleIdentifier</key>
	<string>com.mplanetarian.mp-web-companion</string>
	<key>CFBundleExecutable</key>
	<string>mp-web-companion</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>CFBundleShortVersionString</key>
	<string>$version</string>
	<key>CFBundleVersion</key>
	<string>$version</string>
	<key>LSMinimumSystemVersion</key>
	<string>11.0</string>
	<key>NSHighResolutionCapable</key>
	<true/>
	<key>LSUIElement</key>
	<true/>
$icon_xml
	<key>NSRemovableVolumesUsageDescription</key>
	<string>MP Web Companion streams your mixes from the archive drive plugged into this Mac.</string>
	<key>NSNetworkVolumesUsageDescription</key>
	<string>MP Web Companion streams your mixes from the network archive.</string>
</dict>
</plist>
EOF
    # A bundle edited in place keeps its old LaunchServices record, and macOS
    # then still resolves the identifier to the previous copy. Drop the stale
    # copies and re-register the new one before the service is restarted.
    remove_legacy_bundles "$bundle"
    cat >"$macos_dir/mp-web-companion" <<EOF
#!/bin/bash
# Bundle entry point: launchd starts this, TCC attributes access to the bundle.
exec "$PYTHON_BIN" "$PY_SCRIPT" --host "$HOST" --port "$PORT"
EOF
    chmod +x "$macos_dir/mp-web-companion"

    # A shell script entry point does not survive `exec`: the process image is
    # replaced by the Python binary, so macOS TCC attributes the archive read to
    # the interpreter (com.apple.python3) and a Full Disk Access grant on
    # "MP Web Companion.app" never applies. Compile a tiny launcher instead and
    # keep it resident as the parent of Python; the bundle then stays the
    # responsible process, which is what the privacy grant is recorded against.
    compile_bundle_launcher "$bundle" "$macos_dir/mp-web-companion"
    # Register after the launcher exists and is signed, so Finder draws the icon
    # and Settings can list the app immediately.
    register_bundle "$bundle"

    cat >"$LAUNCHD_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LAUNCHD_LABEL</string>
	<!-- Launch through LaunchServices, not by executing the bundle binary.
	     A launchd job that execs its own binary has no LaunchServices record,
	     so macOS treats it as a background helper: it silently denies access
	     to /Volumes and never lists the app in Privacy & Security. `open`
	     gives the bundle a normal app identity, which is what makes macOS
	     show the "access files on a removable volume" prompt. -W keeps open
	     alive as long as the app, so KeepAlive still works. -->
	<key>ProgramArguments</key>
	<array>
		<string>/usr/bin/open</string>
		<string>-g</string>
		<string>-W</string>
		<string>$bundle</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$APP_DIR</string>
	<key>RunAtLoad</key>
	<true/>
	<!-- KeepAlive must not depend on the exit status: the job runs `open -W`,
	     whose status says nothing about the app, so a crash would otherwise
	     leave the companion stopped for good. -->
	<key>KeepAlive</key>
	<true/>
	<key>ProcessType</key>
	<string>Interactive</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
		<key>PYTHONUNBUFFERED</key>
		<string>1</string>
	</dict>
	<key>StandardOutPath</key>
	<string>$LOG_FILE</string>
	<key>StandardErrorPath</key>
	<string>$LOG_FILE</string>
</dict>
</plist>
EOF
    chmod 644 "$LAUNCHD_PLIST"
    # Remember which tree this bundle runs. The bundle lives in /Applications,
    # which is shared by every install of MP Mix Manager, so a bundle built
    # from an older (or test) tree would otherwise keep serving that tree even
    # after a newer install replaces it.
    printf '%s\n' "$APP_DIR" >"$bundle/Contents/Resources/install_dir" 2>/dev/null || true
    echo "✓ Wrote launchd agent: $LAUNCHD_PLIST"
    echo "  (app bundle: $bundle)"
}

# True when $MAC_BUNDLE was built from a different install tree, or from none.
bundle_matches_app_dir() {
    local bundle="$1" recorded=""
    [ -d "$bundle" ] || return 1
    recorded="$(cat "$bundle/Contents/Resources/install_dir" 2>/dev/null || true)"
    [ -n "$recorded" ] || return 1
    [ "$(cd "$recorded" 2>/dev/null && pwd -P)" = "$(cd "$APP_DIR" 2>/dev/null && pwd -P)" ]
}
write_service() {
    if is_macos; then
        write_launchd_plist
    else
        write_systemd_unit
    fi
}

# ------------------------------------------------------------------------------
# Start / stop
# ------------------------------------------------------------------------------
start_service() {
    echo "Starting Mobile Web Companion..."
    if is_macos; then
        # The bundle and the launchd agent are shared across installs, so
        # rebuild them when they still point at a different tree. Without this
        # `start` silently served whichever install created them first.
        if [ -f "$LAUNCHD_PLIST" ] && ! bundle_matches_app_dir "$MAC_BUNDLE"; then
            echo "  (rebuilding the companion bundle for this install: $APP_DIR)"
            # bootout alone can leave the old app alive (open -W keeps it
            # running), and kickstart would then just re-attach to it. Stop the
            # job *and* the process before writing the new bundle.
            launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1 || true
            local stale_pids
            stale_pids="$(get_pids)"
            if [ -n "$stale_pids" ]; then
                # shellcheck disable=SC2086
                kill $stale_pids 2>/dev/null || true
                local _waited=0
                while [ -n "$(get_pids)" ] && [ "$_waited" -lt 40 ]; do
                    sleep 0.25
                    _waited=$((_waited + 1))
                done
            fi
            write_launchd_plist >/dev/null 2>&1 || true
        fi
        if [ -f "$LAUNCHD_PLIST" ]; then
            launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1 || true
            if launchctl bootstrap "gui/$(id -u)" "$LAUNCHD_PLIST" >/dev/null 2>&1; then
                launchctl kickstart "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1 || true
                echo "✓ Mobile Web Companion started via launchd ($LAUNCHD_LABEL)."
            else
                echo "⚠ launchd start failed; attempting direct background launch..."
            fi
        fi
        if ! service_active && [ -n "$PYTHON_BIN" ]; then
            mkdir -p "$LOG_DIR"
            nohup "$PYTHON_BIN" "$PY_SCRIPT" --host "$HOST" --port "$PORT" >>"$LOG_FILE" 2>&1 &
            sleep 1
        fi
    else
        if have_user_systemd && [ -f "$UNIT_PATH" ]; then
            systemctl --user daemon-reload
            systemctl --user start "$SERVICE_NAME"
            sleep 1
            if systemctl --user is-active --quiet "$SERVICE_NAME"; then
                echo "✓ Mobile Web Companion started via systemd service ($SERVICE_NAME)."
            elif [ -n "$PYTHON_BIN" ]; then
                echo "⚠ systemd start failed; attempting direct background launch..."
                mkdir -p "$LOG_DIR"
                nohup "$PYTHON_BIN" "$PY_SCRIPT" --host "$HOST" --port "$PORT" >>"$LOG_FILE" 2>&1 &
                sleep 1
            fi
        elif [ -n "$PYTHON_BIN" ]; then
            mkdir -p "$LOG_DIR"
            nohup "$PYTHON_BIN" "$PY_SCRIPT" --host "$HOST" --port "$PORT" >>"$LOG_FILE" 2>&1 &
            sleep 1
        fi
    fi
    show_status
}

stop_service() {
    echo "Stopping Mobile Web Companion..."
    if is_macos; then
        if [ -f "$LAUNCHD_PLIST" ]; then
            launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1 || true
        fi
    elif have_user_systemd && [ -f "$UNIT_PATH" ]; then
        systemctl --user stop "$SERVICE_NAME" >/dev/null 2>&1 || true
    fi
    local pids
    pids="$(get_pids)"
    if [ -n "$pids" ]; then
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true
        # Wait for the port to be released; a fixed sleep let "restart" race the
        # old process and bind-fail, which looked like a service that would not
        # start.
        local waited=0
        while [ -n "$(get_pids)" ] && [ "$waited" -lt 50 ]; do
            sleep 0.1
            waited=$((waited + 1))
        done
        pids="$(get_pids)"
        if [ -n "$pids" ]; then
            # shellcheck disable=SC2086
            kill -9 $pids 2>/dev/null || true
            sleep 0.3
        fi
    fi
    echo "✓ Mobile Web Companion stopped."
}

show_status() {
    local lan_ip pids
    lan_ip="$(get_lan_ip)"
    pids="$(get_pids)"
    if service_active; then
        echo "● Mobile Web Companion is RUNNING"
        [ -n "$pids" ] && echo "  • PID(s):         $pids"
        echo "  • Local Address:  http://localhost:${PORT}"
        echo "  • Mobile LAN URL: http://${lan_ip}:${PORT}"
        if is_macos; then
            echo "  • Autostart:      $(launchd_enabled)"
        else
            echo "  • Autostart:      $(systemd_enabled)"
        fi
    else
        echo "○ Mobile Web Companion is STOPPED"
        echo "  • Target Mobile LAN URL: http://${lan_ip}:${PORT}"
        if is_macos; then
            echo "  • Autostart:      $(launchd_enabled)"
        else
            echo "  • Autostart:      $(systemd_enabled)"
        fi
    fi
    # A shared /Applications bundle can outlive the tree it was built from.
    # Say so plainly instead of letting the user wonder why an edit did nothing.
    if is_macos && [ -d "$MAC_BUNDLE" ] && ! bundle_matches_app_dir "$MAC_BUNDLE"; then
        local stale_tree
        stale_tree="$(cat "$MAC_BUNDLE/Contents/Resources/install_dir" 2>/dev/null || echo unknown)"
        echo ""
        echo "⚠ The installed companion app runs a different copy of MP Mix Manager:"
        echo "    app built from: $stale_tree"
        echo "    this tree:      $APP_DIR"
        echo "  Run '$(basename "$0") restart' to rebuild it for this install."
    fi
    macos_privacy_warning
}

# ------------------------------------------------------------------------------
# Autostart
# ------------------------------------------------------------------------------
systemd_enabled() {
    if [ -f "$UNIT_PATH" ] && have_user_systemd; then
        systemctl --user is-enabled "$SERVICE_NAME" 2>/dev/null || echo "disabled"
    elif [ -f "$UNIT_PATH" ]; then
        echo "unit present (no user bus)"
    else
        echo "not installed"
    fi
}

launchd_enabled() {
    if [ -f "$LAUNCHD_PLIST" ]; then
        echo "enabled (RunAtLoad)"
    else
        echo "not installed"
    fi
}

# macOS TCC denies a background job access to external volumes until the job's
# bundle is approved in System Settings > Privacy & Security > Full Disk Access.
# Surface that instead of reporting a healthy server that streams nothing.
#
# The probe must ask the *service*, not this shell: Terminal usually holds its
# own Full Disk Access grant, so a local os.listdir() succeeds while the launchd
# job is still blocked. That is exactly how a "green" status hides a phone with
# no audio.
macos_privacy_warning() {
    is_macos || return 0
    [ -n "${PYTHON_BIN:-}" ] || return 0
    local probe_dir served
    probe_dir="$(sed -n 's/^MIX_ARCHIVE_DIR="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$APP_DIR/config.env" 2>/dev/null | head -1)"
    [ -n "$probe_dir" ] && [ -d "$probe_dir" ] || return 0

    # What the running companion reports to the phones is authoritative.
    served="$("$PYTHON_BIN" - "$PORT" <<'PY' 2>/dev/null || true
import json, sys, urllib.request
port = sys.argv[1] if len(sys.argv) > 1 else "8888"
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3) as r:
        data = json.load(r)
except Exception:
    sys.exit(0)
if data.get("active_file"):
    sys.exit(0)
detail = str(data.get("library_detail") or "")
if "Full Disk Access" in detail or "blocked" in detail.lower():
    print(detail)
PY
)"
    if [ -n "$served" ]; then
        echo "  ⚠ macOS privacy block: the companion cannot read its archive"
        echo "    $served"
        echo "    Add 'MP Web Companion.app' under System Settings > Privacy &"
        echo "    Security > Full Disk Access (or Files and Folders > Removable"
        echo "    Volumes), then run: restart"
        echo "    (this wrapper can open that pane: mix_web_companion.sh grant)"
        return 0
    fi

    "$PYTHON_BIN" - "$probe_dir" <<'PY' || true
import os, sys
path = sys.argv[1]
try:
    os.listdir(path)
except PermissionError:
    print("  ⚠ macOS privacy block: this background service cannot read")
    print("    " + path)
    print("    Add 'MP Web Companion.app' in System Settings > Privacy &")
    print("    Security > Full Disk Access (or Files and Folders >")
    print("    Removable Volumes), then run: restart")
    print("    If the app is already listed, approve this interpreter too:")
    print("    " + sys.executable)
    print("    (only needed when the bundle fell back to the shell launcher)")
    print("    (this wrapper can open that pane: mix_web_companion.sh grant)")
sys.exit(0)
PY
}

enable_autostart() {
    if is_macos; then
        [ -f "$LAUNCHD_PLIST" ] || write_launchd_plist || return 1
        launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1 || true
        if launchctl bootstrap "gui/$(id -u)" "$LAUNCHD_PLIST" >/dev/null 2>&1; then
            echo "✓ Autostart enabled for $LAUNCHD_LABEL (runs at login)."
        else
            echo "⚠ launchd bootstrap failed; the plist will still load at next login."
        fi
        return 0
    fi

    [ -f "$UNIT_PATH" ] || write_systemd_unit || return 1
    if have_user_systemd; then
        systemctl --user daemon-reload
        systemctl --user enable "$SERVICE_NAME"
        # A user unit only starts at boot when lingering is enabled for the user.
        if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
            loginctl enable-linger "$USER" >/dev/null 2>&1 \
                && echo "✓ Enabled lingering so the service starts at boot before login." \
                || echo "⚠ Run 'sudo loginctl enable-linger $USER' so it starts at boot before login."
        fi
        echo "✓ Autostart enabled for $SERVICE_NAME."
    else
        echo "⚠ No systemd user bus in this session. The unit is written and will"
        echo "  activate at next login: systemctl --user enable --now $SERVICE_NAME"
    fi
}

disable_autostart() {
    if is_macos; then
        launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1 || true
        rm -f "$LAUNCHD_PLIST"
        echo "✓ Autostart disabled and agent removed."
        return 0
    fi
    if have_user_systemd; then
        systemctl --user disable "$SERVICE_NAME" >/dev/null 2>&1 || true
    fi
    echo "✓ Autostart disabled for $SERVICE_NAME."
}

install_service() {
    write_service || return 1
    enable_autostart
    start_service
}

uninstall_service() {
    stop_service
    disable_autostart
    rm -f "$UNIT_PATH" "$LAUNCHD_PLIST"
    if is_macos; then
        remove_bundle "$MAC_BUNDLE"
        remove_legacy_bundles "$MAC_BUNDLE"
        echo "  (removed the app bundle and its LaunchServices record)"
    fi
    if have_user_systemd; then
        systemctl --user daemon-reload >/dev/null 2>&1 || true
    fi
    echo "✓ Service files removed."
}

case "${1:-}" in
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    restart)
        stop_service
        sleep 1
        start_service
        ;;
    status)
        show_status
        ;;
    grant|privacy)
        # macOS only: take the user straight to the pane where the companion
        # bundle is approved, then restart so the grant takes effect.
        if ! is_macos; then
            echo "This command is only needed on macOS."
            exit 0
        fi
        if [ -d "$MAC_BUNDLE" ]; then
            # macOS never prompts for this: the companion is a background job,
            # so the volume is denied silently and the app is missing from the
            # list until it is added by hand. The file picker only accepts
            # .app bundles (a .sh or .py row is greyed out on purpose), which
            # is why the app is kept in ~/Applications beside MP Gui and why
            # this reveals it in the Finder rather than a path to type.
            if command -v pbcopy >/dev/null 2>&1; then
                printf '%s' "$MAC_BUNDLE" | pbcopy 2>/dev/null || true
            fi
            open -R "$MAC_BUNDLE" 2>/dev/null || true
            open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null \
                || open "/System/Applications/System Settings.app" 2>/dev/null || true
            echo "Add this app to Full Disk Access, then run: $0 restart"
            echo "  $MAC_BUNDLE"
            echo "  (path copied to the clipboard)"
            echo ""
            echo "In the Settings pane: click +, press Cmd+Shift+G, Cmd+V, Return,"
            echo "then select 'MP Web Companion' and click Open. Dragging the app"
            echo "from the Finder window this command just opened works too."
            echo "If the window is showing the scripts folder, everything there is"
            echo "greyed out by design: only .app bundles can be added."
            echo ""
            echo "The app is in the Applications folder (/Applications), the one"
            echo "Finder's sidebar opens -- not your user ~/Applications folder."
            echo ""
            echo "A USB archive can instead be allowed under Privacy & Security >"
            echo "Files and Folders > Removable Volumes; Full Disk Access covers it too."
        else
            echo "Run '$0 install' first to create the app bundle."
        fi
        ;;
    install|enable)
        if [ "${1:-}" = "install" ]; then
            install_service
        else
            enable_autostart
            service_active || start_service
        fi
        ;;
    disable)
        disable_autostart
        ;;
    uninstall)
        uninstall_service
        ;;
    logs)
        [ -f "$LOG_FILE" ] && tail -n "${2:-40}" "$LOG_FILE" || echo "No log at $LOG_FILE"
        ;;
    open|browse)
        LAN_IP="$(get_lan_ip)"
        target="http://${LAN_IP}:${PORT}"
        echo "Opening $target in default browser..."
        if command -v xdg-open >/dev/null 2>&1; then
            xdg-open "$target" >/dev/null 2>&1 &
        elif command -v open >/dev/null 2>&1; then
            open "$target" >/dev/null 2>&1 &
        fi
        ;;
    *)
        exec "${PYTHON_BIN:-python3}" "$PY_SCRIPT" "$@"
        ;;
esac
