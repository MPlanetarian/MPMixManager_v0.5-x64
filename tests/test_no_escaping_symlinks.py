#!/usr/bin/env python3
"""No tracked file may be a symlink that points outside the repository.

Two scripts used to be committed as symlinks into a developer's private
~/MP_REPORTER installation:

    scripts/mp_daily_email_reporter.py -> ~/MP_REPORTER/mp_daily_email_reporter.py
    scripts/mp_email_manager.sh        -> ~/MP_REPORTER/mp_email_manager.sh

A fresh clone therefore received dangling links: importing the module raised
FileNotFoundError off this workstation, and the Windows portability fixes made
to the reporter could never be committed because the file git recorded was only
the link text. They are now real files.

This test fails if any tracked symlink resolves outside the checkout again.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files that were symlinks and must stay real files.
FORMER_SYMLINKS = (
    ROOT / "scripts" / "mp_daily_email_reporter.py",
    ROOT / "scripts" / "mp_email_manager.sh",
)


def _tracked_symlink_targets() -> dict[str, str]:
    """Tracked symlinks and the text git stores as their target."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-s"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if out.returncode != 0:
        return {}
    links: dict[str, str] = {}
    for line in (out.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        meta, path = parts
        fields = meta.split()
        if len(fields) >= 3 and fields[0] == "120000":
            links[path] = fields[1]
    return links


class TestNoEscapingSymlinks(unittest.TestCase):
    def test_former_symlinks_are_real_files_in_the_working_tree(self):
        for path in FORMER_SYMLINKS:
            with self.subTest(path=path.name):
                self.assertTrue(path.exists(), f"{path} is missing")
                self.assertFalse(
                    path.is_symlink(),
                    f"{path.name} is a symlink again; a fresh clone would get "
                    f"a dangling link instead of the file",
                )

    def test_former_symlinks_are_regular_files_in_git(self):
        """The index must record mode 100644/100755, never 120000."""
        try:
            out = subprocess.run(
                ["git", "ls-files", "-s", "--"] + [str(p.relative_to(ROOT)) for p in FORMER_SYMLINKS],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git is not available")
        if out.returncode != 0:
            self.skipTest("not a git checkout")
        for line in (out.stdout or "").splitlines():
            mode = line.split()[0]
            self.assertNotEqual(
                mode, "120000",
                f"git still records a symlink: {line.split(chr(9))[-1]}",
            )

    def test_no_tracked_symlink_escapes_the_checkout(self):
        offenders = []
        for path, _blob in _tracked_symlink_targets().items():
            full = ROOT / path
            if not full.is_symlink():
                continue
            target = os.readlink(full)
            if os.path.isabs(target):
                offenders.append(f"{path} -> {target}")
                continue
            resolved = (full.parent / target).resolve()
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                offenders.append(f"{path} -> {target}")
        self.assertEqual(
            offenders, [],
            "these tracked symlinks point outside the repo, so a clone gets "
            "dangling links: " + ", ".join(offenders),
        )

    def test_the_reporter_imports_without_a_private_reporter_install(self):
        """It must import from the app tree alone, with no ~/MP_REPORTER."""
        import subprocess
        import sys
        import tempfile

        env = dict(os.environ)
        env["HOME"] = tempfile.mkdtemp()
        env.pop("MP_REPORTER_DIR", None)
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'scripts'); "
             "import mp_daily_email_reporter as m; print(m.BASE_DIR)"],
            cwd=ROOT, capture_output=True, text=True, timeout=180, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertIn(str(ROOT), result.stdout,
                      "without ~/MP_REPORTER the reporter must use the app tree")


class TestReporterKeepsExistingInstallation(unittest.TestCase):
    """The shared-installation layout must keep working exactly as before.

    ~/MP_REPORTER holds the SMTP config, the "one email per day" state and the
    generated reports. Before this change the file was a symlink into that
    directory, so BASE_DIR meant it. Now the file lives in the repo, so
    _resolve_base_dir() has to choose it explicitly or the daily limit resets
    and 20 sent reports are forgotten.
    """

    def test_base_dir_prefers_an_existing_home_reporter(self):
        source = (ROOT / "scripts" / "mp_daily_email_reporter.py").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("_resolve_base_dir", source)
        body = source.split("def _resolve_base_dir", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("MP_REPORTER_DIR", body)
        self.assertIn("email_state.json", body)
        self.assertIn("email_config.env", body)

    def test_the_directory_override_wins(self):
        import subprocess
        import sys
        import tempfile

        target = tempfile.mkdtemp()
        env = dict(os.environ)
        env["MP_REPORTER_DIR"] = target
        env["HOME"] = tempfile.mkdtemp()
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'scripts'); "
             "import mp_daily_email_reporter as m; print(m.BASE_DIR)"],
            cwd=ROOT, capture_output=True, text=True, timeout=180, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        # macOS resolves /var/folders/... to /private/var/folders/..., and
        # _resolve_base_dir() calls .resolve(), so compare resolved paths.
        self.assertEqual(Path(result.stdout.strip()).resolve(), Path(target).resolve())


if __name__ == "__main__":
    unittest.main()
