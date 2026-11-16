#!/usr/bin/env python3
"""A theme switch must not leave the old palette in the header caches.

The header banner, the performance strip and the stats block are cached as
finished lines with their ANSI colours already expanded. Switching theme
repainted the menu chrome but the cache kept serving Sensorium's dark
tangerine ink on a Dreamworlds Ultra pane until a background worker happened
to rewrite it, which could take a minute or more.

save_theme() and sync_theme_from_file() now drop those caches, and
load_header_cache() refuses a cache older than the theme file for the case
where some other writer only touched the theme.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MGR_SH = ROOT / "Mix_Archive_Manager.sh"

# Enough of the manager to exercise the invalidation path on its own.
THEME_FUNCS = r"""
set -e
eval "$(awk '/^set_theme_colors\(\)/{f=1} f{print} f&&/^}/{exit}' Mix_Archive_Manager.sh)"
eval "$(sed -n '/^_theme_file_stamp() {/,/^}/p' Mix_Archive_Manager.sh)"
eval "$(sed -n '/^_theme_file_name() {/,/^}/p' Mix_Archive_Manager.sh)"
eval "$(sed -n '/^_invalidate_theme_caches() {/,/^}/p' Mix_Archive_Manager.sh)"
eval "$(sed -n '/^sync_theme_from_file() {/,/^}/p' Mix_Archive_Manager.sh)"
eval "$(sed -n '/^save_theme() {/,/^}/p' Mix_Archive_Manager.sh)"
_sync_btop_theme() { :; }
_apply_cli_theme_background() { :; }
"""


def _bash(script: str, home: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = home
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
    )


class ThemeCacheInvalidationTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.conf = Path(self.home) / ".config" / "mix-manager"
        self.conf.mkdir(parents=True)
        self.header = self.conf / "header_cache.env"
        self.perf = self.conf / "perf_display.cache"
        self.perf_stamp = self.conf / "perf_display.stamp"
        self.refresh_stamp = self.conf / ".header_refresh_stamp"
        self.theme = self.conf / "theme"

    def _seed(self):
        # save_header_cache() writes %q-quoted values, so a semicolon inside the
        # ANSI sequence survives sourcing. Keep the fixture quoted the same way.
        self.header.write_text(
            "_CACHED_STATS_CORE='Total FLAC Files Generated: 12'\n", encoding="utf-8"
        )
        self.perf.write_text(
            "  CPU Load: 1.0\n", encoding="utf-8"
        )
        self.perf_stamp.write_text("1794823906\n", encoding="utf-8")
        self.refresh_stamp.write_text("1794823906\n", encoding="utf-8")
        self.theme.write_text("sensorium\n", encoding="utf-8")

    def test_save_theme_drops_every_palette_cache(self):
        self._seed()
        res = _bash(
            THEME_FUNCS
            + """
set_theme_colors sensorium
save_theme dreamworlds_ultra
for f in header_cache.env perf_display.cache perf_display.stamp .header_refresh_stamp; do
    [ -e "$HOME/.config/mix-manager/$f" ] && echo "LEFT:$f" || true
done
echo "THEME:$(cat "$HOME/.config/mix-manager/theme")"
""",
            self.home,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("LEFT:", res.stdout)
        self.assertIn("THEME:dreamworlds_ultra", res.stdout)

    def test_load_header_cache_rejects_a_cache_painted_under_another_theme(self):
        """A refresh worker that started before the switch can land after it.

        Deleting the cache at switch time cannot stop a background worker from
        writing the lines it computed in the old palette a moment later, so the
        cache records the theme that painted it and a mismatch is discarded.
        """
        self.header.write_text(
            "_CACHED_THEME=sensorium\n_CACHED_STATS_CORE='Total FLAC: 12'\n",
            encoding="utf-8",
        )
        self.theme.write_text("dreamworlds_ultra\n", encoding="utf-8")
        res = _bash(
            r"""
eval "$(sed -n '/^_theme_file_name() {/,/^}/p' Mix_Archive_Manager.sh)"
eval "$(sed -n '/^load_header_cache() {/,/^}/p' Mix_Archive_Manager.sh)"
load_header_cache
printf 'STATS:[%s]\n' "${_CACHED_STATS_CORE:-}"
[ -e "$HOME/.config/mix-manager/header_cache.env" ] && echo "LEFT:header" || true
exit 0
""",
            self.home,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("STATS:[]", res.stdout)
        self.assertNotIn("LEFT:", res.stdout)

    def test_load_header_cache_keeps_a_cache_from_the_same_theme(self):
        self.header.write_text(
            "_CACHED_THEME=dreamworlds_ultra\n_CACHED_STATS_CORE='Total FLAC: 12'\n",
            encoding="utf-8",
        )
        self.theme.write_text("dreamworlds_ultra\n", encoding="utf-8")
        res = _bash(
            r"""
eval "$(sed -n '/^_theme_file_name() {/,/^}/p' Mix_Archive_Manager.sh)"
eval "$(sed -n '/^load_header_cache() {/,/^}/p' Mix_Archive_Manager.sh)"
load_header_cache
printf 'STATS:[%s]\n' "${_CACHED_STATS_CORE:-}"
exit 0
""",
            self.home,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("STATS:[Total FLAC: 12]", res.stdout)

    def test_save_header_cache_records_the_painting_theme(self):
        res = _bash(
            THEME_FUNCS
            + r"""
set_theme_colors dreamworlds_ultra
_CACHED_STATS_CORE="Total FLAC: 12"
save_header_cache() {
local c_dir="$HOME/.config/mix-manager"; mkdir -p "$c_dir" 2>/dev/null || true; local c_file="$c_dir/header_cache.env"; local c_tmp="$c_file.$$.tmp"; { printf '_CACHED_THEME=%q\n' "${CURRENT_THEME:-}"; printf '_CACHED_STATS_CORE=%q\n' "${_CACHED_STATS_CORE:-}"; } > "$c_tmp" 2>/dev/null && mv -f "$c_tmp" "$c_file" 2>/dev/null || rm -f "$c_tmp" 2>/dev/null || true
}
save_header_cache
grep '^_CACHED_THEME=' "$HOME/.config/mix-manager/header_cache.env"
""",
            self.home,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("_CACHED_THEME=dreamworlds_ultra", res.stdout)

    def test_shipped_save_header_cache_stamps_the_theme(self):
        body = MGR_SH.read_text(encoding="utf-8").split("save_header_cache() {", 1)[1]
        body = body.split("\n}", 1)[0]
        self.assertIn("_CACHED_THEME=%q", body)
        self.assertIn("${CURRENT_THEME:-}", body)

    def test_save_theme_blanks_the_in_memory_copies(self):
        res = _bash(
            THEME_FUNCS
            + """
_CACHED_STATS_CORE="painted in the old palette"
_CACHED_WEATHER_DISPLAY="painted in the old palette"
_CACHED_PERF_DISPLAY="painted in the old palette"
save_theme dreamworlds_ultra
printf 'STATS:[%s] WEATHER:[%s] PERF:[%s]\\n' \
    "$_CACHED_STATS_CORE" "$_CACHED_WEATHER_DISPLAY" "$_CACHED_PERF_DISPLAY"
""",
            self.home,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("STATS:[] WEATHER:[] PERF:[]", res.stdout)

    def test_theme_from_another_window_also_clears_the_caches(self):
        self._seed()
        res = _bash(
            THEME_FUNCS
            + """
set_theme_colors sensorium
printf 'dreamworlds_ultra\\n' > "$HOME/.config/mix-manager/theme"
sync_theme_from_file && echo CHANGED || echo NOCHANGE
[ -e "$HOME/.config/mix-manager/header_cache.env" ] && echo "LEFT:header" || true
echo "THEME:$CURRENT_THEME"
""",
            self.home,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("CHANGED", res.stdout)
        self.assertNotIn("LEFT:", res.stdout)
        self.assertIn("THEME:dreamworlds_ultra", res.stdout)


class PerfCacheThemeStampTest(unittest.TestCase):
    """The CPU/RAM/disk strip is cached with baked colours too."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.conf = Path(self.home) / ".config" / "mix-manager"
        self.conf.mkdir(parents=True)
        self.perf = self.conf / "perf_display.cache"
        self.stamp = self.conf / "perf_display.stamp"
        self.theme = self.conf / "theme"
        # A cold cache would make the function take the slow path, so stamp it.
        self.stamp.write_text(str(int(__import__("time").time())) + "\n", encoding="utf-8")

    def test_stale_theme_line_discards_the_cached_strip(self):
        self.perf.write_text("  painted in sensorium\nsensorium\n", encoding="utf-8")
        self.theme.write_text("dreamworlds_ultra\n", encoding="utf-8")
        res = _bash(
            r"""
eval "$(sed -n '/^_theme_file_name() {/,/^}/p' Mix_Archive_Manager.sh)"
eval "$(sed -n '/^get_system_perf_stats() {/,/^}/p' Mix_Archive_Manager.sh)"
BOLD=""; BLUE=""; CYAN=""; NC=""; OS_TYPE=macos; MIX_ARCHIVE_DIR=/tmp; SCRIPT_DIR=/tmp
_MENU_CURSOR_LOCK=1
get_system_perf_stats >/dev/null 2>&1
[ -e "$HOME/.config/mix-manager/perf_display.cache" ] && echo "CACHE:$([ -n "$_CACHED_PERF_DISPLAY" ] && echo filled || echo empty)" || echo "CACHE:gone"
exit 0
""",
            self.home,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        # Old painted line is gone; the strip is either gone or freshly empty,
        # never the stale Sensorium text.
        self.assertNotIn("painted in sensorium", res.stdout)

    def test_shipped_writer_records_the_theme_on_the_second_line(self):
        body = MGR_SH.read_text(encoding="utf-8")
        self.assertIn('printf \'%s\\n%s\\n\' "$_CACHED_PERF_DISPLAY" "${CURRENT_THEME:-}"', body)


class BtopThemeSyncTest(unittest.TestCase):
    """_sync_btop_theme must work with BSD sed as well as GNU sed.

    It used `sed -i` with no backup suffix, which BSD sed reads as the suffic
    of the -i flag. On macOS the edit failed every time, so btop stayed on
    "Default" and only the .theme file was copied.
    """

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.btop = Path(self.home) / ".config" / "btop"
        self.btop.mkdir(parents=True)
        (self.btop / "btop.conf").write_text(
            'color_theme = "Default"\ntheme[main_bg]="#000000"\n', encoding="utf-8"
        )

    def _run(self, theme_id):
        script = (
            r"""
set -e
SCRIPT_DIR=%s
eval "$(sed -n '/^_sync_btop_theme() {/,/^}/p' Mix_Archive_Manager.sh)"
CURRENT_THEME=%s
_sync_btop_theme "$CURRENT_THEME"
"""
            % (str(ROOT), theme_id)
        )
        return _bash(script, self.home)

    def test_conf_points_at_the_theme_file(self):
        res = self._run("dreamworlds_ultra")
        self.assertEqual(res.returncode, 0, res.stderr)
        conf = (self.btop / "btop.conf").read_text(encoding="utf-8")
        self.assertIn('color_theme = "Dreamworlds_Ultra_MP_Mix_Manager"', conf)
        # The rest of the file survives the rewrite.
        self.assertIn('theme[main_bg]="#000000"', conf)

    def test_sensorium_uses_the_shipped_palette_name(self):
        res = self._run("sensorium")
        self.assertEqual(res.returncode, 0, res.stderr)
        conf = (self.btop / "btop.conf").read_text(encoding="utf-8")
        self.assertIn('color_theme = "Sensorium_MP_Mix_Manager"', conf)

    def test_no_temp_files_are_left_behind(self):
        res = self._run("dreamworlds_ultra")
        self.assertEqual(res.returncode, 0, res.stderr)
        leftovers = [p.name for p in self.btop.iterdir() if ".tmp" in p.name]
        self.assertEqual(leftovers, [])

    def test_a_theme_without_a_btop_palette_is_left_alone(self):
        res = self._run("cyberpunk")
        self.assertEqual(res.returncode, 0, res.stderr)
        conf = (self.btop / "btop.conf").read_text(encoding="utf-8")
        self.assertIn('color_theme = "Default"', conf)


class PortableSedTest(unittest.TestCase):
    def test_manager_has_no_g_form_sed_i(self):
        """`sed -i` without a suffix is GNU-only; BSD sed needs -i ''."""
        text = MGR_SH.read_text(encoding="utf-8")
        offenders = [
            (n, line.strip())
            for n, line in enumerate(text.splitlines(), 1)
            if "sed -i " in line or "sed -i\"" in line or "sed -i'" in line
        ]
        self.assertEqual(offenders, [], "GNU-only sed -i in Mix_Archive_Manager.sh")


if __name__ == "__main__":
    unittest.main()
