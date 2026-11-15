"""A successful convert_audio_format.sh encode writes a spectrogram into SPEK_OUTPUTS."""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.mp_test_bash import BASH

BASE = Path(__file__).resolve().parent.parent
SCRIPT = BASE / "convert_audio_format.sh"


def _stub_ffmpeg(path: Path, log: Path, fail: bool) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {str(log)!r}\n"
        + ("exit 1\n" if fail else
           "out=\"${@: -1}\"\n"
           "mkdir -p \"$(dirname \"$out\")\"\n"
           "printf 'PNG' > \"$out\"\n")
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class TestConvertAudioSpek(unittest.TestCase):
    def test_both_success_paths_generate_a_spek_before_the_split_prompt(self):
        text = SCRIPT.read_text(encoding="utf-8")
        paired = (
            'generate_conversion_spek "$out_file"\n        prompt_split_audio "$out_file"',
            'generate_conversion_spek "$out_file"\n            prompt_split_audio "$out_file"',
        )
        self.assertEqual(sum(text.count(needle) for needle in paired), 2)

    def test_spectrogram_lands_in_the_archive_spek_folder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bindir = root / "bin"
            bindir.mkdir()
            log = root / "ffmpeg.log"
            _stub_ffmpeg(bindir / "ffmpeg", log, fail=False)
            archive = root / "archive"
            flac = archive / "FLAC_CONVERTED_OUTPUTS" / "Mix Name.flac"
            opus = archive / "CONVERTED_AUDIO_OUTPUTS" / "OPUS_160K" / "Other.opus"
            loose = root / "loose"
            flac.parent.mkdir(parents=True)
            opus.parent.mkdir(parents=True)
            loose.mkdir()
            flac.write_bytes(b"flac")
            opus.write_bytes(b"opus")
            (loose / "tone.wav").write_bytes(b"wav")
            script = r"""
set -euo pipefail
export PATH="$STUB_BIN:$PATH"
# shellcheck disable=SC1090
source "$CONVERTER"
generate_conversion_spek "$FLAC"
[[ -s "$FLAC_PNG" ]]
before=$(wc -l < "$FFLOG")
generate_conversion_spek "$FLAC"
after=$(wc -l < "$FFLOG")
[[ "$before" == "$after" ]]
grep -q "showspectrumpic=s=1920x1080" "$FFLOG"
grep -q "Mix Name_spectrogram.png" "$FFLOG"
generate_conversion_spek "$OPUS"
[[ -s "$OPUS_PNG" ]]
all_mix_archive_roots+=("$LOOSE")
generate_conversion_spek "$LOOSE/tone.wav"
[[ -s "$LOOSE/SPEK_OUTPUTS/tone_spectrogram.png" ]]
"""
            env = os.environ.copy()
            env.update({
                "STUB_BIN": str(bindir),
                "CONVERTER": str(SCRIPT),
                "FLAC": str(flac),
                "FLAC_PNG": str(archive / "SPEK_OUTPUTS" / "Mix Name_spectrogram.png"),
                "OPUS": str(opus),
                "OPUS_PNG": str(archive / "SPEK_OUTPUTS" / "Other_spectrogram.png"),
                "LOOSE": str(loose),
                "FFLOG": str(log),
            })
            proc = subprocess.run(
                [BASH, "-c", script],
                cwd=str(BASE),
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)

    def test_spek_failure_does_not_fail_the_conversion_step(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bindir = root / "bin"
            bindir.mkdir()
            log = root / "ffmpeg.log"
            _stub_ffmpeg(bindir / "ffmpeg", log, fail=True)
            archive = root / "archive"
            flac = archive / "FLAC_CONVERTED_OUTPUTS" / "Mix.flac"
            flac.parent.mkdir(parents=True)
            flac.write_bytes(b"flac")
            png = archive / "SPEK_OUTPUTS" / "Mix_spectrogram.png"
            script = r"""
set -euo pipefail
export PATH="$STUB_BIN:$PATH"
# shellcheck disable=SC1090
source "$CONVERTER"
generate_conversion_spek "$FLAC"
[[ ! -e "$PNG" ]]
echo SURVIVED
"""
            env = os.environ.copy()
            env.update({
                "STUB_BIN": str(bindir),
                "CONVERTER": str(SCRIPT),
                "FLAC": str(flac),
                "PNG": str(png),
            })
            proc = subprocess.run(
                [BASH, "-c", script],
                cwd=str(BASE),
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
            self.assertIn("SURVIVED", proc.stdout)
            self.assertIn("spectrogram was not written", proc.stdout)
            self.assertFalse(png.exists())
