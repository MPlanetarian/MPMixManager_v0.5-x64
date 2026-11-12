"""The manager's option 4 menu and the converter's output-directory rule.

Two promises are checked here:

  * An AUDIO_CONVERTED_OUTPUTS folder must only appear after a run that
    finished. A cancelled or failed batch leaves no empty directory, because a
    half-made folder makes the archive look converted when it is not.
  * The menu offers every format the registry advertises, and the header shows
    the format count.
"""

import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import audio_conversion_suite as suite

MGR = ROOT / "Mix_Archive_Manager.sh"
SOF = ROOT / "Make_SOF_FLAC_CONVERSION.sh"


def _read(path):
    return path.read_text(encoding="utf-8", errors="ignore")


class OutputDirectoryRuleTests(unittest.TestCase):
    """The staging design is what makes 'only on full completion' true."""

    @classmethod
    def setUpClass(cls):
        cls.sof = _read(SOF)

    def test_output_dir_is_not_created_up_front(self):
        # The old script ran: mkdir -p "$OUTPUT_DIR" "$ARCHIVE_DIR" "$SPEK_DIR"
        self.assertNotIn('mkdir -p "$OUTPUT_DIR"', self.sof)

    def test_conversions_are_staged_first(self):
        self.assertIn("SOB_STAGE_ROOT", self.sof)
        self.assertIn("group_out_dir=\"$SOB_STAGE_DIR\"", self.sof)

    def test_promotion_is_gated_on_success(self):
        body = self.sof[self.sof.index("sob_promote_staged_output() {"):]
        body = body[:body.index("\n}\n")]
        self.assertIn('all_groups_successful', body)
        self.assertIn("return 0", body)
        # The mkdir that publishes a folder comes after the success check.
        guard = body.index('all_groups_successful:-false}')
        make_dir = body.index('mkdir -p "$final"')
        self.assertGreater(make_dir, guard)

    def test_promotion_is_called_at_the_end(self):
        self.assertIn("sob_promote_staged_output\n", self.sof)

    def test_promotion_moves_format_subdirectories(self):
        body = self.sof[self.sof.index("sob_promote_staged_output() {"):]
        body = body[:body.index("\n}\n")]
        self.assertIn('[ -d "$item" ]', body,
                      "multi-format runs stage one directory per format")

    def test_multi_format_publishes_one_folder_per_format(self):
        self.assertIn('${SOB_KEY^^}_CONVERTED_OUTPUTS', self.sof)

    def test_spectrogram_goes_to_staging(self):
        self.assertIn('spek_image="${SOB_STAGE_DIR}/', self.sof)


class ManagerMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mgr = _read(MGR)

    def test_menu_lists_every_registry_format(self):
        body = self.mgr[self.mgr.index("manage_mix_conversion() {"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("mgr_suite choices", body,
                      "the format rows must come from the shared registry")

    def test_menu_offers_a_batch_all_formats_mode(self):
        body = self.mgr[self.mgr.index("manage_mix_conversion() {"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("SOF_OUTPUT_FORMAT=\"any\"", body)

    def test_menu_opens_converted_folders(self):
        self.assertIn("manage_converted_audio_folders()", self.mgr)
        body = self.mgr[self.mgr.index("manage_mix_conversion() {"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("manage_converted_audio_folders", body)

    def test_menu_shows_statistics(self):
        body = self.mgr[self.mgr.index("manage_mix_conversion() {"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("mgr_conversion_stats", body)

    def test_header_shows_the_format_count(self):
        self.assertIn("Audio Output Formats Available:", self.mgr)
        self.assertIn("mgr_format_count", self.mgr)

    def test_bridge_functions_exist(self):
        for name in ("_mgr_suite_py", "mgr_suite", "mgr_format_count",
                     "mgr_conversion_stats", "mgr_converted_folders_table",
                     "mgr_converted_folder_rows", "mgr_show_formats_table",
                     "manage_converted_audio_folders"):
            self.assertIn(f"{name}() {{", self.mgr, name)

    def test_format_count_falls_back_to_the_registry_size(self):
        self.assertIn("_MGR_FORMAT_COUNT=21", self.mgr)


class ConverterMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sof = _read(SOF)

    def test_converter_shows_the_format_count_status_line(self):
        self.assertIn("sob_status_line", self.sof)
        self.assertIn("Audio Output Formats Available", self.sof)

    def test_converter_shows_previous_stats_before_converting(self):
        body = self.sof[self.sof.index("run_sof_conversion() {"):]
        self.assertIn("sob_show_previous_stats", body)
        # ...and it happens before the first encode.
        self.assertLess(body.index("sob_show_previous_stats"),
                        body.index('Encoding ${SOB_KEY^^}'))

    def test_converter_supports_the_batch_all_mode(self):
        self.assertIn('OUTPUT_FORMAT" = "any"', self.sof)

    def test_multiple_targets_are_resolved_from_the_registry(self):
        self.assertIn("sob_format_choices | cut -d'|' -f1", self.sof)

    def test_metadata_menu_offers_presets_and_custom_fields(self):
        self.assertIn("sob_manage_presets", self.sof)
        self.assertIn("Add a Custom Metadata Field", self.sof)

    def test_metadata_menu_supports_back_cover_and_disc_image(self):
        self.assertIn("BACK Cover", self.sof)
        self.assertIn("DISC Image", self.sof)

    def test_metadata_menu_supports_disc_sets(self):
        self.assertIn("Disc number for this set", self.sof)

    def test_per_file_prompt_has_a_skip_the_rest_escape(self):
        self.assertIn("SOB_SKIP_REMAINING_METADATA", self.sof)
        self.assertIn("skip the rest of the batch", self.sof)

    def test_staging_is_not_used_when_covers_cannot_embed(self):
        # A format without cover support must warn rather than fail the encode.
        self.assertIn("cannot store embedded artwork", self.sof)


class RegistryConsistencyTests(unittest.TestCase):
    def test_converter_covers_every_player_readable_extension(self):
        from audio_formats import AUDIO_EXTENSION_SET
        missing = set(suite.READABLE_EXTENSIONS) - set(AUDIO_EXTENSION_SET)
        # A few extra source-only types (.dsf/.ape/.tak) are allowed; what must
        # not happen is the converter refusing a type the player can play.
        unexpected = {e for e in missing if e not in (".dsf", ".dff", ".ape", ".tak",
                                                      ".mpc", ".mpp")}
        self.assertEqual(unexpected, set())

    def test_unwritable_targets_are_refused_with_a_reason(self):
        for ext in (".ape", ".tak", ".dsf", ".dff", ".mpc"):
            self.assertTrue(suite.unwritable_reason(ext), ext)

    def test_every_output_can_be_reached_from_a_wav(self):
        """The common case: one WAV into every advertised format."""
        refused = [f.key for f in suite.OUTPUT_FORMATS
                   if not suite.plan_conversion("mix.wav", f.key).allowed]
        self.assertEqual(refused, [])


if __name__ == "__main__":
    unittest.main()
